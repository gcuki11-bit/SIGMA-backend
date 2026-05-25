"""
QuantAdvisor — Quant Engine Core
Pipeline completo de 5 etapas: fundamental → técnico → liquidez → covarianzas → optimización.

Optimizadores implementados:
  - Markowitz (Mean-Variance)
  - Maximum Sharpe Ratio
  - Minimum Variance
  - Risk Parity
  - Black-Litterman (Elite)
  - CVaR Optimization (Elite)
"""
import logging
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple

import numpy as np
import pandas as pd
from scipy.optimize import minimize
from scipy.stats import norm

logger = logging.getLogger(__name__)


# ─── Data Structures ─────────────────────────────────────────────────────────

@dataclass
class AssetData:
    ticker: str
    name: str
    asset_type: str
    returns: pd.Series            # Serie temporal de retornos diarios
    sector: Optional[str] = None
    country: Optional[str] = None
    # Fundamentales
    roe: Optional[float] = None
    roic: Optional[float] = None
    ev_ebitda: Optional[float] = None
    peg_ratio: Optional[float] = None
    debt_equity: Optional[float] = None
    current_ratio: Optional[float] = None
    fcf_growth: Optional[float] = None
    revenue_growth: Optional[float] = None
    net_margin: Optional[float] = None
    # Técnicos
    rsi_14: Optional[float] = None
    macd_signal: Optional[float] = None
    above_sma_200: Optional[bool] = None
    above_ema_50: Optional[bool] = None
    momentum_3m: Optional[float] = None
    atr_percentile: Optional[float] = None
    # Liquidez
    avg_daily_volume_usd: Optional[float] = None
    bid_ask_spread_pct: Optional[float] = None
    # Para bonos
    ytm: Optional[float] = None
    duration: Optional[float] = None
    credit_rating: Optional[str] = None


@dataclass
class InvestorConstraints:
    """Restricciones del inversor que el optimizador debe respetar."""
    max_beta: float = 1.5
    max_drawdown_tolerance: float = 0.20      # 20%
    max_volatility: float = 0.25             # 25% anual
    max_single_asset_weight: float = 0.20    # 20% por activo
    max_sector_weight: float = 0.35          # 35% por sector
    min_assets: int = 5
    max_assets: int = 20
    excluded_sectors: List[str] = field(default_factory=list)
    excluded_countries: List[str] = field(default_factory=list)
    excluded_tickers: List[str] = field(default_factory=list)
    preferred_sectors: List[str] = field(default_factory=list)
    risk_free_rate: float = 0.05             # 5% anual (referencia)
    target_return: Optional[float] = None


@dataclass
class OptimizationResult:
    weights: Dict[str, float]                 # ticker → peso
    expected_return: float
    expected_volatility: float
    sharpe_ratio: float
    sortino_ratio: float
    max_drawdown_estimate: float
    beta: float
    var_95: float
    cvar_95: float
    optimization_model: str
    feasible: bool = True
    warnings: List[str] = field(default_factory=list)


# ─── Stage 1: Fundamental Filter ─────────────────────────────────────────────

