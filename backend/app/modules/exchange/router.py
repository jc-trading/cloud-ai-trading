"""
Exchange connection API routes.
"""

from fastapi import APIRouter, Depends, HTTPException
from uuid import UUID

from app.dependencies import CurrentUser, DB, require_permission
from app.modules.auth.models import User
from app.modules.exchange.schemas import (
    ExchangeCreate,
    ExchangeUpdate,
    ExchangeResponse,
    ExchangeTestResult,
    BalanceResponse,
)
from app.modules.exchange.service import ExchangeService

router = APIRouter(prefix="/exchanges", tags=["Exchanges"])


@router.get("", response_model=list[ExchangeResponse])
async def list_connections(
    current_user: CurrentUser,
    db: DB,
):
    """List all exchange connections for current user."""
    connections = await ExchangeService.get_connections(db, current_user.id)
    return [ExchangeResponse.model_validate(c) for c in connections]


@router.post("", response_model=ExchangeResponse, status_code=201)
async def create_connection(
    data: ExchangeCreate,
    db: DB,
    user: User = Depends(require_permission("connect_exchange")),
):
    """Create a new exchange connection. v3 is stocks-only: Alpaca is the sole
    supported broker — anything else is refused up front (QA finding #13 saw a
    Binance connection persist and then 500 on test)."""
    if str(data.exchange_type).lower().split(".")[-1] != "alpaca":
        raise HTTPException(status_code=422,
                            detail="Only Alpaca connections are supported (stocks/ETF only)")
    connection = await ExchangeService.create_connection(db, user.id, data)
    return ExchangeResponse.model_validate(connection)


@router.put("/{connection_id}", response_model=ExchangeResponse)
async def update_connection(
    connection_id: UUID,
    data: ExchangeUpdate,
    current_user: CurrentUser,
    db: DB,
):
    """Update an exchange connection."""
    connection = await ExchangeService.update_connection(
        db, current_user.id, connection_id, data
    )
    return ExchangeResponse.model_validate(connection)


@router.delete("/{connection_id}", status_code=204)
async def delete_connection(
    connection_id: UUID,
    current_user: CurrentUser,
    db: DB,
):
    """Delete an exchange connection."""
    await ExchangeService.delete_connection(db, current_user.id, connection_id)


@router.post("/{connection_id}/test", response_model=ExchangeTestResult)
async def test_connection(
    connection_id: UUID,
    current_user: CurrentUser,
    db: DB,
):
    """Test an exchange connection — adapter/credential failures surface as a
    clean 422, never a bare 500."""
    try:
        result = await ExchangeService.test_connection(db, current_user.id, connection_id)
    except ValueError as e:
        raise HTTPException(status_code=422, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=422, detail=f"connection test failed: {e}")
    return ExchangeTestResult(**result)


@router.get("/{connection_id}/balance", response_model=BalanceResponse)
async def get_balance(
    connection_id: UUID,
    current_user: CurrentUser,
    db: DB,
):
    """Get balance from exchange account. Adapter failures surface as a clean
    422, never a bare 500 (review #22: a legacy non-Alpaca row must not break
    the settings page)."""
    try:
        result = await ExchangeService.get_balance(db, current_user.id, connection_id)
    except ValueError as e:
        raise HTTPException(status_code=422, detail=str(e))
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=422, detail=f"balance fetch failed: {e}")
    return BalanceResponse(**result)
