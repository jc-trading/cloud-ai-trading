"""Risk management Celery tasks - P3 Phase 3B."""

import logging
from decimal import Decimal
from datetime import datetime, timezone

from celery import shared_task

from app.config import get_settings
from app.celery_database import CeleryAsyncSessionLocal
from app.modules.market_data.service import MarketDataService
from app.modules.risk.tracker import PortfolioRiskTracker
from app.modules.risk.engine import RiskEngine
from app.modules.trading.models import Position
from app.modules.watchlist.models import Watchlist

logger = logging.getLogger(__name__)


def _starting_capital() -> Decimal:
    """Configured simulated starting capital (SIMULATE_BALANCE, default $10000).

    This is the real, single source of the account's base equity for the paper /
    simulated portfolio — replaces the old hardcoded placeholder equity. Current
    portfolio net worth = this + open-position market value change, computed inside
    PortfolioRiskTracker.calculate_portfolio_metrics.
    """
    return Decimal(str(get_settings().DEFAULT_SIMULATE_BALANCE))


@shared_task(name="risk.monitor_portfolio")
def monitor_portfolio():
    """
    Monitor all portfolios and update risk metrics.

    Runs every 1 minute via Celery Beat.

    Operations:
    - Update position metrics (P&L, MFE, MAE)
    - Calculate portfolio metrics
    - Record drawdown snapshots
    - Check portfolio limits
    - Send alerts if limits exceeded
    """
    import asyncio

    async def _monitor():
        async with CeleryAsyncSessionLocal() as session:
            from sqlalchemy import select

            # Get all watchlists
            stmt = select(Watchlist)
            result = await session.execute(stmt)
            watchlists = result.scalars().all()

            for watchlist in watchlists:
                try:
                    # Get risk limits
                    from app.modules.risk.models import RiskLimit

                    stmt = select(RiskLimit).where(RiskLimit.watchlist_id == watchlist.id)
                    result = await session.execute(stmt)
                    risk_limit = result.scalar_one_or_none()

                    if not risk_limit or not risk_limit.enabled:
                        continue

                    # Get open positions
                    stmt = select(Position).where(
                        Position.watchlist_id == watchlist.id,
                        Position.status == "open",
                    )
                    result = await session.execute(stmt)
                    open_positions = result.scalars().all()

                    # Guardrail: no open positions -> nothing to price, no metrics to
                    # write. Skip cleanly so we never emit placeholder rows or alerts.
                    if not open_positions:
                        continue

                    # Real current prices: latest stored OHLCV close per symbol.
                    symbols = [p.symbol for p in open_positions]
                    current_prices = await MarketDataService.get_latest_prices(
                        session, watchlist.id, symbols
                    )

                    # Only price positions we actually have a market price for. A
                    # missing price means the collector has no candle yet — skip that
                    # position rather than fabricate a value, so metrics stay truthful.
                    priced_positions = [p for p in open_positions if p.symbol in current_prices]
                    missing = {p.symbol for p in open_positions} - set(current_prices)
                    if missing:
                        logger.warning(
                            f"No market price for {sorted(missing)} on watchlist "
                            f"{watchlist.id}; skipping those positions this cycle"
                        )
                    if not priced_positions:
                        logger.info(
                            f"No priced positions for watchlist {watchlist.id}; "
                            f"skipping metric/drawdown write this cycle"
                        )
                        continue

                    # Update position metrics (only for priced positions)
                    for position in priced_positions:
                        try:
                            await PortfolioRiskTracker.update_position_metrics(
                                session,
                                position.id,
                                current_prices[position.symbol],
                            )
                        except Exception as e:
                            logger.error(f"Failed to update position {position.id}: {e}")

                    # Starting capital from SIMULATE_BALANCE; equity is derived inside
                    # calculate_portfolio_metrics as starting_capital + total P&L.
                    starting_capital = _starting_capital()

                    # Calculate portfolio metrics
                    metrics = await PortfolioRiskTracker.calculate_portfolio_metrics(
                        session,
                        watchlist.id,
                        current_prices,
                        starting_capital,
                    )

                    # Record drawdown
                    await PortfolioRiskTracker.record_drawdown(
                        session,
                        watchlist.id,
                        risk_limit.id,
                        metrics,
                    )

                    # Update portfolio stats
                    await PortfolioRiskTracker.update_portfolio_stats(
                        session,
                        watchlist.id,
                        metrics,
                    )

                    # Check portfolio limits. Loss limits are measured against the
                    # deployed base capital (fixed threshold), not a moving equity.
                    limits_ok, alert = await RiskEngine.check_portfolio_limits(
                        session,
                        watchlist.id,
                        starting_capital,
                        metrics["total_pnl"],
                    )

                    if not limits_ok:
                        logger.warning(f"Portfolio limit exceeded for {watchlist.id}: {alert}")
                        # TODO: Send alert to user

                    await session.commit()

                    logger.info(
                        f"Portfolio monitored: {watchlist.id}, "
                        f"positions={metrics['open_positions']}, "
                        f"pnl=${metrics['total_pnl']}"
                    )

                except Exception as e:
                    logger.error(f"Error monitoring portfolio {watchlist.id}: {e}")
                    await session.rollback()

    asyncio.run(_monitor())


