"""
QuantAdvisor — Feature Flags por Plan de Suscripción
Define exactamente qué puede hacer cada plan. Single source of truth.
"""
from dataclasses import dataclass, field
from enum import Enum
from typing import Optional


class PlanType(str, Enum):
    STARTER = "starter"
    PRO = "pro"
    ELITE = "elite"
    INSTITUTIONAL = "institutional"


class BillingPeriod(str, Enum):
    MONTHLY = "monthly"
    QUARTERLY = "quarterly"
    ANNUAL = "annual"
    TRIENNIAL = "triennial"
    LIFETIME = "lifetime"


# ─── Pricing (ARS) ───────────────────────────────────────────────────────────

PRICING: dict[PlanType, dict[BillingPeriod, float]] = {
    PlanType.STARTER: {
        BillingPeriod.MONTHLY: 24_900,
        BillingPeriod.QUARTERLY: 67_000,
        BillingPeriod.ANNUAL: 224_000,
        BillingPeriod.TRIENNIAL: 539_000,
        BillingPeriod.LIFETIME: 900_000,
    },
    PlanType.PRO: {
        BillingPeriod.MONTHLY: 69_900,
        BillingPeriod.QUARTERLY: 188_000,
        BillingPeriod.ANNUAL: 629_000,
        BillingPeriod.TRIENNIAL: 1_510_000,
        BillingPeriod.LIFETIME: 2_500_000,
    },
    PlanType.ELITE: {
        BillingPeriod.MONTHLY: 149_900,
        BillingPeriod.QUARTERLY: 404_000,
        BillingPeriod.ANNUAL: 1_349_000,
        BillingPeriod.TRIENNIAL: 3_237_000,
        BillingPeriod.LIFETIME: 5_000_000,
    },
    PlanType.INSTITUTIONAL: {
        BillingPeriod.MONTHLY: 499_000,
        BillingPeriod.ANNUAL: 5_000_000,
    },
}

FOUNDER_PRICING: dict[PlanType, float] = {
    PlanType.STARTER: 14_900,
    PlanType.PRO: 39_900,
    PlanType.ELITE: 89_900,
}


# ─── Feature Flags ───────────────────────────────────────────────────────────

@dataclass
class PlanFeatures:
    plan: PlanType

    # Límites de portfolios
    max_portfolios: int = 1
    max_assets_monitored: int = 20

    # Asset types
    access_etfs: bool = True
    access_cedears: bool = True
    access_us_stocks: bool = True
    access_bonds_sovereign: bool = False
    access_bonds_corporate: bool = False   # ONs
    access_fcis: bool = False
    access_cauciones: bool = False

    # Optimización
    optimizer_markowitz: bool = True
    optimizer_tobin: bool = False
    optimizer_max_sharpe: bool = False
    optimizer_black_litterman: bool = False
    optimizer_risk_parity: bool = False
    optimizer_min_variance: bool = False
    optimizer_cvar: bool = False

    # Risk Analytics
    metrics_sharpe: bool = True
    metrics_beta: bool = True
    metrics_volatility: bool = True
    metrics_drawdown: bool = True
    metrics_var: bool = False
    metrics_cvar: bool = False
    metrics_sortino: bool = False
    metrics_alpha: bool = False
    stress_testing: bool = False
    monte_carlo: bool = False

    # Rebalanceo
    rebalance_monthly: bool = True
    rebalance_tactical: bool = False
    rebalance_volatility_triggered: bool = False
    rebalance_macro_triggered: bool = False

    # News & AI
    ai_news_engine: bool = False
    ai_explanations: bool = False
    ai_macro_intelligence: bool = False
    ai_market_regime: bool = False
    ai_tokens_per_session: int = 0

    # Dashboard
    dashboard_heatmaps: bool = False
    dashboard_correlation_matrix: bool = False
    dashboard_factor_analysis: bool = False
    dashboard_sector_exposure_advanced: bool = False

    # Real-time
    realtime_signals: bool = False
    realtime_alerts: bool = False

    # Restricciones personalizadas
    custom_restrictions: bool = False

    # Backtesting
    backtesting: bool = False

    # API Access
    api_access: bool = False
    white_label: bool = False
    multi_user: bool = False


