"""P3a: the outcome / IC reports read one strategy group at a time, against
real PostgreSQL (rolled back). Two groups with a rec on the same symbol and
trade_date must not see each other's rows, and the CLIs refuse to guess which
group is meant once there is more than one."""

from datetime import date
from uuid import uuid4

import pytest
import pytest_asyncio
from sqlalchemy import select

import app.models_registry  # noqa: F401
from quant.engine.strategies import V1_COMPOSITE_KEY, get_spec

from app.modules.auth.models import User, UserRole
from app.modules.simledger import outcomes, outcomes_ic
from app.modules.simledger.models import Recommendation, RecommendationOutcome, SimAccount
from app.modules.simledger.service import _dec
from app.modules.strategy_groups.models import StrategyGroup
from tests._pg_rollback import pg_session_rollback  # noqa: F401

DAY = date(2026, 10, 16)


def _rec(group_id, confidence):
    rec = Recommendation(id=uuid4(), group_id=group_id, symbol="ZZT1", trade_date=DAY,
                         direction="up", confidence=_dec(confidence), phase="up")
    out = RecommendationOutcome(recommendation_id=rec.id, symbol="ZZT1", trade_date=DAY,
                                base_open=_dec(10), close_d0=_dec(10.5), ret_1d=_dec(0.01),
                                evaluated_through=DAY)
    return rec, out


@pytest_asyncio.fixture
async def two_groups(pg_session_rollback):
    db = pg_session_rollback
    g1 = (await db.execute(select(StrategyGroup).order_by(StrategyGroup.created_at)
                           .limit(1))).scalar_one()
    user_id = (await db.execute(select(User.id).where(
        User.role == UserRole.SUPER_ADMIN).limit(1))).scalar_one()
    gid = uuid4()
    acct = SimAccount(user_id=user_id, name=f"grp:{gid}", is_system=True,
                      starting_capital=_dec(2000), cash=_dec(2000))
    db.add(acct)
    await db.flush()
    g2 = StrategyGroup(id=gid, name=f"zz-p3a-{gid.hex[:8]}", strategy_key=V1_COMPOSITE_KEY,
                       params_json=get_spec(V1_COMPOSITE_KEY).default_params(),
                       status="paused", sim_account_id=acct.id, account_is_system=True)
    db.add(g2)
    await db.flush()
    for group_id, confidence in ((g1.id, 70), (g2.id, 80)):
        rec, out = _rec(group_id, confidence)
        db.add(rec)
        await db.flush()
        db.add(out)
    await db.flush()
    return db, g1, g2


@pytest.mark.asyncio
async def test_load_frame_keeps_each_groups_rows_apart(two_groups):
    db, g1, g2 = two_groups
    df2, total2 = await outcomes_ic.load_frame(db, group_id=g2.id)
    assert len(df2) == 1 and total2 == 1
    assert df2.iloc[0]["confidence"] == 80.0
    df1, total1 = await outcomes_ic.load_frame(db, group_id=g1.id)
    zz = df1[df1["symbol"] == "ZZT1"]
    assert len(zz) == 1 and zz.iloc[0]["confidence"] == 70.0
    assert total1 >= 1


@pytest.mark.asyncio
async def test_build_report_counts_only_its_group(two_groups):
    db, _, g2 = two_groups
    report = await outcomes.build_report(db, group_id=g2.id)
    assert "recommendations in DB: 1; evaluated: 1 (" in report


@pytest.mark.asyncio
async def test_resolve_group_refuses_to_guess_between_groups(two_groups):
    db, g1, g2 = two_groups
    with pytest.raises(SystemExit) as exc:
        await outcomes.resolve_group(db, None)
    assert "multiple strategy groups" in str(exc.value)
    assert g1.name in str(exc.value) and g2.name in str(exc.value)
    with pytest.raises(SystemExit, match="unknown group 'nope'"):
        await outcomes.resolve_group(db, "nope")
    assert (await outcomes.resolve_group(db, g2.name)).id == g2.id


@pytest.mark.asyncio
async def test_resolve_group_takes_the_only_group(pg_session_rollback):
    groups = (await pg_session_rollback.execute(select(StrategyGroup))).scalars().all()
    assert len(groups) == 1
    assert (await outcomes.resolve_group(pg_session_rollback, None)).id == groups[0].id


def test_outcomes_ic_cli_takes_a_group():
    assert outcomes_ic._parse_args(["--group", "v1 对照"]).group == "v1 对照"
    assert outcomes_ic._parse_args([]).group is None
