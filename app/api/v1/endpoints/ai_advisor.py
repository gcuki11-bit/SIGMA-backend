"""
QuantAdvisor — AI Advisor Endpoint
POST /ai/explain/portfolio      → Explicación de construcción de portfolio
POST /ai/explain/rebalance/{id} → Explicación de rebalanceo
POST /ai/news-impact            → Análisis de impacto de noticia
POST /ai/risk-report/{id}       → Reporte de riesgo ejecutivo
GET  /ai/token-status           → Tokens disponibles en la sesión actual
"""
import logging
from typing import Dict, Optional

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps.auth import get_current_user, get_user_active_plan
from app.core.database import get_db
from app.core.feature_flags import get_plan_features
from app.models.models import Portfolio, User
from app.services.ai.claude_service import TokenController, ai_service

router = APIRouter()
logger = logging.getLogger(__name__)
token_controller = TokenController()

DISCLAIMER = (
    "Las explicaciones generadas por IA son análisis educativos basados en modelos cuantitativos. "
    "No constituyen asesoramiento financiero. Consultá a un asesor financiero registrado."
)


# ─── Schemas ──────────────────────────────────────────────────────────────────

class PortfolioExplainRequest(BaseModel):
    portfolio_id: str


class NewsImpactRequest(BaseModel):
    news_signal_id: Optional[str] = None
    headline: str
    summary: Optional[str] = ""
    portfolio_id: str


# ─── Routes ──────────────────────────────────────────────────────────────────

@router.get("/token-status")
async def get_token_status(
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Cuántos tokens de Claude le quedan al usuario hoy."""
    plan = await get_user_active_plan(current_user, db)
    features = get_plan_features(plan)

    if features.ai_tokens_per_session == 0:
        return {
            "available": False,
            "tokens_limit": 0,
            "tokens_used": 0,
            "tokens_remaining": 0,
            "message": "AI Explanations requiere Plan Pro o superior.",
        }

    can_use, remaining = await token_controller.can_use_tokens(current_user.id, 1, db)
    session = await token_controller.get_or_create_session(current_user.id, db)

    return {
        "available": not session.is_exhausted,
        "tokens_limit": session.tokens_limit,
        "tokens_used": session.tokens_used,
        "tokens_remaining": remaining,
        "session_exhausted": session.is_exhausted,
        "expires_at": session.expires_at,
    }


@router.post("/explain/portfolio")
async def explain_portfolio(
    payload: PortfolioExplainRequest,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Genera explicación IA de la construcción del portfolio."""
    plan = await get_user_active_plan(current_user, db)
    features = get_plan_features(plan)

    if not features.ai_explanations:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="AI Explanations requiere Plan Pro o superior.",
        )

    result = await db.execute(
        select(Portfolio).where(
            Portfolio.id == payload.portfolio_id,
            Portfolio.user_id == current_user.id,
        )
    )
    portfolio = result.scalar_one_or_none()
    if not portfolio:
        raise HTTPException(status_code=404, detail="Portfolio no encontrado")

    # Obtener posiciones
    from app.models.models import Position, InvestorProfile
    positions_result = await db.execute(
        select(Position).where(Position.portfolio_id == portfolio.id)
    )
    positions = positions_result.scalars().all()
    weights = {p.ticker: p.weight_recommended for p in positions}

    profile_result = await db.execute(
        select(InvestorProfile).where(InvestorProfile.user_id == current_user.id)
    )
    profile = profile_result.scalar_one_or_none()

    ai_result = await ai_service.explain_portfolio_construction(
        user_id=current_user.id,
        db=db,
        portfolio_data={
            "weights": weights,
            "metrics": {
                "expected_return": portfolio.sharpe_ratio,  # Proxy temporal
                "expected_volatility": portfolio.volatility_annual,
                "sharpe_ratio": portfolio.sharpe_ratio,
                "beta": portfolio.beta,
                "var_95": portfolio.var_95,
            },
            "optimization_model": portfolio.optimization_model,
            "health_score": {"total": portfolio.health_score},
        },
        investor_profile={
            "risk_classification": profile.risk_classification if profile else "moderado",
            "max_beta": profile.max_beta if profile else 1.0,
            "max_drawdown_tolerance": profile.max_drawdown_tolerance if profile else 0.20,
            "time_horizon_years": profile.time_horizon_years if profile else 5,
        },
    )

    return {
        **ai_result,
        "disclaimer": DISCLAIMER,
    }


@router.post("/risk-report/{portfolio_id}")
async def generate_risk_report(
    portfolio_id: str,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Genera reporte ejecutivo de riesgo del portfolio."""
    plan = await get_user_active_plan(current_user, db)
    features = get_plan_features(plan)

    if not features.ai_explanations:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Requiere Plan Pro o superior.",
        )

    result = await db.execute(
        select(Portfolio).where(
            Portfolio.id == portfolio_id,
            Portfolio.user_id == current_user.id,
        )
    )
    portfolio = result.scalar_one_or_none()
    if not portfolio:
        raise HTTPException(status_code=404, detail="Portfolio no encontrado")

    ai_result = await ai_service.generate_risk_report(
        user_id=current_user.id,
        db=db,
        portfolio_metrics={
            "expected_return": 0.12,  # Se obtiene de posiciones en producción
            "expected_volatility": portfolio.volatility_annual,
            "sharpe_ratio": portfolio.sharpe_ratio,
            "sortino_ratio": portfolio.sortino_ratio,
            "beta": portfolio.beta,
            "alpha": portfolio.alpha,
            "var_95": portfolio.var_95,
            "cvar_95": portfolio.cvar_95,
            "max_drawdown_estimate": portfolio.max_drawdown,
            "health_score": {"total": portfolio.health_score},
        },
    )

    return {
        **ai_result,
        "disclaimer": DISCLAIMER,
    }


@router.post("/news-impact")
async def analyze_news_impact(
    payload: NewsImpactRequest,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Analiza el impacto de una noticia sobre el portfolio del usuario."""
    plan = await get_user_active_plan(current_user, db)
    features = get_plan_features(plan)

    if not features.ai_news_engine:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="AI News Engine requiere Plan Pro o superior.",
        )

    # Obtener exposición del portfolio
    from app.models.models import Position
    positions_result = await db.execute(
        select(Position).where(
            Position.portfolio_id == payload.portfolio_id
        )
    )
    positions = positions_result.scalars().all()
    portfolio_exposure = {p.ticker: p.weight_recommended for p in positions}

    ai_result = await ai_service.analyze_news_impact(
        user_id=current_user.id,
        db=db,
        news_headline=payload.headline,
        news_summary=payload.summary or "",
        sentiment_score=0.0,  # Se calcula en el news engine
        affected_sectors=[],
        affected_tickers=[],
        portfolio_exposure=portfolio_exposure,
    )

    return {
        **ai_result,
        "disclaimer": DISCLAIMER,
    }
