"""Strategy engine: indicators, Signal, StrategyEngine, funnel, sizing, exits.

Everything here is PURE (same input -> same output, no I/O). Enforced by a test
that greps for httpx/requests/sqlalchemy imports. Shared verbatim between
backtest and live (架构铁律 ①)."""