class FundamentalFilter:
    """
    Elimina activos con fundamentales débiles.
    Scores calculados por percentil dentro del universo.
    """

    STOCK_MIN_THRESHOLDS = {
        "roe": 0.08,             # ROE mínimo 8%
        "current_ratio": 1.0,   # Liquidez corriente mínima 1x
        "net_margin": 0.0,       # Sin pérdidas netas
    }

    STOCK_REJECT_THRESHOLDS = {
        "debt_equity": 5.0,      # D/E > 5 → rechazado
        "peg_ratio": 4.0,        # PEG > 4 → caro sin crecimiento
    }

    def filter(self, assets: List[AssetData]) -> Tuple[List[AssetData], List[str]]:
        """
        Retorna: (activos_aprobados, razones_descarte)
        """
        approved = []
        rejected_reasons = []

        for asset in assets:
            # Bonos y ETFs: filtro fundamental diferente
            if asset.asset_type in ("etf", "fci", "caution"):
                approved.append(asset)
                continue

            if asset.asset_type in ("bond_sovereign", "bond_corporate"):
                if self._filter_bond(asset):
                    approved.append(asset)
                else:
                    rejected_reasons.append(f"{asset.ticker}: bono rechazado por riesgo")
                continue

            # Acciones y Cedears
            reason = self._filter_stock(asset)
            if reason is None:
                approved.append(asset)
            else:
                rejected_reasons.append(f"{asset.ticker}: {reason}")

        logger.info(
            f"Fundamental filter: {len(approved)}/{len(assets)} approved, "
            f"{len(rejected_reasons)} rejected"
        )
        return approved, rejected_reasons

    def _filter_stock(self, asset: AssetData) -> Optional[str]:
        """None = aprobado. String = razón de rechazo."""
        if asset.roe is not None and asset.roe < self.STOCK_MIN_THRESHOLDS["roe"]:
            return f"ROE {asset.roe:.1%} < mínimo 8%"
        if asset.current_ratio is not None and asset.current_ratio < 1.0:
            return f"Current ratio {asset.current_ratio:.2f} < 1.0"
        if asset.net_margin is not None and asset.net_margin < 0:
            return f"Net margin negativo ({asset.net_margin:.1%})"
        if asset.debt_equity is not None and asset.debt_equity > 5.0:
            return f"D/E excesivo: {asset.debt_equity:.1f}x"
        return None

    def _filter_bond(self, asset: AssetData) -> bool:
        if asset.ytm is not None and asset.ytm < 0:
            return False    # YTM negativo — rechazado
        return True


# ─── Stage 2: Technical Filter ───────────────────────────────────────────────

class TechnicalFilter:
    """
    Filtra activos en tendencias bajistas extremas o volatilidad anormal.
    No elimina activos rebotando — discrimina momentum persistentemente negativo.
    """

    def filter(
        self,
        assets: List[AssetData],
        conservative: bool = False
    ) -> Tuple[List[AssetData], List[str]]:
        approved = []
        rejected_reasons = []

        for asset in assets:
            # ETFs/FCIs: filtro técnico más suave
            if asset.asset_type in ("etf", "fci", "caution"):
                if self._basic_trend_check(asset):
                    approved.append(asset)
                else:
                    rejected_reasons.append(f"{asset.ticker}: tendencia bajista severa")
                continue

            reason = self._filter_asset(asset, conservative)
            if reason is None:
                approved.append(asset)
            else:
                rejected_reasons.append(f"{asset.ticker}: {reason}")

        logger.info(
            f"Technical filter: {len(approved)}/{len(assets)} approved"
        )
        return approved, rejected_reasons

    def _filter_asset(self, asset: AssetData, conservative: bool) -> Optional[str]:
        # RSI extremo: >85 sobrecomprado, <15 caída libre
        if asset.rsi_14 is not None:
            if asset.rsi_14 > 85:
                return f"RSI sobrecomprado: {asset.rsi_14:.1f}"
            if conservative and asset.rsi_14 < 15:
                return f"RSI en caída libre: {asset.rsi_14:.1f}"

        # Precio debajo de SMA200 (tendencia bajista de largo plazo)
        if conservative and asset.above_sma_200 is False:
            return "Precio debajo de SMA 200 (tendencia bajista)"

        # Volatilidad en percentil extremo (> p90 del universo)
        if asset.atr_percentile is not None and asset.atr_percentile > 0.92:
            return f"Volatilidad extrema (percentil {asset.atr_percentile:.0%})"

        return None

    def _basic_trend_check(self, asset: AssetData) -> bool:
        if asset.above_sma_200 is False and asset.rsi_14 is not None and asset.rsi_14 < 25:
            return False
        return True


# ─── Stage 3: Liquidity Filter ───────────────────────────────────────────────

