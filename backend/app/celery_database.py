"""
Database session factory for Celery tasks.

Celery tasks in this project bridge sync task functions to async database work
with a fresh event loop per task. Asyncpg connections are bound to the event
loop that created them, so Celery must not reuse pooled async connections across
task runs.
"""

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from app.config import settings


celery_engine = create_async_engine(
    settings.DATABASE_URL,
    echo=settings.DEBUG,
    poolclass=NullPool,
    pool_pre_ping=True,
)

CeleryAsyncSessionLocal = async_sessionmaker(
    celery_engine,
    class_=AsyncSession,
    expire_on_commit=False,
)
