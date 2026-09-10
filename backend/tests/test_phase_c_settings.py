"""Phase C #12 — the tighten-only master_settings read path and its startup checks.

The hard acceptance criterion: seeded at the constants, the effective settings
ARE the constants (migration 018 changes nothing at t0). Everything else is the
rejection ladder — looser, unknown, non-finite — and where a rejection lands
(backend refuses to boot outside production; the worker only alerts).
"""

import asyncio
from decimal import Decimal

import pytest

import app.models_registry  # noqa: F401

from app.modules.simledger import cycles
from app.modules.simledger import settings as settings_mod
from app.modules.simledger.models import MasterSetting
from quant import config as qconfig

import tasks.celery_app as celery_app_mod


class _Session:
    """Returns the given master_settings rows for the single SELECT it serves."""

    def __init__(self, rows):
        self._rows = rows

    async def execute(self, _stmt):
        rows = self._rows

        class _R:
            def scalars(self):
                return iter(rows)
        return _R()

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False


def _row(key, value):
    return MasterSetting(key=key, value=Decimal(str(value)), tighten_only=True)


def _effective(rows):
    return asyncio.run(settings_mod.effective_settings(_Session(rows)))


# --- the acceptance criterion: seed == constants -> zero behaviour change ----

SEED = {
    "per_trade_risk_pct": qconfig.PER_TRADE_RISK_PCT,
    "daily_loss_pause_pct": qconfig.DAILY_LOSS_PAUSE_PCT,
    "portfolio_drawdown_halt_pct": qconfig.PORTFOLIO_DRAWDOWN_HALT_PCT,
    "min_confidence": cycles.RECOMMENDED_FUNNEL.min_confidence,
    "intraday_entry_chase_cap": qconfig.INTRADAY_ENTRY_CHASE_CAP,
    "max_concurrent_slots": qconfig.POSITION_LADDER[-1][1],
}


def test_knob_registry_matches_the_deployed_constants():
    assert {k.key: k.default for k in settings_mod.KNOBS} == \
        {k: float(v) for k, v in SEED.items()}


def test_seeded_at_the_constants_is_a_no_op():
    eff = _effective([_row(k, v) for k, v in SEED.items()])
    assert eff.rejected == ()
    assert eff.per_trade_risk_pct == qconfig.PER_TRADE_RISK_PCT
    assert eff.daily_loss_pause_pct == qconfig.DAILY_LOSS_PAUSE_PCT
    assert eff.portfolio_drawdown_halt_pct == qconfig.PORTFOLIO_DRAWDOWN_HALT_PCT
    assert eff.min_confidence == cycles.RECOMMENDED_FUNNEL.min_confidence
    assert eff.intraday_entry_chase_cap == qconfig.INTRADAY_ENTRY_CHASE_CAP
    assert eff.max_concurrent_slots == qconfig.POSITION_LADDER[-1][1]


def test_empty_table_falls_back_to_the_constants():
    """The state on the live DB until migration 018 lands."""
    assert _effective([]) == _effective([_row(k, v) for k, v in SEED.items()])


# --- tightening is allowed in the declared direction only -------------------

def test_tightening_is_accepted_in_both_directions():
    eff = _effective([_row("per_trade_risk_pct", 0.01),      # down = stricter
                      _row("min_confidence", 80)])           # up   = stricter
    assert (eff.per_trade_risk_pct, eff.min_confidence) == (0.01, 80.0)
    assert eff.rejected == ()


@pytest.mark.parametrize("key,value", [
    ("per_trade_risk_pct", 0.10),
    ("daily_loss_pause_pct", 0.50),
    ("portfolio_drawdown_halt_pct", 0.90),
    ("min_confidence", 10),
    ("intraday_entry_chase_cap", 0.20),
    ("max_concurrent_slots", 50),
])
def test_a_looser_value_is_rejected_and_the_constant_applies(key, value, caplog):
    eff = _effective([_row(key, value)])
    assert len(eff.rejected) == 1 and "LOOSER" in eff.rejected[0]
    assert getattr(eff, key) == settings_mod._BY_KEY[key].default
    assert "master_settings rejected" in caplog.text


def test_unknown_key_is_rejected_without_touching_the_others():
    eff = _effective([_row("delete_all_the_money", 1),
                      _row("per_trade_risk_pct", 0.01)])
    assert eff.rejected == ("unknown key 'delete_all_the_money'",)
    assert eff.per_trade_risk_pct == 0.01