class LiquidityFilter:
    """
    Asegura que los activos tengan suficiente liquidez para ser operables.
    Crucial para el mercado argentino donde algunos Cedears tienen bajo volumen.
    """

    MIN_ADV_USD = {
        "etf": 1_000_000,       # $1M USD/día mínimo
        "stock": 500_000,
        "cedear": 100_000,       # ARS equivalente — más permisivo
        "bond_sovereign": 50_000,
        "bond_corporate": 20_000,
        "fci": 0,                # Sin mínimo para FCIs
        "caution": 0,
    }

    MAX_SPREAD_PCT = {
        "etf": 0.005,    # 0.5%
        "stock": 0.01,   # 1%
        "cedear": 0.03,  # 3% — mercado local más ilíquido
        "bond_sovereign": 0.02,
        "bond_corporate": 0.05,
    }

    def filter(self, assets: List[AssetData]) -> Tuple[List[AssetData], List[str]]:
        approved = []
        rejected_reasons = []

        for asset in assets:
            min_adv = self.MIN_ADV_USD.get(asset.asset_type, 0)
            max_spread = self.MAX_SPREAD_PCT.get(asset.asset_type, 0.10)

            if (
                asset.avg_daily_volume_usd is not None
                and asset.avg_daily_volume_usd < min_adv
            ):
                rejected_reasons.append(
                    f"{asset.ticker}: volumen insuficiente "
                    f"(${asset.avg_daily_volume_usd:,.0f} < ${min_adv:,.0f})"
                )
                continue

            if (
                asset.bid_ask_spread_pct is not None
                and asset.bid_ask_spread_pct > max_spread
            ):
                rejected_reasons.append(
                    f"{asset.ticker}: spread excesivo "
                    f"({asset.bid_ask_spread_pct:.1%} > {max_spread:.1%})"
                )
                continue

            approved.append(asset)

        return approved, rejected_reasons


# ─── Stage 4: Covariance Matrix ──────────────────────────────────────────────

class CovarianceEstimator:
    """
    Estimación robusta de la matriz de covarianzas.
    Usa Ledoit-Wolf shrinkage para reducir el error de estimación
    con muestras finitas (problema clásico en portfolio optimization).
    """

    def __init__(self, min_history_days: int = 252):
        self.min_history_days = min_history_days

    def estimate(self, assets: List[AssetData]) -> Tuple[np.ndarray, List[str]]:
        """
        Retorna: (matriz_covarianzas_anualizada, tickers_válidos)
        """
        returns_dict = {}
        for asset in assets:
            if len(asset.returns) >= self.min_history_days:
                returns_dict[asset.ticker] = asset.returns
            else:
                logger.warning(
                    f"{asset.ticker}: solo {len(asset.returns)} días de historia "
                    f"(mínimo {self.min_history_days})"
                )

        if len(returns_dict) < 2:
            raise ValueError("Insuficiente historia para estimar covarianzas")

        returns_df = pd.DataFrame(returns_dict).dropna(how="any")

        # Ledoit-Wolf shrinkage
        cov_matrix = self._ledoit_wolf_shrinkage(returns_df)

        # Anualizar (252 días hábiles)
        cov_annual = cov_matrix * 252

        return cov_annual, list(returns_dict.keys())

    def _ledoit_wolf_shrinkage(self, returns: pd.DataFrame) -> np.ndarray:
        """
        Implementación de Ledoit-Wolf (2004).
        Shrinks hacia la identidad escalada para mejorar condicionamiento.
        """
        T, n = returns.shape
        sample_cov = returns.cov().values

        # Estimador target: identidad escalada por varianza media
        mu = np.trace(sample_cov) / n
        target = mu * np.eye(n)

        # Intensidad de shrinkage óptima (Oracle approximating shrinkage)
        delta = min(1.0, max(0.0, (n / T) ** 0.5))

        shrunk_cov = (1 - delta) * sample_cov + delta * target
        return shrunk_cov


# ─── Stage 5: Optimizers ─────────────────────────────────────────────────────

