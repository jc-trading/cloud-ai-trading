"""
Watchlist API routes.

Design: every user has a "default" watchlist that is auto-created on first use.
The frontend doesn't need to manage watchlist IDs — it just calls /watchlists/default/*.
"""

from uuid import UUID
from fastapi import APIRouter, Depends

from app.dependencies import CurrentUser, DB, require_permission
from app.modules.auth.models import User
from app.modules.watchlist.schemas import (
    WatchlistCreate,
    WatchlistResponse,
    WatchlistItemCreate,
    WatchlistItemResponse,
    WatchlistItemWithPrice,
)
from app.modules.watchlist.service import WatchlistService

router = APIRouter(prefix="/watchlists", tags=["Watchlist"])


# ── Default watchlist (most commonly used) ────────────────────────

@router.get("/default", response_model=WatchlistResponse)
async def get_default_watchlist(
    db: DB,
    user: User = Depends(require_permission("manage_watchlist")),
):
    """Get (or auto-create) the user's default watchlist."""
    wl = await WatchlistService.get_or_create_default(db, user.id)
    return WatchlistResponse.model_validate(wl)


@router.post("/default/items", response_model=WatchlistItemResponse, status_code=201)
async def add_to_default(
    data: WatchlistItemCreate,
    db: DB,
    user: User = Depends(require_permission("manage_watchlist")),
):
    """Add a symbol to the default watchlist (auto-creates it if needed)."""
    wl = await WatchlistService.get_or_create_default(db, user.id)
    item = await WatchlistService.add_item(db, user.id, wl.id, data)
    return WatchlistItemResponse.model_validate(item)


@router.delete("/default/items/{item_id}", status_code=204)
async def remove_from_default(
    item_id: UUID,
    db: DB,
    user: User = Depends(require_permission("manage_watchlist")),
):
    """Remove a symbol from the default watchlist."""
    wl = await WatchlistService.get_or_create_default(db, user.id)
    await WatchlistService.remove_item(db, user.id, wl.id, item_id)


@router.get("/default/prices", response_model=list[WatchlistItemWithPrice])
async def get_default_with_prices(
    db: DB,
    user: User = Depends(require_permission("manage_watchlist")),
):
    """Get default watchlist items enriched with live market prices."""
    return await WatchlistService.get_items_with_prices(db, user.id)


# ── Full watchlist CRUD (for multi-list feature later) ───────────

@router.get("", response_model=list[WatchlistResponse])
async def list_watchlists(
    db: DB,
    user: User = Depends(require_permission("manage_watchlist")),
):
    """Get all watchlists for current user."""
    watchlists = await WatchlistService.get_watchlists(db, user.id)
    return [WatchlistResponse.model_validate(w) for w in watchlists]


@router.post("", response_model=WatchlistResponse, status_code=201)
async def create_watchlist(
    data: WatchlistCreate,
    db: DB,
    user: User = Depends(require_permission("manage_watchlist")),
):
    """Create a new named watchlist."""
    wl = await WatchlistService.create_watchlist(db, user.id, data)
    return WatchlistResponse.model_validate(wl)


@router.delete("/{watchlist_id}", status_code=204)
async def delete_watchlist(
    watchlist_id: UUID,
    db: DB,
    user: User = Depends(require_permission("manage_watchlist")),
):
    """Delete a watchlist."""
    await WatchlistService.delete_watchlist(db, user.id, watchlist_id)


@router.get("/{watchlist_id}/items", response_model=list[WatchlistItemResponse])
async def get_items(
    watchlist_id: UUID,
    db: DB,
    user: CurrentUser,
):
    """Get items in a watchlist."""
    wl = await WatchlistService.get_watchlist(db, user.id, watchlist_id)
    return [WatchlistItemResponse.model_validate(i) for i in wl.items]


@router.post("/{watchlist_id}/items", response_model=WatchlistItemResponse, status_code=201)
async def add_item(
    watchlist_id: UUID,
    data: WatchlistItemCreate,
    db: DB,
    user: User = Depends(require_permission("manage_watchlist")),
):
    """Add a symbol to a specific watchlist."""
    item = await WatchlistService.add_item(db, user.id, watchlist_id, data)
    return WatchlistItemResponse.model_validate(item)


@router.delete("/{watchlist_id}/items/{item_id}", status_code=204)
async def remove_item(
    watchlist_id: UUID,
    item_id: UUID,
    db: DB,
    user: User = Depends(require_permission("manage_watchlist")),
):
    """Remove a symbol from a watchlist."""
    await WatchlistService.remove_item(db, user.id, watchlist_id, item_id)
