"""
QuantAdvisor — Factor & Risk Attribution
========================================
Descompone el retorno y el RIESGO de un portfolio en sus drivers:

  1. Exposicion a factores (betas) por regresion multifactor estilo Fama-French,
     usando ETFs proxy como retornos de factor (Market/Size/Value/Momentum/Quality).
  2. Contribucion de cada factor al retorno esperado.
  3. Descomposicion del riesgo total en: parte explicada por factores vs idiosincratica.
  4. Contribucion marginal de cada activo al riesgo (Euler / MCTR-PCTR).
  5. Exposicion por sector.

Esto es lo que un fondo mira para entender "de donde viene mi P&L y mi riesgo",
no solo cuanto rindio. Todo en numpy/statsmodels — sin dependencias de pago.

El caller provee los datos (retornos de activos, pesos, retornos de factores).
El modulo NO hace I/O: es puro calculo y testeable de forma aislada.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, List, Optional

import numpy as np
import pandas as pd

# Proxies de factores via ETFs (retornos del ETF ~ retorno del factor).
# El servicio de datos baja estos tickers para construir la matriz de factores.
FACTOR_PROXIES: Dict[str, str] = {
    "Market": "SPY",     # Mercado amplio US
    "Size": "IWM",       # Small caps (size)
    "Value": "VTV",      # Value
    "Growth": "VUG",     # Growth (para construir Value-minus-Growth si se desea)
    "Momentum": "MTUM",  # Momentum
    "Quality": "QUAL",   # Quality
    "LowVol": "USMV",    # Baja volatilidad
}

ANNUALIZATION = 252


@dataclass
class FactorExposure:
    factor: str
    beta: float
    t_stat: Optional[float]
    contribution_return: float   # beta * retorno medio anualizado del factor


@dataclass
class AssetRiskContribution:
    ticker: str
    weight: float
    mctr: float                  # contribucion marginal al riesgo (anualizada)
    pct_risk: float              # % del riesgo total del portfolio


@dataclass
class AttributionResult:
    annualized_vol: float
    factor_exposures: List[FactorExposure]
    r_squared: float
    risk_factor_pct: float       # % del riesgo explicado por factores
    risk_idiosyncratic_pct: float
    asset_risk: List[AssetRiskContribution]
    sector_exposure: Dict[str, float] = field(default_factory=dict)

    def to_dict(self) -> dict:
        return {
            "annualized_vol": _r(self.annualized_vol),
            "r_squared": _r(self.r_squared),
            "risk_factor_pct": _r(self.risk_factor_pct),
            "risk_idiosyncratic_pct": _r(self.risk_idiosyncratic_pct),
            "factor_exposures": [
                {
                    "factor": f.factor,
                    "beta": _r(f.beta),
                    "t_stat": _r(f.t_stat) if f.t_stat is not None else None,
                    "contribution_return": _r(f.contribution_return),
                }
                for f in self.factor_exposures
            ],
            "asset_risk": [
                {
                    "ticker": a.ticker,
                    "weight": _r(a.weight),
                    "mctr": _r(a.mctr),
                    "pct_risk": _r(a.pct_risk),
                }
                for a in self.asset_risk
            ],
            "sector_exposure": {k: _r(v) for k, v in self.sector_exposure.items()},
        }


def _r(x, nd: int = 4):
    try:
        if x is None or (isinstance(x, float) and (np.isnan(x) or np.isinf(x))):
            return None
        return round(float(x), nd)
    except (TypeError, ValueError):
        return None


# ─── Riesgo: contribuciones por activo (Euler decomposition) ──────────────────

def risk_contributions(
    asset_returns: pd.DataFrame,
    weights: Dict[str, float],
) -> tuple[float, List[AssetRiskContribution]]:
    """
    Descompone la vol del portfolio en contribuciones por activo.
    MCTR_i = w_i * (Sigma w)_i / sigma_p ; PCTR_i = MCTR_i / sigma_p.
    Retorna (vol_anualizada, lista de contribuciones).
    """
    cols = [c for c in asset_returns.columns if c in weights]
    if not cols:
        return 0.0, []
    rets = asset_returns[cols].dropna(how="all").fillna(0.0)
    w = np.array([weights[c] for c in cols], dtype=float)
    wsum = w.sum()
    if wsum > 0:
        w = w / wsum  # normalizar a 1

    cov = rets.cov().values * ANNUALIZATION  # covarianza anualizada
    port_var = float(w @ cov @ w)
    sigma_p = float(np.sqrt(port_var)) if port_var > 0 else 0.0
    if sigma_p == 0:
        return 0.0, []

    marginal = cov @ w                  # dSigma/dw
    contrib = w * marginal              # contribucion absoluta a la varianza
    mctr = contrib / sigma_p            # a vol
    pct = contrib / port_var           # % del riesgo

    out = [
        AssetRiskContribution(ticker=cols[i], weight=float(w[i]),
                              mctr=float(mctr[i]), pct_risk=float(pct[i]))
        for i in range(len(cols))
    ]
    out.sort(key=lambda a: a.pct_risk, reverse=True)
    return sigma_p, out


# ─── Factores: regresion multifactor ─────────────────────────────────────────

def factor_regression(
    portfolio_returns: pd.Series,
    factor_returns: pd.DataFrame,
) -> tuple[List[FactorExposure], float, float]:
    """
    Regresa los retornos del portfolio contra los retornos de los factores.
    Retorna (exposiciones, r2, vol_explicada_por_factores_anualizada).
    Usa statsmodels si esta disponible; si no, OLS via numpy.
    """
    df = pd.concat([portfolio_returns.rename("y"), factor_returns], axis=1).dropna()
    if len(df) < 30 or factor_returns.shape[1] == 0:
        return [], 0.0, 0.0

    y = df["y"].values
    factors = [c for c in factor_returns.columns if c in df.columns]
    X = df[factors].values
    Xc = np.column_stack([np.ones(len(X)), X])  # intercepto

    t_stats: Dict[str, float] = {}
    try:
        import statsmodels.api as sm
        model = sm.OLS(y, Xc).fit()
        coefs = model.params
        r2 = float(model.rsquared)
        for i, f in enumerate(factors):
            t_stats[f] = float(model.tvalues[i + 1])
    except Exception:
        beta, *_ = np.linalg.lstsq(Xc, y, rcond=None)
        coefs = beta
        yhat = Xc @ beta
        ss_res = float(((y - yhat) ** 2).sum())
        ss_tot = float(((y - y.mean()) ** 2).sum())
        r2 = 1 - ss_res / ss_tot if ss_tot > 0 else 0.0
        for f in factors:
            t_stats[f] = None  # type: ignore

    factor_means = df[factors].mean().values * ANNUALIZATION  # retorno medio anualizado
    exposures: List[FactorExposure] = []
    for i, f in enumerate(factors):
        beta = float(coefs[i + 1])
        exposures.append(FactorExposure(
            factor=f, beta=beta, t_stat=t_stats.get(f),
            contribution_return=beta * float(factor_means[i]),
        ))

    # Vol explicada por factores = sqrt(R2 * var_total) anualizada
    total_vol = float(portfolio_returns.std() * np.sqrt(ANNUALIZATION))
    explained_vol = float(np.sqrt(max(r2, 0.0)) * total_vol)
    return exposures, r2, explained_vol


# ─── Orquestador ──────────────────────────────────────────────────────────────

def attribute(
    asset_returns: pd.DataFrame,
    weights: Dict[str, float],
    factor_returns: Optional[pd.DataFrame] = None,
    sectors: Optional[Dict[str, str]] = None,
) -> AttributionResult:
    """
    Calcula la atribucion completa.
      asset_returns : DataFrame (index=fecha, columnas=tickers) de retornos diarios.
      weights       : pesos por ticker (se normalizan a 1).
      factor_returns: DataFrame de retornos de factores (proxies ETF). Opcional.
      sectors       : mapa ticker -> sector. Opcional.
    """
    # Retornos del portfolio
    cols = [c for c in asset_returns.columns if c in weights]
    w = np.array([weights[c] for c in cols], dtype=float)
    if w.sum() > 0:
        w = w / w.sum()
    port_ret = (asset_returns[cols].fillna(0.0) * w).sum(axis=1)

    sigma_p, asset_risk = risk_contributions(asset_returns, weights)

    exposures: List[FactorExposure] = []
    r2 = 0.0
    if factor_returns is not None and not factor_returns.empty:
        exposures, r2, _ = factor_regression(port_ret, factor_returns)

    risk_factor_pct = float(max(min(r2, 1.0), 0.0))
    risk_idio_pct = 1.0 - risk_factor_pct

    sector_exposure: Dict[str, float] = {}
    if sectors:
        for i, c in enumerate(cols):
            sec = sectors.get(c, "Other")
            sector_exposure[sec] = sector_exposure.get(sec, 0.0) + float(w[i])

    return AttributionResult(
        annualized_vol=sigma_p,
        factor_exposures=exposures,
        r_squared=r2,
        risk_factor_pct=risk_factor_pct,
        risk_idiosyncratic_pct=risk_idio_pct,
        asset_risk=asset_risk,
        sector_exposure=sector_exposure,
    )