class PortfolioOptimizer:
    """
    Implementa múltiples modelos de optimización.
    Todos respetan las constraints del inversor.
    """

    def __init__(self, constraints: InvestorConstraints):
        self.constraints = constraints

    def optimize(
        self,
        expected_returns: np.ndarray,
        cov_matrix: np.ndarray,
        tickers: List[str],
        model: str = "max_sharpe",
        sector_map: Optional[Dict[str, str]] = None,
    ) -> OptimizationResult:
        """
        model: markowitz | max_sharpe | min_variance | risk_parity
        """
        n = len(tickers)

        if n < self.constraints.min_assets:
            raise ValueError(
                f"Insuficientes activos ({n}) para optimizar. "
                f"Mínimo: {self.constraints.min_assets}"
            )

        # Constraints scipy
        scipy_constraints = self._build_scipy_constraints(
            expected_returns, cov_matrix, tickers, sector_map
        )
        bounds = [(0.0, self.constraints.max_single_asset_weight)] * n

        if model == "max_sharpe":
            weights = self._max_sharpe(expected_returns, cov_matrix, bounds, scipy_constraints)
        elif model == "min_variance":
            weights = self._min_variance(cov_matrix, bounds, scipy_constraints)
        elif model == "risk_parity":
            weights = self._risk_parity(cov_matrix, n)
        elif model == "markowitz":
            weights = self._markowitz_efficient(
                expected_returns, cov_matrix, bounds, scipy_constraints
            )
        else:
            weights = self._max_sharpe(expected_returns, cov_matrix, bounds, scipy_constraints)

        # Calcular métricas del portfolio resultante
        result = self._compute_metrics(weights, expected_returns, cov_matrix, tickers, model)
        return result

    def _max_sharpe(
        self,
        mu: np.ndarray,
        sigma: np.ndarray,
        bounds,
        constraints,
    ) -> np.ndarray:
        n = len(mu)
        rf = self.constraints.risk_free_rate / 252  # diario

        def neg_sharpe(w):
            port_ret = np.dot(w, mu)
            port_vol = np.sqrt(w @ sigma @ w)
            if port_vol < 1e-10:
                return 0
            return -(port_ret - rf) / port_vol

        w0 = np.ones(n) / n
        result = minimize(
            neg_sharpe,
            w0,
            method="SLSQP",
            bounds=bounds,
            constraints=constraints,
            options={"maxiter": 1000, "ftol": 1e-9},
        )
        w = result.x
        w = np.maximum(w, 0)
        w /= w.sum()
        return w

    def _min_variance(self, sigma: np.ndarray, bounds, constraints) -> np.ndarray:
        n = sigma.shape[0]

        def portfolio_var(w):
            return w @ sigma @ w

        w0 = np.ones(n) / n
        result = minimize(
            portfolio_var,
            w0,
            method="SLSQP",
            bounds=bounds,
            constraints=constraints,
            options={"maxiter": 1000, "ftol": 1e-9},
        )
        w = np.maximum(result.x, 0)
        w /= w.sum()
        return w

    def _markowitz_efficient(
        self,
        mu: np.ndarray,
        sigma: np.ndarray,
        bounds,
        constraints,
    ) -> np.ndarray:
        """
        Encuentra el punto sobre la frontera eficiente que maximiza retorno
        dado el target del inversor, o usa max Sharpe como fallback.
        """
        if self.constraints.target_return is not None:
            target_daily = self.constraints.target_return / 252

            extra = [{"type": "ineq", "fun": lambda w: np.dot(w, mu) - target_daily}]
            all_constraints = list(constraints) + extra

            n = len(mu)
            w0 = np.ones(n) / n

            def portfolio_var(w):
                return w @ sigma @ w

            result = minimize(
                portfolio_var, w0,
                method="SLSQP",
                bounds=bounds,
                constraints=all_constraints,
                options={"maxiter": 1000},
            )
            if result.success:
                w = np.maximum(result.x, 0)
                w /= w.sum()
                return w

        return self._max_sharpe(mu, sigma, bounds, constraints)

    def _risk_parity(self, sigma: np.ndarray, n: int) -> np.ndarray:
        """
        Equal Risk Contribution (ERC).
        Cada activo contribuye igual al riesgo total del portfolio.
        """
        w0 = np.ones(n) / n

        def risk_parity_objective(w):
            port_var = w @ sigma @ w
            marginal_risk = sigma @ w
            risk_contributions = w * marginal_risk / port_var
            target = np.ones(n) / n
            return np.sum((risk_contributions - target) ** 2)

        bounds = [(0.01, 0.40)] * n
        constraints = [{"type": "eq", "fun": lambda w: np.sum(w) - 1}]

        result = minimize(
            risk_parity_objective,
            w0,
            method="SLSQP",
            bounds=bounds,
            constraints=constraints,
            options={"maxiter": 2000, "ftol": 1e-10},
        )
        w = np.maximum(result.x, 0)
        w /= w.sum()
        return w

    def _build_scipy_constraints(
        self,
        mu: np.ndarray,
        sigma: np.ndarray,
        tickers: List[str],
        sector_map: Optional[Dict[str, str]],
    ) -> list:
        c = self.constraints
        constraints = [
            # Suma de pesos = 1
            {"type": "eq", "fun": lambda w: np.sum(w) - 1},
        ]

        # Volatilidad máxima (anualizada)
        max_vol_daily = c.max_volatility / np.sqrt(252)
        constraints.append({
            "type": "ineq",
            "fun": lambda w: max_vol_daily**2 - (w @ sigma @ w),
        })

        # Restricciones sectoriales
        if sector_map:
            sectors = list(set(sector_map.values()))
            for sector in sectors:
                sector_indices = [
                    i for i, t in enumerate(tickers)
                    if sector_map.get(t) == sector
                ]
                if sector_indices:
                    def sector_constraint(w, idx=sector_indices):
                        return c.max_sector_weight - sum(w[i] for i in idx)
                    constraints.append({"type": "ineq", "fun": sector_constraint})

        return constraints

    def _compute_metrics(
        self,
        weights: np.ndarray,
        mu: np.ndarray,
        sigma: np.ndarray,
        tickers: List[str],
        model: str,
    ) -> OptimizationResult:
        rf_daily = self.constraints.risk_free_rate / 252

        port_return_daily = np.dot(weights, mu)
        port_var = weights @ sigma @ weights
        port_vol_daily = np.sqrt(port_var)

        # Anualizar
        port_return_annual = port_return_daily * 252
        port_vol_annual = port_vol_daily * np.sqrt(252)

        # Sharpe
        sharpe = (port_return_daily - rf_daily) / port_vol_daily * np.sqrt(252) if port_vol_daily > 0 else 0

        # Sortino (solo retornos negativos)
        downside_var = np.mean(np.minimum(mu - rf_daily, 0) ** 2)
        sortino = (port_return_daily - rf_daily) / np.sqrt(downside_var) * np.sqrt(252) if downside_var > 0 else 0

        # VaR y CVaR al 95% (paramétrico, distribución normal)
        var_95 = norm.ppf(0.05) * port_vol_daily
        cvar_95 = -port_vol_daily * norm.pdf(norm.ppf(0.05)) / 0.05

        # Estimación de Max Drawdown (Calmar simplificado)
        max_dd_estimate = port_vol_annual * np.sqrt(2 * np.log(252))

        # Beta estimada (vs SPY — simplificado, se mejora con regresión real)
        beta_estimate = port_vol_annual / 0.20  # Asume SPY vol = 20%

        weights_dict = {
            ticker: round(float(w), 6)
            for ticker, w in zip(tickers, weights)
            if w > 0.001
        }

        return OptimizationResult(
            weights=weights_dict,
            expected_return=round(float(port_return_annual), 4),
            expected_volatility=round(float(port_vol_annual), 4),
            sharpe_ratio=round(float(sharpe), 4),
            sortino_ratio=round(float(sortino), 4),
            max_drawdown_estimate=round(float(max_dd_estimate), 4),
            beta=round(float(beta_estimate), 4),
            var_95=round(float(var_95), 4),
            cvar_95=round(float(cvar_95), 4),
            optimization_model=model,
        )


