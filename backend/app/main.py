"""
Cloud AI Trading - FastAPI Application Entry Point
"""

import asyncio
import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI

from app.config import get_settings
from app.core.middleware import setup_middleware

# Import all ORM models before handling requests so SQLAlchemy can resolve
# string-based relationships even when some routers are disabled. The full
# registry lives in one place (app/models_registry.py) shared with Alembic and
# Celery so the three import lists can no longer drift apart.
import app.models_registry  # noqa: F401

# Import all routers
from app.modules.auth.router import router as auth_router
from app.modules.market.router import router as market_router
from app.modules.watchlist.router import router as watchlist_router
from app.modules.simledger.router import router as simledger_router
from app.modules.llm.router import router as llm_router
from app.modules.nightwatch.router import router as nightwatch_router
from app.modules.system.routes import router as system_router
from app.modules.admin.router import router as admin_router

settings = get_settings()

# Configure logging
logging.basicConfig(
    level=logging.DEBUG if settings.DEBUG else logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
)
logger = logging.getLogger("cloud_ai_trading")


def _run_migrations():
    """Run Alembic migrations synchronously on startup."""
    try:
        from alembic.config import Config
        from alembic import command

        alembic_cfg = Config("alembic.ini")
        alembic_cfg.set_main_option("sqlalchemy.url", settings.DATABASE_URL_SYNC)
        command.upgrade(alembic_cfg, "head")
        logger.info("Database migrations applied successfully.")
    except Exception as e:
        logger.error(f"Migration runner failed: {e}", exc_info=True)
        # Fail fast outside production so a broken migration aborts startup
        # instead of booting a server whose every DB request 500s (false green).
        # In production we keep booting (log-only) to avoid an outage on a
        # transient migration error; the elevated log level surfaces it.
        if settings.ENVIRONMENT != "production":
            raise


async def _validate_master_settings() -> list[str]:
    """§8.6 read path: master_settings may only TIGHTEN a risk knob. A row that
    would loosen one (or a key nobody knows) is a configuration error.

    Loud, never fatal (拍板 2026-09-10). This process also hosts the watchdog
    that catches a wedged worker, so refusing to boot would leave the worker
    trading unwatched — and the cycles have already fallen back to the constant
    anyway. Same posture as the worker's worker_ready check."""
    from app.modules.simledger.settings import validate_settings

    try:
        from app.database import AsyncSessionLocal

        async with AsyncSessionLocal() as db:
            problems = await validate_settings(db)
    except Exception as e:
        logger.error(f"master_settings validation could not run: {e}", exc_info=True)
        return []
    if not problems:
        return []
    detail = "; ".join(problems)
    logger.error(f"master_settings validation failed: {detail}")
    try:
        from app.modules.notifications import TelegramNotifier

        await TelegramNotifier().send_message(
            "⚠️ master_settings 有非法行（只允许收紧）— backend 用常量在跑: " + detail,
            parse_mode=None)
    except Exception:
        logger.warning("master_settings alert could not be sent", exc_info=True)
    return problems


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Application startup and shutdown events."""
    logger.info(f"Starting {settings.APP_NAME} v{settings.APP_VERSION}")
    logger.info(f"Debug mode: {settings.DEBUG}")
    if settings.ALPACA_MODE == "live":
        logger.warning(
            "ALPACA_MODE=live — system-level Alpaca defaults point at the LIVE "
            "endpoint. Auto-execution remains paper-forced regardless."
        )
    _run_migrations()
    await _validate_master_settings()
    # Pipeline watchdog lives in THIS process because it exists to catch a
    # wedged Celery worker — it can't run on the thing it monitors.
    from app.modules.system.watchdog import run_watchdog
    watchdog_task = asyncio.create_task(run_watchdog())
    yield
    watchdog_task.cancel()
    logger.info("Shutting down...")


# Create FastAPI application
app = FastAPI(
    title=settings.APP_NAME,
    version=settings.APP_VERSION,
    description="Quantitative US-equity paper-trading platform (simledger + quant engine)",
    docs_url="/api/docs" if settings.DEBUG else None,
    redoc_url="/api/redoc" if settings.DEBUG else None,
    lifespan=lifespan,
)

# Setup middleware (CORS, logging, rate limiting)
setup_middleware(app)

# Register API routers
app.include_router(auth_router, prefix=settings.API_V1_PREFIX)
app.include_router(market_router, prefix=settings.API_V1_PREFIX)
app.include_router(watchlist_router, prefix=settings.API_V1_PREFIX)
app.include_router(simledger_router, prefix=settings.API_V1_PREFIX)
app.include_router(llm_router, prefix=settings.API_V1_PREFIX)
app.include_router(nightwatch_router, prefix=settings.API_V1_PREFIX)
app.include_router(system_router, prefix=settings.API_V1_PREFIX)
app.include_router(admin_router, prefix=settings.API_V1_PREFIX)


@app.get("/api/health")
async def health_check():
    """Health check endpoint."""
    return {
        "status": "healthy",
        "app": settings.APP_NAME,
        "version": settings.APP_VERSION,
    }


@app.get("/")
async def root():
    """Root redirect to API docs."""
    return {
        "message": f"Welcome to {settings.APP_NAME}",
        "docs": "/api/docs",
        "health": "/api/health",
    }
