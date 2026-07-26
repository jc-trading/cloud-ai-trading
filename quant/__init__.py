"""CAT quant core — deterministic backtest + strategy engine.

Framework-free package (no FastAPI/Celery/SQLAlchemy). The engine/ subpackage is
pure functions shared verbatim between backtest and live (架构铁律 ①). Live/host
wiring lives in backend/ and imports from here; nothing here imports backend.
"""