# ─── Black-Litterman (Elite) ─────────────────────────────────────────────────

class BlackLittermanOptimizer:
    """
    Black-Litterman (1990/1992).
    Combina el prior del mercado (equilibrio CAPM) con las views del analista/IA.
    Solo disponible en plan Elite.
    """

    def __init__(self, risk_aversion: float = 3.0, tau: float = 0.05):
        self.risk_aversion = risk_aversion
        self.tau = tau

    def optimize(
        self,
        cov_matrix: np.ndarray,
        market_weights: np.ndarray,
        views_P: Optional[np.ndarray],
        views_Q: Optional[np.ndarray],
        views_omega: Optional[np.ndarray],
        tickers: List[str],
        constraints: InvestorConstraints,
    ) -> OptimizationResult:
        """
        cov_matrix: matriz de covarianzas (anualizada)
        market_weights: pesos del portfolio de mercado (ej: capitalización)
        views_P: matriz de views (k x n) donde k = número de views
        views_Q: vector de retornos esperados por las views (k,)
        views_omega: diagonal de la matriz de incertidumbre de las views
        """
        n = len(tickers)

        # Prior: retornos de equilibrio CAPM implícitos
        pi = self.risk_aversion * cov_matrix @ market_weights

        if views_P is None or views_Q is None:
            # Sin views: usar equilibrio puro
            mu_bl = pi
        else:
            # Combinar prior + views (fórmula BL)
            tau_sigma = self.tau * cov_matrix
            omega = np.diag(views_omega) if views_omega is not None else np.eye(len(views_Q)) * 0.05

            A = np.linalg.inv(np.linalg.inv(tau_sigma) + views_P.T @ np.linalg.inv(omega) @ views_P)
            b = np.linalg.inv(tau_sigma) @ pi + views_P.T @ np.linalg.inv(omega) @ views_Q
            mu_bl = A @ b

        # Optimizar con los retornos BL
        optimizer = PortfolioOptimizer(constraints)
        return optimizer.optimize(
            expected_returns=mu_bl / 252,   # Convertir a diario
            cov_matrix=cov_matrix / 252,
            tickers=tickers,
            model="black_litterman",
        )


