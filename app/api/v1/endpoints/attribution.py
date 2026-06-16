"""
QuantAdvisor — Factor & Risk Attribution Endpoint
POST /attribution/analyze  → descompone retorno y riesgo del portfolio por factores.
"""
import asyncio
import logging
from typing import Dict, List, Optional

import pandas as pd
from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps.auth import get_current_user
from app.core.database import get_db
from app.models.models import User
from app.quant.engine.attribution import FACTOR_PROXIES, attribute
from app.services.market_data.market_data import market_data_service

router = APIRouter()
logger = logging.getLogger(__name__)


class Holding(BaseModel):
    ticker: str
    asset_type: str = "us_equity"
    weight: float = Field(gt=0)


class AttributionRequest(BaseModel):
    holdings: List[Holding]
    days: int = Field(504, ge=60, le=2000)
    factors: Optional[List[str]] = None   # subset de FACTOR_PROXIES; None = default


DEFAULT_FACTORS = ["Market", "Size", "Value", "Momentum", "Quality"]


async def _returns(ticker: str, asset_type: str, days: int) -> tuple[str, pd.Series]:
    try:
        s = await market_data_service.get_price_history(ticker, asset_type, days=days)
        return ticker, s
    except Exception as e:  # noqa: BLE001
        logger.warning("attribution: sin retornos para %s: %s", ticker, e)
        return ticker, pd.Series(dtype=float)


@router.post("/analyze")
async def analyze_attribution(
    req: AttributionRequest,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Factor + risk attribution del portfolio enviado."""
    weights = {h.ticker.upper(): h.weight for h in req.holdings}

    # 1) Retornos de los activos (en paralelo)
    asset_tasks = [_returns(h.ticker.upper(), h.asset_type, req.days) for h in req.holdings]

    # 2) Retornos de los factores proxy (ETFs)
    factor_names = [f for f in (req.factors or DEFAULT_FACTORS) if f in FACTOR_PROXIES]
    factor_tasks = [_returns(FACTOR_PROXIES[f], "etf", req.days) for f in factor_names]

    results = await asyncio.gather(*asset_tasks, *factor_tasks)
    asset_results = results[: len(asset_tasks)]
    factor_results = results[len(asset_tasks):]

    asset_returns = pd.DataFrame({t: s for t, s in asset_results if not s.empty})
    if asset_returns.empty:
        return {"error": "No se pudieron obtener retornos para ningún activo del portfolio."}

    factor_returns = pd.DataFrame()
    proxy_to_name = {FACTOR_PROXIES[n]: n for n in factor_names}
    fr = {proxy_to_name[t]: s for t, s in factor_results if not s.empty and t in proxy_to_name}
    if fr:
        factor_returns = pd.DataFrame(fr)

    # 3) Sectores (best-effort, no bloqueante)
    sectors: Dict[str, str] = {}
    try:
        fund_tasks = [
            market_data_service.get_fundamentals(h.ticker.upper(), h.asset_type)
            for h in req.holdings
        ]
        funds = await asyncio.gather(*fund_tasks, return_exceptions=True)
        for h, f in zip(req.holdings, funds):
            if isinstance(f, dict) and f.get("sector"):
                sectors[h.ticker.upper()] = f["sector"]
    except Exception:  # noqa: BLE001
        pass

    result = attribute(asset_returns, weights, factor_returns=factor_returns, sectors=sectors)
    out = result.to_dict()
    out["meta"] = {
        "assets_used": list(asset_returns.columns),
        "factors_used": list(factor_returns.columns) if not factor_returns.empty else [],
        "observations": int(len(asset_returns)),
        "days_requested": req.days,
    }
    out["disclaimer"] = (
        "Factores aproximados con ETFs proxy. Análisis educativo, no constituye "
        "asesoramiento de inversión."
    )
    return out
