"""R0-1 smoke test: the package imports and config constants are sane."""

from quant import config


def test_config_imports_and_paths():
    assert config.REPO_ROOT.name == "cloud-ai-trading"
    assert config.DATA_ROOT == config.REPO_ROOT / "cat-data"
    assert config.MANIFEST_DB.parent == config.META_DIR


def test_master_settings_sane():
    assert config.STARTING_CAPITAL_USD == 2_000.0
    assert config.PER_TRADE_RISK_PCT == 0.03
    assert config.PORTFOLIO_DRAWDOWN_HALT_PCT == 0.15
    assert config.MAX_PYRAMID_ADDS_PER_SYMBOL == 1
    # ladder is ordered by ascending equity threshold and starts at 3 slots
    thresholds = [t for t, _ in config.POSITION_LADDER]
    assert thresholds == sorted(thresholds)
    assert config.POSITION_LADDER[0] == (2_000.0, 3)


def test_data_feed_is_sip():
    # design §4.5 / research B1: never pull history via IEX
    assert config.DATA_FEED == "sip"
    assert config.SIP_DELAY_MINUTES == 15


def test_subpackages_import():
    import quant.data  # noqa: F401
    import quant.engine  # noqa: F401
    import quant.backtest  # noqa: F401
    import quant.research  # noqa: F401