@shared_task(name="risk.update_risk_metrics")
def update_risk_metrics():
    """
    Calculate expensive risk metrics (Sharpe, VaR, correlation).

    Runs every 1 hour via Celery Beat.

    Operations:
    - Calculate Sharpe ratio from trade history
    - Calculate Value at Risk (VaR)
    - Update correlation matrix
    - Archive metrics snapshots
    """
    import asyncio

    async def _update():
        async with CeleryAsyncSessionLocal() as session:
            from sqlalchemy import select

            # Get all watchlists
            stmt = select(Watchlist)
            result = await session.execute(stmt)
            watchlists = result.scalars().all()

            for watchlist in watchlists:
                try:
                    logger.info(f"Updating risk metrics for {watchlist.id}")

                    # Get closed positions for historical analysis
                    stmt = select(Position).where(
                        Position.watchlist_id == watchlist.id,
                        Position.status == "closed",
                    )
                    result = await session.execute(stmt)
                    closed_positions = result.scalars().all()

                    if not closed_positions:
                        continue

                    # Calculate Sharpe ratio
                    sharpe = await PortfolioRiskTracker._calculate_sharpe_ratio(
                        closed_positions,
                        _starting_capital(),
                    )

                    # Calculate VaR
                    var_95 = await PortfolioRiskTracker._calculate_var(
                        closed_positions,
                        confidence=Decimal("0.95"),
                    )

                    logger.info(
                        f"Risk metrics calculated: {watchlist.id}, "
                        f"sharpe={sharpe}, var_95=${var_95}"
                    )

                except Exception as e:
                    logger.error(f"Error updating risk metrics for {watchlist.id}: {e}")

            await session.commit()

    asyncio.run(_update())


