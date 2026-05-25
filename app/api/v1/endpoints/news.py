"""
QuantAdvisor — News Endpoint
GET /news/signals           → Señales recientes filtradas por impacto
GET /news/signals/{id}      → Detalle de una señal
GET /news/market-context    → Contexto macro actual (BCRA + global)
"""
import logging
from typing import Optional
from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps.auth import get_current_user, require_feature
from app.core.database import get_db
from app.models.models import User
from app.services.news.news_engine import news_service
from app.services.market_data.market_data import market_data_service

router = APIRouter()
logger = logging.getLogger(__name__)


@router.get("/signals")
async def get_news_signals(
    hours: int = Query(default=24, ge=1, le=168),
    min_impact: str = Query(default="medium"),
    limit: int = Query(default=20, ge=1, le=100),
    current_user: User = Depends(get_current_user),
    _: None = Depends(require_feature("ai_news_engine")),
    db: AsyncSession = Depends(get_db),
):
    """
    Retorna señales de noticias recientes.
    Requiere Plan Pro+.
    """
    signals = await news_service.get_recent_signals(
        db=db,
        hours=hours,
        min_impact=min_impact,
        limit=limit,
    )

    return {
        "signals": [
            {
                "id": s.id,
                "source": s.source,
                "headline": s.headline,
                "sentiment_label": s.sentiment_label,
                "sentiment_score": s.sentiment_score,
                "impact_level": s.impact_level,
                "event_category": s.event_category,
                "affected_sectors": s.affected_sectors,
                "affected_tickers": s.affected_tickers,
                "published_at": s.published_at,
                "processed_at": s.processed_at,
            }
            for s in signals
        ],
        "total": len(signals),
        "disclaimer": "El análisis de sentimiento es un modelo estadístico con limitaciones. No constituye asesoramiento financiero.",
    }


@router.get("/market-context")
async def get_market_context(
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Contexto macro: BCRA + VIX + retornos de benchmark."""
    context = await market_data_service.get_macro_context()
    return {
        "context": context,
        "timestamp": "now",
        "sources": ["BCRA API", "Yahoo Finance"],
    }
