"""Async PostgreSQL session inside one outer transaction that is always rolled
back: code under test may ``commit()`` freely (each commit only releases a
savepoint), and no row ever reaches the real tables."""

import os

import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlalchemy.pool import NullPool


def _no_pg(reason):
    if os.environ.get("CAT_NO_PG") == "1":
        pytest.skip(reason)
    pytest.fail(f"{reason} — run `make test-backend` (it exports the URL from "
                ".env), or set CAT_NO_PG=1 to acknowledge the gap")


@pytest_asyncio.fixture
async def pg_session_rollback():
    url = os.environ.get("DATABASE_URL")
    if not url:
        _no_pg("DATABASE_URL not set")
    engine = create_async_engine(url, poolclass=NullPool)
    conn = await engine.connect()
    trans = await conn.begin()
    session = AsyncSession(bind=conn, join_transaction_mode="create_savepoint",
                           expire_on_commit=False)
    try:
        yield session
    finally:
        await session.close()
        await trans.rollback()
        await conn.close()
        await engine.dispose()