def get_plan_features(plan: PlanType) -> PlanFeatures:
    """Retorna los features habilitados para un plan dado."""

    if plan == PlanType.STARTER:
        return PlanFeatures(
            plan=PlanType.STARTER,
            max_portfolios=1,
            max_assets_monitored=20,
            # Optimización básica
            optimizer_markowitz=True,
            # Risk básico
            metrics_sharpe=True,
            metrics_beta=True,
            metrics_volatility=True,
            metrics_drawdown=True,
            # Sin AI
            ai_tokens_per_session=0,
        )

    elif plan == PlanType.PRO:
        return PlanFeatures(
            plan=PlanType.PRO,
            max_portfolios=5,
            max_assets_monitored=100,
            # Assets extendidos
            access_bonds_sovereign=True,
            access_bonds_corporate=True,
            access_fcis=True,
            # Optimización avanzada
            optimizer_markowitz=True,
            optimizer_tobin=True,
            optimizer_max_sharpe=True,
            # Risk avanzado
            metrics_sharpe=True,
            metrics_beta=True,
            metrics_volatility=True,
            metrics_drawdown=True,
            metrics_var=True,
            metrics_cvar=False,   # Solo Elite
            metrics_sortino=True,
            metrics_alpha=True,
            # Rebalanceo inteligente
            rebalance_monthly=True,
            rebalance_tactical=True,
            rebalance_volatility_triggered=True,
            # AI activada
            ai_news_engine=True,
            ai_explanations=True,
            ai_tokens_per_session=5000,
            # Restricciones
            custom_restrictions=True,
            # Alertas
            realtime_alerts=True,
        )

    elif plan == PlanType.ELITE:
        return PlanFeatures(
            plan=PlanType.ELITE,
            max_portfolios=999,   # ilimitado
            max_assets_monitored=9999,
            # Todos los assets
            access_bonds_sovereign=True,
            access_bonds_corporate=True,
            access_fcis=True,
            access_cauciones=True,
            # Todos los optimizadores
            optimizer_markowitz=True,
            optimizer_tobin=True,
            optimizer_max_sharpe=True,
            optimizer_black_litterman=True,
            optimizer_risk_parity=True,
            optimizer_min_variance=True,
            optimizer_cvar=True,
            # Risk institucional
            metrics_sharpe=True,
            metrics_beta=True,
            metrics_volatility=True,
            metrics_drawdown=True,
            metrics_var=True,
            metrics_cvar=True,
            metrics_sortino=True,
            metrics_alpha=True,
            stress_testing=True,
            monte_carlo=True,
            # Rebalanceo completo
            rebalance_monthly=True,
            rebalance_tactical=True,
            rebalance_volatility_triggered=True,
            rebalance_macro_triggered=True,
            # AI completa
            ai_news_engine=True,
            ai_explanations=True,
            ai_macro_intelligence=True,
            ai_market_regime=True,
            ai_tokens_per_session=5000,
            # Dashboard premium
            dashboard_heatmaps=True,
            dashboard_correlation_matrix=True,
            dashboard_factor_analysis=True,
            dashboard_sector_exposure_advanced=True,
            # Real-time
            realtime_signals=True,
            realtime_alerts=True,
            # Restricciones
            custom_restrictions=True,
            backtesting=True,
            api_access=True,
        )

    elif plan == PlanType.INSTITUTIONAL:
        f = get_plan_features(PlanType.ELITE)
        f.plan = PlanType.INSTITUTIONAL
        f.multi_user = True
        f.white_label = True
        return f

    return get_plan_features(PlanType.STARTER)  # fallback


def check_feature(plan: PlanType, feature_name: str) -> bool:
    """Verifica si un feature está habilitado para un plan."""
    features = get_plan_features(plan)
    return getattr(features, feature_name, False)
