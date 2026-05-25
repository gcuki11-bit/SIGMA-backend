"""
QuantAdvisor — Portfolio Endpoints
POST /portfolios           → Crear portfolio
GET  /portfolios           → Listar portfolios del usuario
GET  /portfolios/{id}      → Detalle + métricas
POST /portfolios/{id}/optimize  → Ejecutar Quant Engine
POST /portfolios/{id}/rebalance → Solicitar rebalanceo
GET  /portfolios/{id}/health    → Health Score
"""
import logging
from typing import List, Optional
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps.auth import get_current_user, require_feature
from app.core.database import get_db
from app.core.feature_flags import PlanType, get_plan_features
from app.models.models import Portfolio, Position, InvestorProfile, User, RebalanceEvent
from app.quant.engine.pipeline import (
    QuantPipeline,
    InvestorConstraints,
    AssetData,
)
from app.services.market_data.market_data import market_data_service
from app.services.ai.claude_service import ai_service
from app.schemas.portfolio import (
    PortfolioCreate,
    PortfolioResponse,
    OptimizeRequest,
    OptimizationResponse,
    RebalanceResponse,
    HealthScoreResponse,
)

router = APIRouter()
logger = logging.getLogger(__name__)

DISCLAIMER = (
    "Los resultados son análisis cuantitativos educativos basados en modelos matemáticos. "
    "No constituyen asesoramiento financiero. Rentabilidades pasadas no garantizan resultados futuros. "
    "Consultá a un asesor financiero registrado antes de invertir."
)


# ─── Helpers ─────────────────────────────────────────────────────────────────

async def get_user_plan(user: User, db: AsyncSession) -> PlanType:
    """Obtiene el plan activo del usuario."""
    from sqlalchemy import select
    from app.models.models import Subscription
    result = await db.execute(
        select(Subscription)
        .where(
            Subscription.user_id == user.id,
            Subscription.status.in_(["active", "trialing"]),
        )
        .order_by(Subscription.created_at.desc())
        .limit(1)
    )
    sub = result.scalar_one_or_none()
    if sub is None:
        return PlanType.STARTER
    return PlanType(sub.plan_type)


async def check_portfolio_limit(user: User, db: AsyncSession, plan: PlanType) -> None:
    """Verifica que el usuario no supere el límite de portfolios de su plan."""
    features = get_plan_features(plan)
    result = await db.execute(
        select(Portfolio).where(
            Portfolio.user_id == user.id,
            Portfolio.is_active == True,
        )
    )
    count = len(result.scalars().all())
    if count >= features.max_portfolios:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=(
                f"Tu plan {plan.value} permite hasta {features.max_portfolios} portfolio(s). "
                "Upgrade tu plan para crear más."
            ),
        )


# ─── Routes ──────────────────────────────────────────────────────────────────