# ─── Health Score Calculator ─────────────────────────────────────────────────

class HealthScoreCalculator:
    """
    Calcula el AI Portfolio Health Score (0-100).
    Componentes: diversification, macro_exposure, concentration_risk,
                 volatility_risk, defensive_rating.
    """

    WEIGHTS = {
        "diversification": 0.25,
        "concentration": 0.20,
        "volatility": 0.20,
        "macro_exposure": 0.20,
        "defensive": 0.15,
    }

    def calculate(
        self,
        result: OptimizationResult,
        assets: List[AssetData],
        weights_dict: Dict[str, float],
    ) -> Dict:
        scores = {}

        # 1. Diversification Score: penaliza concentración por sector/país
        sector_weights: Dict[str, float] = {}
        for asset in assets:
            w = weights_dict.get(asset.ticker, 0)
            if asset.sector and w > 0:
                sector_weights[asset.sector] = sector_weights.get(asset.sector, 0) + w
        max_sector = max(sector_weights.values()) if sector_weights else 1.0
        scores["diversification"] = max(0, 100 - (max_sector - 0.20) * 300)

        # 2. Concentration Score: Herfindahl-Hirschman Index (HHI)
        w_values = list(weights_dict.values())
        hhi = sum(w**2 for w in w_values)
        # HHI de 1/n (máxima diversificación) a 1 (concentrado total)
        n = len(w_values)
        hhi_normalized = (hhi - 1/n) / (1 - 1/n) if n > 1 else 1
        scores["concentration"] = max(0, round(100 * (1 - hhi_normalized), 1))

        # 3. Volatility Score: penaliza si supera umbrales por clasificación
        vol = result.expected_volatility
        if vol < 0.10:
            scores["volatility"] = 100
        elif vol < 0.15:
            scores["volatility"] = 85
        elif vol < 0.20:
            scores["volatility"] = 70
        elif vol < 0.30:
            scores["volatility"] = 50
        else:
            scores["volatility"] = max(0, round(100 - (vol - 0.30) * 200))

        # 4. Macro Exposure: porcentaje en activos defensivos vs cíclicos
        defensive_sectors = {"utilities", "consumer_staples", "healthcare", "fixed_income"}
        defensive_weight = sum(
            weights_dict.get(a.ticker, 0)
            for a in assets
            if (a.sector or "").lower().replace(" ", "_") in defensive_sectors
        )
        scores["macro_exposure"] = min(100, round(50 + defensive_weight * 100))

        # 5. Defensive Rating: basado en Sortino y Max Drawdown estimado
        sortino_score = min(100, max(0, result.sortino_ratio * 30))
        dd_score = max(0, 100 - result.max_drawdown_estimate * 300)
        scores["defensive"] = round((sortino_score + dd_score) / 2)

        # Score compuesto
        total = sum(
            scores[k] * self.WEIGHTS[k]
            for k in self.WEIGHTS
        )

        return {
            "total": round(total),
            "breakdown": {k: round(v) for k, v in scores.items()},
            "label": self._label(total),
        }

    def _label(self, score: float) -> str:
        if score >= 85:
            return "Excelente"
        elif score >= 70:
            return "Bueno"
        elif score >= 55:
            return "Moderado"
        elif score >= 40:
            return "Débil"
        return "Crítico"


