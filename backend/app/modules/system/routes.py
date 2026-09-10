"""API routes for system monitoring."""

import asyncio
import logging

from fastapi import APIRouter, Depends, HTTPException

from app.dependencies import require_permission
from app.modules.system.metrics import SystemMetrics
from app.modules.system.schemas import BarStoreResponse

logger = logging.getLogger(__name__)

router = APIRouter(
    prefix="/system",
    tags=["system-monitoring"],
)

RequireSystemPermission = Depends(require_permission("manage_system"))


@router.get("/bar-store", response_model=BarStoreResponse)
async def get_bar_store(_current_user=RequireSystemPermission):
    """Size of the Parquet bar store — the one directory on this host that
    grows without bound. Walking the tree takes seconds, so the gauge itself is
    cached; this endpoint only surfaces the cached measurement."""
    result = await asyncio.to_thread(SystemMetrics.get_bar_store_metrics)
    if result is None:
        raise HTTPException(status_code=503, detail="bar store unavailable")
    return BarStoreResponse(human=SystemMetrics.format_bytes(result["bytes"]), **result)