@shared_task(name="risk.check_emergency_conditions")
def check_emergency_conditions():
    """
    Check for emergency conditions and trigger alerts/actions.

    Runs every 1 minute via Celery Beat.

    Checks:
    - Daily loss limit exceeded
    - Max drawdown exceeded
    - Max consecutive losses
    - Margin call (if applicable)
    """
    import asyncio

    async def _check():
        async with CeleryAsyncSessionLocal() as session:
            from sqlalchemy import select

            # Get all watchlists
            stmt = select(Watchlist)
            result = await session.execute(stmt)
            watchlists = result.scalars().all()

            for watchlist in watchlists:
                try:
                    # Get risk limits
                    from app.modules.risk.models import RiskLimit

                    stmt = select(RiskLimit).where(RiskLimit.watchlist_id == watchlist.id)
                    result = await session.execute(stmt)
                    risk_limit = result.scalar_one_or_none()

                    if not risk_limit or not risk_limit.enabled:
                        continue

                    # Get all positions
                    stmt = select(Position).where(Position.watchlist_id == watchlist.id)
                    result = await session.execute(stmt)
                    all_positions = result.scalars().all()

                    # Guardrail: no positions at all -> no trades today -> no daily
                    # loss possible. Skip so we never raise a false circuit-breaker.
                    if not all_positions:
                        continue

                    # Calculate daily P&L (only realized on positions closed today)
                    today = datetime.now(timezone.utc).replace(hour=0, minute=0, second=0, microsecond=0)
                    daily_pnl = Decimal("0")

                    for position in all_positions:
                        if position.status == "closed" and position.exit_date and position.exit_date >= today:
                            if position.exit_price:
                                pnl = (Decimal(position.exit_price) - Decimal(position.entry_price)) * Decimal(
                                    position.quantity
                                )
                                daily_pnl += pnl

                    # Check daily loss limit against the configured base capital.
                    account_equity = _starting_capital()
                    daily_loss_limit = account_equity * (risk_limit.daily_loss_limit_percent / Decimal("100"))

                    if daily_pnl < -daily_loss_limit:
                        logger.critical(
                            f"EMERGENCY: Daily loss limit exceeded for {watchlist.id}: "
                            f"loss=${abs(daily_pnl)}, limit=${daily_loss_limit}"
                        )
                        # TODO: Trigger emergency actions:
                        # - Close all losing positions
                        # - Pause new trades
                        # - Send critical alert

                    await session.commit()

                except Exception as e:
                    logger.error(f"Error checking emergency conditions for {watchlist.id}: {e}")
                    await session.rollback()

    asyncio.run(_check())


@shared_task(name="risk.position_adjustment")
def position_adjustment():
    """
    Check and apply position adjustments (trailing stops, profit-taking, etc).

    Runs every 1 minute via Celery Beat.

    Adjustments:
    - Trailing stop updates
    - Partial profit-taking
    - Loss mitigation (close after X days)
    - Portfolio rebalancing
    """
    import asyncio

    async def _adjust():
        async with CeleryAsyncSessionLocal() as session:
            from sqlalchemy import select

            # Get all watchlists
            stmt = select(Watchlist)
            result = await session.execute(stmt)
            watchlists = result.scalars().all()

            for watchlist in watchlists:
                try:
                    # Get open positions
                    stmt = select(Position).where(
                        Position.watchlist_id == watchlist.id,
                        Position.status == "open",
                    )
                    result = await session.execute(stmt)
                    open_positions = result.scalars().all()

                    if not open_positions:
                        continue

                    # Get risk limits
                    from app.modules.risk.models import RiskLimit

                    stmt = select(RiskLimit).where(RiskLimit.watchlist_id == watchlist.id)
                    result = await session.execute(stmt)
                    risk_limit = result.scalar_one_or_none()

                    if not risk_limit:
                        continue

                    # Check each position for adjustments
                    for position in open_positions:
                        try:
                            # Get latest metrics
                            from app.modules.risk.models import PositionMetric

                            stmt = select(PositionMetric).where(
                                PositionMetric.position_id == position.id
                            ).order_by(PositionMetric.recorded_at.desc()).limit(1)
                            result = await session.execute(stmt)
                            metric = result.scalar_one_or_none()

                            if not metric:
                                continue

                            # Check time-based exit (position too old without profit)
                            if metric.days_in_trade >= risk_limit.max_position_age_days:
                                if metric.pnl_percent < Decimal("0"):
                                    logger.info(
                                        f"Position {position.id} exceeds max age {risk_limit.max_position_age_days} days "
                                        f"with loss {metric.pnl_percent:.2f}% - closing"
                                    )
                                    # TODO: Close position

                        except Exception as e:
                            logger.error(f"Error adjusting position {position.id}: {e}")

                    await session.commit()
                    logger.info(f"Position adjustments checked for {watchlist.id}")

                except Exception as e:
                    logger.error(f"Error in position adjustment for {watchlist.id}: {e}")
                    await session.rollback()

    asyncio.run(_adjust())