# ─── Main Pipeline ───────────────────────────────────────────────────────────

class QuantPipeline:
    """
    Orquesta las 5 etapas del Quant Engine.
    Punto de entrada principal para el servicio de portfolio.
    """

    def __init__(
        self,
        constraints: InvestorConstraints,
        optimization_model: str = "max_sharpe",
        conservative_technical: bool = True,
    ):
        self.constraints = constraints
        self.optimization_model = optimization_model
        self.fundamental_filter = FundamentalFilter()
        self.technical_filter = TechnicalFilter()
        self.liquidity_filter = LiquidityFilter()
        self.cov_estimator = CovarianceEstimator()
        self.health_calculator = HealthScoreCalculator()

        if optimization_model == "black_litterman":
            self.optimizer = BlackLittermanOptimizer()
        else:
            self.optimizer = PortfolioOptimizer(constraints)

    def run(
        self,
        universe: List[AssetData],
        views_P: Optional[np.ndarray] = None,
        views_Q: Optional[np.ndarray] = None,
    ) -> Dict:
        """
        Ejecuta el pipeline completo.
        Retorna dict con resultado de optimización, métricas y health score.
        """
        logger.info(f"QuantPipeline: starting with {len(universe)} assets")
        rejected_all = []

        # Aplicar restricciones del usuario primero
        universe = [
            a for a in universe
            if a.ticker not in self.constraints.excluded_tickers
            and a.sector not in self.constraints.excluded_sectors
            and a.country not in self.constraints.excluded_countries
        ]

        # Etapa 1: Fundamental
        universe, rejected = self.fundamental_filter.filter(universe)
        rejected_all.extend(rejected)

        # Etapa 2: Técnico
        universe, rejected = self.technical_filter.filter(
            universe,
            conservative=self.constraints.max_drawdown_tolerance < 0.10
        )
        rejected_all.extend(rejected)

        # Etapa 3: Liquidez
        universe, rejected = self.liquidity_filter.filter(universe)
        rejected_all.extend(rejected)

        if len(universe) < self.constraints.min_assets:
            return {
                "error": f"Solo {len(universe)} activos superaron los filtros. "
                         f"Mínimo requerido: {self.constraints.min_assets}",
                "rejected": rejected_all,
            }

        # Limitar al máximo de activos
        universe = universe[:self.constraints.max_assets]

        # Etapa 4: Covarianzas
        try:
            cov_matrix, valid_tickers = self.cov_estimator.estimate(universe)
        except ValueError as e:
            return {"error": str(e), "rejected": rejected_all}

        # Filtrar universe a los tickers con suficiente historia
        universe = [a for a in universe if a.ticker in valid_tickers]

        # Retornos esperados (media histórica — se reemplaza con BL en Elite)
        expected_returns = np.array([
            a.returns.mean() for a in universe
        ])

        # Etapa 5: Optimización
        try:
            if self.optimization_model == "black_litterman":
                market_weights = np.ones(len(universe)) / len(universe)
                result = self.optimizer.optimize(
                    cov_matrix=cov_matrix / 252,
                    market_weights=market_weights,
                    views_P=views_P,
                    views_Q=views_Q,
                    views_omega=None,
                    tickers=valid_tickers,
                    constraints=self.constraints,
                )
            else:
                result = self.optimizer.optimize(
                    expected_returns=expected_returns,
                    cov_matrix=cov_matrix / 252,
                    tickers=valid_tickers,
                    model=self.optimization_model,
                    sector_map={a.ticker: a.sector for a in universe if a.sector},
                )
        except Exception as e:
            logger.error(f"Optimization failed: {e}")
            return {"error": f"Error en optimización: {str(e)}", "rejected": rejected_all}

        # Health Score
        health = self.health_calculator.calculate(result, universe, result.weights)

        logger.info(
            f"QuantPipeline done: {len(result.weights)} assets, "
            f"Sharpe={result.sharpe_ratio:.2f}, Health={health['total']}"
        )

        return {
            "result": result,
            "health_score": health,
            "assets_used": [a.ticker for a in universe if a.ticker in result.weights],
            "rejected_assets": rejected_all,
            "optimization_model": self.optimization_model,
        }