@router.post("", response_model=PortfolioResponse, status_code=status.HTTP_201_CREATED)
async def create_portfolio(
    payload: PortfolioCreate,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Crea un nuevo portfolio vacío."""
    plan = await get_user_plan(current_user, db)
    await check_portfolio_limit(current_user, db, plan)

    portfolio = Portfolio(
        user_id=current_user.id,
        name=payload.name,
        optimization_model=payload.optimization_model,
        simulated_capital_ars=payload.simulated_capital_ars,
        rebalance_frequency=payload.rebalance_frequency,
    )
    db.add(portfolio)
    await db.flush()
    await db.refresh(portfolio)

    logger.info(f"Portfolio created: {portfolio.id} for user {current_user.id}")
    return portfolio


@router.get("", response_model=List[PortfolioResponse])
async def list_portfolios(
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Lista todos los portfolios activos del usuario."""
    result = await db.execute(
        select(Portfolio)
        .where(Portfolio.user_id == current_user.id, Portfolio.is_active == True)
        .order_by(Portfolio.created_at.desc())
    )
    return result.scalars().all()


@router.get("/{portfolio_id}", response_model=PortfolioResponse)
async def get_portfolio(
    portfolio_id: str,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Detalle de un portfolio con posiciones y métricas."""
    result = await db.execute(
        select(Portfolio).where(
            Portfolio.id == portfolio_id,
            Portfolio.user_id == current_user.id,
        )
    )
    portfolio = result.scalar_one_or_none()
    if not portfolio:
        raise HTTPException(status_code=404, detail="Portfolio no encontrado")
    return portfolio


@router.post("/{portfolio_id}/optimize", response_model=OptimizationResponse)
async def optimize_portfolio(
    portfolio_id: str,
    payload: OptimizeRequest,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """
    Ejecuta el Quant Engine completo sobre el portfolio.
    Respeta los features del plan del usuario.
    """
    plan = await get_user_plan(current_user, db)
    features = get_plan_features(plan)

    # Verificar optimizer disponible para el plan
    optimizer_map = {
        "markowitz": features.optimizer_markowitz,
        "max_sharpe": features.optimizer_max_sharpe,
        "min_variance": features.optimizer_min_variance,
        "risk_parity": features.optimizer_risk_parity,
        "black_litterman": features.optimizer_black_litterman,
    }
    requested_model = payload.optimization_model or "markowitz"
    if not optimizer_map.get(requested_model, False):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=(
                f"El modelo '{requested_model}' no está disponible en tu plan {plan.value}. "
                "Upgrade para acceder a optimizadores avanzados."
            ),
        )

    # Obtener portfolio y perfil del inversor
    result = await db.execute(
        select(Portfolio).where(
            Portfolio.id == portfolio_id,
            Portfolio.user_id == current_user.id,
        )
    )
    portfolio = result.scalar_one_or_none()
    if not portfolio:
        raise HTTPException(status_code=404, detail="Portfolio no encontrado")

    result = await db.execute(
        select(InvestorProfile).where(InvestorProfile.user_id == current_user.id)
    )
    profile = result.scalar_one_or_none()
    if not profile:
        raise HTTPException(
            status_code=400,
            detail="Completá el cuestionario de perfil de inversor primero.",
        )

    # Construir constraints del usuario
    constraints = InvestorConstraints(
        max_beta=profile.max_beta or 1.5,
        max_drawdown_tolerance=profile.max_drawdown_tolerance or 0.20,
        max_volatility=profile.max_volatility or 0.25,
        max_single_asset_weight=profile.max_single_asset_weight or 0.20,
        max_sector_weight=profile.max_sector_weight or 0.35,
        max_assets=min(payload.max_assets or 15, features.max_assets_monitored),
        excluded_sectors=profile.excluded_sectors or [],
        excluded_countries=profile.excluded_countries or [],
        excluded_tickers=profile.excluded_tickers or [],
    )

    # Determinar tipos de activo según plan
    asset_types = ["etf", "stock", "cedear"]
    if features.access_bonds_sovereign:
        asset_types.append("bond_sovereign")
    if features.access_bonds_corporate:
        asset_types.append("bond_corporate")
    if features.access_fcis:
        asset_types.append("fci")

    # Obtener universo de tickers
    tickers = await market_data_service.build_asset_universe(asset_types, features)
    if payload.include_tickers:
        tickers = list(set(tickers + payload.include_tickers))
    if payload.exclude_tickers:
        tickers = [t for t in tickers if t not in payload.exclude_tickers]

    # Construir AssetData para cada ticker
    # (En producción: batch con cache Redis, aquí simplificado para legibilidad)
    logger.info(f"Building universe of {len(tickers)} assets for portfolio {portfolio_id}")
    universe: List[AssetData] = []

    for ticker in tickers[:100]:  # Limitar para no saturar la API
        try:
            # Determinar tipo de activo
            a_type = _classify_asset(ticker, asset_types)

            # Obtener precios históricos
            returns = await market_data_service.get_price_history(ticker, a_type, days=504)
            if returns.empty or len(returns) < 100:
                continue

            # Fundamentales
            fundamentals = await market_data_service.get_fundamentals(ticker, a_type)

            # Técnicos (necesitamos la serie de precios, no solo retornos)
            # Simplificado aquí — en producción es un paso separado cacheado
            asset = AssetData(
                ticker=ticker,
                name=fundamentals.get("name") or ticker,
                asset_type=a_type,
                returns=returns,
                sector=fundamentals.get("sector"),
                country=fundamentals.get("country"),
                roe=fundamentals.get("roe"),
                roic=fundamentals.get("roic"),
                ev_ebitda=fundamentals.get("ev_ebitda"),
                peg_ratio=fundamentals.get("peg_ratio"),
                debt_equity=fundamentals.get("debt_equity"),
                current_ratio=fundamentals.get("current_ratio"),
                net_margin=fundamentals.get("net_margin"),
                avg_daily_volume_usd=fundamentals.get("avg_volume"),
                beta=fundamentals.get("beta"),
            )
            universe.append(asset)

        except Exception as e:
            logger.warning(f"Error loading {ticker}: {e}")
            continue

    if len(universe) < constraints.min_assets:
        raise HTTPException(
            status_code=400,
            detail=f"Solo se pudieron cargar {len(universe)} activos con datos suficientes.",
        )

    # Ejecutar pipeline cuantitativo
    pipeline = QuantPipeline(
        constraints=constraints,
        optimization_model=requested_model,
    )
    quant_result = pipeline.run(universe)

    if "error" in quant_result:
        raise HTTPException(status_code=400, detail=quant_result["error"])

    opt_result = quant_result["result"]
    health = quant_result["health_score"]

    # Actualizar portfolio en DB
    portfolio.optimization_model = requested_model
    portfolio.sharpe_ratio = opt_result.sharpe_ratio
    portfolio.sortino_ratio = opt_result.sortino_ratio
    portfolio.max_drawdown = opt_result.max_drawdown_estimate
    portfolio.beta = opt_result.beta
    portfolio.var_95 = opt_result.var_95
    portfolio.cvar_95 = opt_result.cvar_95 if features.metrics_cvar else None
    portfolio.volatility_annual = opt_result.expected_volatility
    portfolio.health_score = health["total"]
    portfolio.health_breakdown = health["breakdown"]

    # Actualizar posiciones
    await db.execute(
        Position.__table__.delete().where(Position.portfolio_id == portfolio.id)
    )
    for ticker, weight in opt_result.weights.items():
        position = Position(
            portfolio_id=portfolio.id,
            ticker=ticker,
            weight_recommended=weight,
        )
        db.add(position)

    await db.flush()

    # Generar explicación IA (si el plan lo permite)
    ai_explanation = None
    if features.ai_explanations:
        ai_result = await ai_service.explain_portfolio_construction(
            user_id=current_user.id,
            db=db,
            portfolio_data={
                "weights": opt_result.weights,
                "metrics": {
                    "expected_return": opt_result.expected_return,
                    "expected_volatility": opt_result.expected_volatility,
                    "sharpe_ratio": opt_result.sharpe_ratio,
                    "beta": opt_result.beta,
                    "var_95": opt_result.var_95,
                },
                "optimization_model": requested_model,
                "health_score": health,
            },
            investor_profile={
                "risk_classification": profile.risk_classification,
                "max_beta": profile.max_beta,
                "max_drawdown_tolerance": profile.max_drawdown_tolerance,
                "time_horizon_years": profile.time_horizon_years,
            },
        )
        ai_explanation = ai_result.get("explanation")

    return OptimizationResponse(
        portfolio_id=portfolio.id,
        weights=opt_result.weights,
        metrics={
            "expected_return": opt_result.expected_return,
            "expected_volatility": opt_result.expected_volatility,
            "sharpe_ratio": opt_result.sharpe_ratio,
            "sortino_ratio": opt_result.sortino_ratio if features.metrics_sortino else None,
            "max_drawdown": opt_result.max_drawdown_estimate,
            "beta": opt_result.beta,
            "var_95": opt_result.var_95 if features.metrics_var else None,
            "cvar_95": opt_result.cvar_95 if features.metrics_cvar else None,
        },
        health_score=health if True else None,
        optimization_model=requested_model,
        assets_used=quant_result["assets_used"],
        rejected_assets=quant_result["rejected_assets"][:20],
        ai_explanation=ai_explanation,
        disclaimer=DISCLAIMER,
    )


@router.get("/{portfolio_id}/health", response_model=HealthScoreResponse)
async def get_health_score(
    portfolio_id: str,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Retorna el AI Portfolio Health Score del portfolio."""
    result = await db.execute(
        select(Portfolio).where(
            Portfolio.id == portfolio_id,
            Portfolio.user_id == current_user.id,
        )
    )
    portfolio = result.scalar_one_or_none()
    if not portfolio:
        raise HTTPException(status_code=404, detail="Portfolio no encontrado")

    if portfolio.health_score is None:
        raise HTTPException(
            status_code=400,
            detail="Optimizá el portfolio primero para obtener el Health Score.",
        )

    return HealthScoreResponse(
        portfolio_id=portfolio.id,
        total=portfolio.health_score,
        breakdown=portfolio.health_breakdown or {},
        label=_health_label(portfolio.health_score),
    )


# ─── Helpers ─────────────────────────────────────────────────────────────────

def _classify_asset(ticker: str, available_types: List[str]) -> str:
    """Clasifica un ticker en su tipo de activo."""
    from app.services.market_data.market_data import (
        ETF_UNIVERSE, CEDEAR_UNIVERSE, BOND_SOVEREIGN_UNIVERSE, BOND_CORPORATE_UNIVERSE
    )
    if ticker in ETF_UNIVERSE:
        return "etf"
    if ticker in BOND_SOVEREIGN_UNIVERSE:
        return "bond_sovereign"
    if ticker in BOND_CORPORATE_UNIVERSE:
        return "bond_corporate"
    if ticker in CEDEAR_UNIVERSE:
        return "cedear"
    return "stock"


def _health_label(score: int) -> str:
    if score >= 85:
        return "Excelente"
    elif score >= 70:
        return "Bueno"
    elif score >= 55:
        return "Moderado"
    elif score >= 40:
        return "Débil"
    return "Crítico"
