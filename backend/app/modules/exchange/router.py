"""
Exchange connection API routes.
"""

from fastapi import APIRouter, Depends
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
    """Create a new exchange connection."""
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
    """Test an exchange connection."""
    result = await ExchangeService.test_connection(db, current_user.id, connection_id)
    return ExchangeTestResult(**result)


@router.get("/{connection_id}/balance", response_model=BalanceResponse)
async def get_balance(
    connection_id: UUID,
    current_user: CurrentUser,
    db: DB,
):
    """Get balance from exchange account."""
    result = await ExchangeService.get_balance(db, current_user.id, connection_id)
    return BalanceResponse(**result)
