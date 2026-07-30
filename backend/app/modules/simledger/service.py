"""Sim-ledger service (R1-3): bookkeeping for the simulation-only platform.

This layer ONLY books trades — orders, fills, position lots, cash — under an
idempotency key so a re-run cycle can never double-book. WHICH trades to make
(funnel, sizing, exits) is decided elsewhere: the scheduled cycle tasks apply
`quant.engine` pure functions, and manual user trades arrive with an explicit
quantity from the API. Every fill is priced through the same CostModel the
backtest used, so the live scoreboard stays comparable to the backtest.
"""

from __future__ import annotations

from datetime import date, datetime, timezone
from decimal import Decimal
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from quant.backtest.costs import CostModel
from quant.engine import sizing

from app.modules.simledger.models import (
    AccountSnapshot, SimAccount, SimFill, SimOrder, SimPosition,
)

_COSTS = CostModel()


def _dec(x: float | Decimal) -> Decimal:
    return Decimal(str(round(float(x), 6)))


class SimLedgerError(Exception):
    pass


class InsufficientCash(SimLedgerError):
    pass


class SimLedgerService:

    @staticmethod
    async def get_or_create_account(db: AsyncSession, user_id: UUID,
                                    name: str = "default", *, is_system: bool = False,
                                    starting_capital: float = 2000.0) -> SimAccount:
        row = (await db.execute(
            select(SimAccount).where(SimAccount.user_id == user_id,
                                     SimAccount.name == name)
        )).scalar_one_or_none()
        if row is not None:
            return row
        acct = SimAccount(user_id=user_id, name=name, is_system=is_system,
                          starting_capital=_dec(starting_capital),
                          cash=_dec(starting_capital))
        db.add(acct)
        await db.flush()
        return acct

    @staticmethod
    async def already_booked(db: AsyncSession, idempotency_key: str) -> bool:
        row = (await db.execute(
            select(SimOrder.id).where(SimOrder.idempotency_key == idempotency_key)
        )).scalar_one_or_none()
        return row is not None

    @staticmethod
    async def get_open_position(db: AsyncSession, account_id: UUID,
                                symbol: str) -> SimPosition | None:
        return (await db.execute(
            select(SimPosition).where(SimPosition.account_id == account_id,
                                      SimPosition.symbol == symbol,
                                      SimPosition.status == "open")
        )).scalar_one_or_none()

    @staticmethod
    async def get_open_positions(db: AsyncSession, account_id: UUID) -> list[SimPosition]:
        return list((await db.execute(
            select(SimPosition).where(SimPosition.account_id == account_id,
                                      SimPosition.status == "open")
        )).scalars().all())

    @staticmethod
    async def open_or_add(db: AsyncSession, account: SimAccount, *, symbol: str,
                          qty: float, raw_price: float, stop: float, reason: str,
                          idempotency_key: str, trade_date: date,
                          adv: float | None = None,
                          recommendation_id: UUID | None = None) -> SimOrder | None:
        """BUY: open a new lot, or pyramid into the existing open lot (the lot
        mutates — one open lot per account+symbol by schema). Returns None when
        this idempotency key was already booked (re-run safe)."""
        if qty <= 0 or raw_price <= 0:
            raise SimLedgerError(f"bad qty/price for {symbol}: {qty}/{raw_price}")
        if await SimLedgerService.already_booked(db, idempotency_key):
            return None
        price = _COSTS.entry_fill(float(raw_price), adv=adv)
        cost = qty * price
        if cost > float(account.cash) + 1e-9:
            raise InsufficientCash(
                f"{symbol}: cost {cost:.2f} > cash {float(account.cash):.2f}")

        pos = await SimLedgerService.get_open_position(db, account.id, symbol)
        if pos is None:
            pos = SimPosition(account_id=account.id, symbol=symbol, status="open",
                              shares=_dec(qty), avg_cost=_dec(price), stop=_dec(stop),
                              r_unit=_dec(price - stop), high_water=_dec(price),
                              entry_date=trade_date, adds_done=0,
                              reversal_count=0, bars_held=0)
            db.add(pos)
            await db.flush()
        else:
            new_total = float(pos.shares) + qty
            pos.avg_cost = _dec(sizing.blend_avg_cost(
                float(pos.shares), float(pos.avg_cost), qty, price))
            pos.shares = _dec(new_total)
            pos.stop = _dec(max(float(pos.stop), stop))   # adds never lower the stop
            pos.adds_done = int(pos.adds_done) + 1

        order = SimOrder(account_id=account.id, position_id=pos.id,
                         recommendation_id=recommendation_id, symbol=symbol,
                         side="buy", qty=_dec(qty), order_type="market",
                         status="filled", reason=reason,
                         idempotency_key=idempotency_key,
                         filled_at=datetime.now(timezone.utc))
        db.add(order)
        await db.flush()
        db.add(SimFill(order_id=order.id, price=_dec(price),
                       raw_price=_dec(raw_price), qty=_dec(qty)))
        account.cash = _dec(float(account.cash) - cost)
        return order

    @staticmethod
    async def close_position(db: AsyncSession, account: SimAccount,
                             position: SimPosition, *, raw_price: float,
                             reason: str, idempotency_key: str,
                             adv: float | None = None) -> SimOrder | None:
        """SELL the whole lot (rev2 has no partial take-profit). Returns None
        when already booked under this idempotency key."""
        if position.status != "open":
            raise SimLedgerError(f"{position.symbol}: position not open")
        if await SimLedgerService.already_booked(db, idempotency_key):
            return None
        qty = float(position.shares)
        price = _COSTS.exit_fill(float(raw_price), adv=adv)

        order = SimOrder(account_id=account.id, position_id=position.id,
                         symbol=position.symbol, side="sell", qty=_dec(qty),
                         order_type="market", status="filled", reason=reason,
                         idempotency_key=idempotency_key,
                         filled_at=datetime.now(timezone.utc))
        db.add(order)
        await db.flush()
        db.add(SimFill(order_id=order.id, price=_dec(price),
                       raw_price=_dec(raw_price), qty=_dec(qty)))
        account.cash = _dec(float(account.cash) + qty * price)
        position.status = "closed"
        position.close_reason = reason
        position.closed_at = datetime.now(timezone.utc)
        return order

    @staticmethod
    def equity(account: SimAccount, open_positions: list[SimPosition],
               quotes: dict[str, float]) -> float:
        """Cash + open lots marked at the given quotes (avg_cost fallback when a
        quote is missing — the cycle's stale-quote guard alerts separately)."""
        mtm = float(account.cash)
        for p in open_positions:
            q = quotes.get(p.symbol)
            mtm += float(p.shares) * (float(q) if q else float(p.avg_cost))
        return mtm

    @staticmethod
    async def snapshot(db: AsyncSession, account: SimAccount, snapshot_date: date,
                       quotes: dict[str, float]) -> AccountSnapshot:
        """Upsert the daily equity point for the dashboard curve."""
        positions = await SimLedgerService.get_open_positions(db, account.id)
        eq = SimLedgerService.equity(account, positions, quotes)
        row = (await db.execute(
            select(AccountSnapshot).where(
                AccountSnapshot.account_id == account.id,
                AccountSnapshot.snapshot_date == snapshot_date)
        )).scalar_one_or_none()
        if row is None:
            row = AccountSnapshot(account_id=account.id, snapshot_date=snapshot_date,
                                  equity=_dec(eq), cash=account.cash,
                                  open_positions=len(positions))
            db.add(row)
        else:
            row.equity = _dec(eq)
            row.cash = account.cash
            row.open_positions = len(positions)
        return row
