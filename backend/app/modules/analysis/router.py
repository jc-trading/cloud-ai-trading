"""
AI Analysis API routes.
"""

from uuid import UUID
from fastapi import APIRouter, Depends, Query

from app.dependencies import DB, require_permission
from app.modules.auth.models import User
from app.modules.analysis.schemas import (
    AnalysisRequest,
    AnalysisResponse,
    AnalysisSummary,
)
from app.modules.analysis.service import AnalysisService

router = APIRouter(prefix="/analysis", tags=["AI Analysis"])


@router.post("", response_model=AnalysisResponse, status_code=201)
async def run_analysis(
    data: AnalysisRequest,
    user: User = Depends(require_permission("ai_analysis")),
    db: DB = None,
):
    """Run AI analysis on a symbol (manual trigger)."""
    result = await AnalysisService.run_analysis(
        db,
        user.id,
        data.symbol,
        data.exchange_type,
        strategy_id=data.strategy_id,
    )
    return AnalysisResponse.model_validate(result)


@router.get("", response_model=list[AnalysisResponse])
async def list_analyses(
    symbol: str | None = None,
    limit: int = Query(default=50, ge=1, le=200),
    user: User = Depends(require_permission("ai_analysis")),
    db: DB = None,
):
    """Get analysis history."""
    analyses = await AnalysisService.get_analyses(db, user.id, symbol, limit)
    return [AnalysisResponse.model_validate(a) for a in analyses]


@router.get("/summary", response_model=AnalysisSummary)
async def analysis_summary(
    user: User = Depends(require_permission("ai_analysis")),
    db: DB = None,
):
    """Get AI analysis usage summary."""
    return await AnalysisService.get_summary(db, user.id)


@router.get("/latest/{symbol:path}", response_model=AnalysisResponse)
async def get_latest(
    symbol: str,
    user: User = Depends(require_permission("ai_analysis")),
    db: DB = None,
):
    """Get the latest analysis for a symbol."""
    from app.core.exceptions import NotFoundException
    analysis = await AnalysisService.get_latest_analysis(db, user.id, symbol)
    if not analysis:
        raise NotFoundException("Analysis")
    return AnalysisResponse.model_validate(analysis)


@router.get("/{analysis_id}", response_model=AnalysisResponse)
async def get_analysis(
    analysis_id: UUID,
    user: User = Depends(require_permission("ai_analysis")),
    db: DB = None,
):
    """Get a specific analysis result."""
    analysis = await AnalysisService.get_analysis(db, user.id, analysis_id)
    return AnalysisResponse.model_validate(analysis)