def test_non_finite_value_is_rejected():
    eff = _effective([_row("per_trade_risk_pct", float("nan"))])
    assert len(eff.rejected) == 1 and "not finite" in eff.rejected[0]
    assert eff.per_trade_risk_pct == qconfig.PER_TRADE_RISK_PCT


def test_max_concurrent_slots_is_an_int_for_the_ladder_min():
    eff = _effective([_row("max_concurrent_slots", 4)])
    assert eff.max_concurrent_slots == 4 and isinstance(eff.max_concurrent_slots, int)


def test_validate_settings_reports_exactly_the_rejections():
    problems = asyncio.run(settings_mod.validate_settings(
        _Session([_row("per_trade_risk_pct", 0.99), _row("min_confidence", 80)])))
    assert len(problems) == 1 and "per_trade_risk_pct" in problems[0]


# --- startup wiring ---------------------------------------------------------

@pytest.fixture
def stub_db(monkeypatch):
    """Both startup checks open their own session; hand them an empty table."""
    import app.database as database
    import app.celery_database as celery_database

    monkeypatch.setattr(database, "AsyncSessionLocal", lambda: _Session([]))
    monkeypatch.setattr(celery_database, "CeleryAsyncSessionLocal",
                        lambda: _Session([]))


@pytest.fixture
def rejecting_db(monkeypatch):
    import app.database as database
    import app.celery_database as celery_database

    rows = [_row("per_trade_risk_pct", 0.99)]
    monkeypatch.setattr(database, "AsyncSessionLocal", lambda: _Session(rows))
    monkeypatch.setattr(celery_database, "CeleryAsyncSessionLocal",
                        lambda: _Session(rows))


@pytest.fixture
def backend_alerts(monkeypatch):
    sent = []

    class _Notifier:
        async def send_message(self, message, parse_mode=None):
            sent.append(message)
            return True

    import app.main as main

    monkeypatch.setattr("app.modules.notifications.TelegramNotifier",
                        lambda *a, **kw: _Notifier())
    return sent


@pytest.mark.asyncio
async def test_backend_startup_passes_on_a_clean_table(stub_db, backend_alerts):
    import app.main as main

    assert await main._validate_master_settings() == []
    assert backend_alerts == []


@pytest.mark.parametrize("environment", ["local", "development", "production"])
@pytest.mark.asyncio
async def test_backend_startup_alerts_but_never_refuses_to_boot(
        rejecting_db, backend_alerts, monkeypatch, caplog, environment):
    """This process also hosts the watchdog: a crash-looping backend would leave
    the worker trading unwatched over a row the cycles already ignored."""
    import app.main as main

    monkeypatch.setattr(main.settings, "ENVIRONMENT", environment)
    problems = await main._validate_master_settings()
    assert len(problems) == 1 and "per_trade_risk_pct" in problems[0]
    assert "master_settings validation failed" in caplog.text
    assert len(backend_alerts) == 1 and "master_settings" in backend_alerts[0]


@pytest.mark.asyncio
async def test_backend_startup_survives_an_unreachable_database(monkeypatch,
                                                                caplog):
    import app.database as database
    import app.main as main

    def boom():
        raise RuntimeError("connection refused")

    monkeypatch.setattr(database, "AsyncSessionLocal", boom)
    assert await main._validate_master_settings() == []
    assert "could not run" in caplog.text


@pytest.mark.asyncio
async def test_worker_alerts_but_never_refuses_to_start(rejecting_db, monkeypatch):
    """A worker that will not boot stops trading entirely — worse than running
    on the constants it already fell back to."""
    sent = []

    async def _notify(message):
        sent.append(message)
        return True

    monkeypatch.setattr("app.tasks.quant_tasks._notify", _notify)
    problems = await celery_app_mod.check_master_settings()
    assert len(problems) == 1
    assert len(sent) == 1 and "master_settings" in sent[0]


@pytest.mark.asyncio
async def test_worker_is_silent_when_settings_are_legal(stub_db, monkeypatch):
    sent = []

    async def _notify(message):
        sent.append(message)
        return True

    monkeypatch.setattr("app.tasks.quant_tasks._notify", _notify)
    assert await celery_app_mod.check_master_settings() == []
    assert sent == []
