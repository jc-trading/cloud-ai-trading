"""
Watchlist service: CRUD operations for watchlists and items.
"""

import asyncio
from uuid import UUID
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.core.exceptions import NotFoundException
from app.modules.watchlist.models import Watchlist, WatchlistItem
from app.modules.watchlist.schemas import WatchlistCreate, WatchlistItemCreate, WatchlistItemWithPrice


class WatchlistService:

    @staticmethod
    async def get_watchlists(db: AsyncSession, user_id: UUID) -> list[Watchlist]:
        result = await db.execute(
            select(Watchlist)
            .options(selectinload(Watchlist.items))
            .where(Watchlist.user_id == user_id)
            .order_by(Watchlist.created_at.desc())
        )
        return list(result.scalars().all())

    @staticmethod
    async def get_or_create_default(db: AsyncSession, user_id: UUID) -> Watchlist:
        """Get the user's default watchlist, creating it if it doesn't exist."""
        result = await db.execute(
            select(Watchlist)
            .options(selectinload(Watchlist.items))
            .where(Watchlist.user_id == user_id, Watchlist.name == "Default")
            .limit(1)
        )
        wl = result.scalar_one_or_none()
        if wl is None:
            wl = Watchlist(user_id=user_id, name="Default")
            db.add(wl)
            await db.flush()
            await db.refresh(wl, ["items"])
        return wl

    @staticmethod
    async def create_watchlist(db: AsyncSession, user_id: UUID, data: WatchlistCreate) -> Watchlist:
        wl = Watchlist(user_id=user_id, name=data.name)
        db.add(wl)
        await db.flush()
        await db.refresh(wl, ["items"])
        return wl

    @staticmethod
    async def get_watchlist(db: AsyncSession, user_id: UUID, watchlist_id: UUID) -> Watchlist:
        result = await db.execute(
            select(Watchlist)
            .options(selectinload(Watchlist.items))
            .where(Watchlist.id == watchlist_id, Watchlist.user_id == user_id)
        )
        wl = result.scalar_one_or_none()
        if not wl:
            raise NotFoundException("Watchlist")
        return wl

    @staticmethod
    async def delete_watchlist(db: AsyncSession, user_id: UUID, watchlist_id: UUID) -> None:
        wl = await WatchlistService.get_watchlist(db, user_id, watchlist_id)
        await db.delete(wl)

    @staticmethod
    async def add_item(
        db: AsyncSession, user_id: UUID, watchlist_id: UUID, data: WatchlistItemCreate
    ) -> WatchlistItem:
        # Verify ownership
        await WatchlistService.get_watchlist(db, user_id, watchlist_id)

        # Check duplicate
        result = await db.execute(
            select(WatchlistItem).where(
                WatchlistItem.watchlist_id == watchlist_id,
                WatchlistItem.symbol == data.symbol,
            )
        )
        if result.scalar_one_or_none():
            from fastapi import HTTPException
            raise HTTPException(status_code=409, detail=f"{data.symbol} is already in your watchlist")

        # Infer exchange_type from market_type
        exchange_type = "alpaca" if data.market_type == "stock" else "binance"

        item = WatchlistItem(
            watchlist_id=watchlist_id,
            symbol=data.symbol,
            exchange_type=exchange_type,
            market_type=data.market_type,
            notes=data.notes,
        )
        db.add(item)
        await db.flush()
        await db.refresh(item)
        return item

    @staticmethod
    async def remove_item(
        db: AsyncSession, user_id: UUID, watchlist_id: UUID, item_id: UUID
    ) -> None:
        await WatchlistService.get_watchlist(db, user_id, watchlist_id)
        result = await db.execute(
            select(WatchlistItem).where(
                WatchlistItem.id == item_id,
                WatchlistItem.watchlist_id == watchlist_id,
            )
        )
        item = result.scalar_one_or_none()
        if not item:
            raise NotFoundException("Watchlist item")
        await db.delete(item)

    @staticmethod
    async def get_all_watched_symbols(db: AsyncSession, user_id: UUID) -> list[str]:
        """Get all unique symbols across user's watchlists."""
        result = await db.execute(
            select(WatchlistItem.symbol)
            .join(Watchlist)
            .where(Watchlist.user_id == user_id)
            .distinct()
        )
        return [row[0] for row in result.all()]

    @staticmethod
    async def get_items_with_prices(
        db: AsyncSession, user_id: UUID
    ) -> list[WatchlistItemWithPrice]:
        """Return default watchlist items enriched with live market prices."""
        from app.modules.market.service import MarketService

        wl = await WatchlistService.get_or_create_default(db, user_id)
        items = wl.items
        if not items:
            return []

        # Separate crypto and stock symbols
        crypto_items = [i for i in items if i.market_type != "stock"]
        stock_items  = [i for i in items if i.market_type == "stock"]

        # Fetch prices concurrently
        price_map: dict[str, dict] = {}

        if crypto_items:
            try:
                crypto_syms = [i.symbol for i in crypto_items]
                tickers = await MarketService.get_tickers(crypto_syms)
                for t in tickers:
                    price_map[t["symbol"]] = t
            except Exception:
                pass

        if stock_items:
            try:
                stock_syms = [i.symbol for i in stock_items]
                tickers = await MarketService.get_stock_tickers(stock_syms)
                for t in tickers:
                    price_map[t["symbol"]] = t
            except Exception:
                pass

        result = []
        for item in items:
            ticker = price_map.get(item.symbol, {})
            result.append(WatchlistItemWithPrice(
                id=item.id,
                symbol=item.symbol,
                exchange_type=item.exchange_type,
                market_type=item.market_type,
                notes=item.notes,
                created_at=item.created_at,
                last=ticker.get("last"),
                change_24h=ticker.get("change_24h"),
                high=ticker.get("high"),
                low=ticker.get("low"),
                volume=ticker.get("volume"),
            ))

        return result
