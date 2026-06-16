"""
QuantAdvisor — Market Data Endpoint
GET /market/quote/{ticker}       → Cotización en tiempo real
GET /market/history/{ticker}     → Histórico de precios
GET /market/fundamentals/{ticker}→ Datos fundamentales
GET /market/search               → Búsqueda de activos
GET /market/macro                → Dashboard macro AR + global
GET /market/universe             → Universo de activos disponibles
"""
import logging
from typing import Optional
from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps.auth import get_current_user, get_user_active_plan
from app.core.database import get_db
from app.core.feature_flags import get_plan_features
from app.models.models import User
from app.services.market_data.market_data import market_data_service, ETF_UNIVERSE, CEDEAR_UNIVERSE, BOND_SOVEREIGN_UNIVERSE, US_STOCK_UNIVERSE

router = APIRouter()
logger = logging.getLogger(__name__)


@router.get("/quote/{ticker}")
async def get_quote(
    ticker: str,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Cotización del activo. IOL para activos AR, yfinance para el resto."""
    ticker = ticker.upper()
    try:
        fundamentals = await market_data_service.get_fundamentals(ticker, "stock")
        return {
            "ticker": ticker,
            "name": fundamentals.get("name"),
            "last_price": fundamentals.get("current_price"),
            "currency": fundamentals.get("currency"),
            "market_cap": fundamentals.get("market_cap"),
            "beta": fundamentals.get("beta"),
            "dividend_yield": fundamentals.get("dividend_yield"),
            "source": "yfinance",
            "disclaimer": "Precios con posible delay de 15 min.",
        }
    except Exception as e:
        return {"ticker": ticker, "error": str(e)}


@router.get("/quote-live/{ticker}")
async def get_quote_live(
    ticker: str,
    asset_type: str = Query("us_equity"),
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Cotizacion normalizada con failover y metadata de calidad (provenance)."""
    return await market_data_service.get_quote(ticker.upper(), asset_type)


@router.get("/ohlc/{ticker}")
async def get_ohlc(
    ticker: str,
    asset_type: str = Query("us_equity"),
    days: int = Query(365, ge=30, le=2000),
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Serie OHLCV lista para TradingView Lightweight Charts (+ provenance)."""
    try:
        return await market_data_service.get_ohlc(ticker.upper(), asset_type, days=days)
    except Exception as e:  # noqa: BLE001
        return {"symbol": ticker.upper(), "candles": [], "volume": [],
                "provenance": None, "error": str(e)}


@router.get("/providers/health")
async def providers_health(
    current_user: User = Depends(get_current_user),
):
    """Estado de la capa de proveedores (failover / circuit breakers)."""
    try:
        return {"providers": market_data_service.router.health()}
    except Exception as e:  # noqa: BLE001
        return {"providers": [], "error": str(e)}


@router.get("/fundamentals/{ticker}")
async def get_fundamentals(
    ticker: str,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Datos fundamentales del activo."""
    ticker = ticker.upper()
    data = await market_data_service.get_fundamentals(ticker, "stock")
    return {"ticker": ticker, "data": data}


@router.get("/search")
async def search_assets(
    q: str = Query(min_length=1, max_length=10),
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Búsqueda de activos en el universo disponible."""
    q_upper = q.upper()
    all_tickers = ETF_UNIVERSE + US_STOCK_UNIVERSE + CEDEAR_UNIVERSE + BOND_SOVEREIGN_UNIVERSE

    matches = [t for t in all_tickers if q_upper in t][:20]
    return {"query": q, "results": matches, "total": len(matches)}


@router.get("/macro")
async def get_macro_dashboard(
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Dashboard macro: BCRA + VIX + benchmarks globales."""
    context = await market_data_service.get_macro_context()
    return {
        "macro": context,
        "sources": ["BCRA API (gratuita)", "Yahoo Finance"],
        "disclaimer": "Datos con posible delay. Fuentes públicas y gratuitas.",
    }


@router.get("/universe")
async def get_asset_universe(
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Retorna el universo de activos disponibles para el plan del usuario."""
    plan = await get_user_active_plan(current_user, db)
    features = get_plan_features(plan)

    universe = {
        "etfs": ETF_UNIVERSE,
        "us_stocks": US_STOCK_UNIVERSE,
        "cedears": CEDEAR_UNIVERSE if features.access_cedears else [],
        "bonds_sovereign": BOND_SOVEREIGN_UNIVERSE if features.access_bonds_sovereign else [],
        "bonds_corporate": [] if not features.access_bonds_corporate else ["YPF", "TLGD"],
    }

    return {
        "universe": universe,
        "plan": plan.value,
        "total_assets": sum(len(v) for v in universe.values()),
    }
