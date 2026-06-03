"""
QuantAdvisor — Database Models (SQLAlchemy 2.x async)
Schema completo: users, profiles, subscriptions, portfolios, positions, assets,
rebalance_events, news_signals, ai_sessions, audit_log.
"""
import uuid
from datetime import datetime
from typing import Any, Dict, List, Optional

from sqlalchemy import (
    BigInteger, Boolean, DateTime, Float, ForeignKey,
    Integer, JSON, String, Text, UniqueConstraint, func,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


# ─── Base ────────────────────────────────────────────────────────────────────

class Base(DeclarativeBase):
    pass


def generate_uuid() -> str:
    return str(uuid.uuid4())


# ─── USERS ───────────────────────────────────────────────────────────────────

class User(Base):
    __tablename__ = "users"

    id: Mapped[str] = mapped_column(
        String(36), primary_key=True, default=generate_uuid
    )
    email: Mapped[str] = mapped_column(String(255), unique=True, nullable=False, index=True)
    hashed_password: Mapped[Optional[str]] = mapped_column(String(255))  # None si OAuth
    full_name: Mapped[Optional[str]] = mapped_column(String(255))
    avatar_url: Mapped[Optional[str]] = mapped_column(String(512))

    # Roles: user | analyst | admin
    role: Mapped[str] = mapped_column(String(20), default="user", nullable=False)

    # OAuth
    google_id: Mapped[Optional[str]] = mapped_column(String(255), unique=True, index=True)
    oauth_provider: Mapped[Optional[str]] = mapped_column(String(50))

    # Estado
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    is_email_verified: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    email_verification_token: Mapped[Optional[str]] = mapped_column(String(255))
    password_reset_token: Mapped[Optional[str]] = mapped_column(String(255))
    password_reset_expires: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))

    # Timestamps
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )
    last_login_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))

    # Relaciones
    investor_profile: Mapped[Optional["InvestorProfile"]] = relationship(
        back_populates="user", uselist=False, cascade="all, delete-orphan"
    )
    subscriptions: Mapped[List["Subscription"]] = relationship(
        back_populates="user", cascade="all, delete-orphan"
    )
    portfolios: Mapped[List["Portfolio"]] = relationship(
        back_populates="user", cascade="all, delete-orphan"
    )
    ai_sessions: Mapped[List["AISession"]] = relationship(
        back_populates="user", cascade="all, delete-orphan"
    )
    audit_logs: Mapped[List["AuditLog"]] = relationship(back_populates="user")

    def __repr__(self) -> str:
        return f"<User {self.email} role={self.role}>"


# ─── INVESTOR PROFILE ────────────────────────────────────────────────────────

class InvestorProfile(Base):
    __tablename__ = "investor_profiles"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=generate_uuid)
    user_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("users.id", ondelete="CASCADE"),
        unique=True, nullable=False, index=True
    )

    # Score de riesgo calculado (0-100)
    risk_score: Mapped[Optional[int]] = mapped_column(Integer)

    # Clasificación final
    # ultra_conservative | conservative | moderate | growth | aggressive | speculative
    risk_classification: Mapped[Optional[str]] = mapped_column(String(30))

    # Parámetros cuantitativos derivados del perfil
    max_beta: Mapped[Optional[float]] = mapped_column(Float)
    max_drawdown_tolerance: Mapped[Optional[float]] = mapped_column(Float)   # % decimal, ej: 0.15
    max_volatility: Mapped[Optional[float]] = mapped_column(Float)           # anualizada
    max_single_asset_weight: Mapped[Optional[float]] = mapped_column(Float)  # ej: 0.20
    max_sector_weight: Mapped[Optional[float]] = mapped_column(Float)        # ej: 0.35
    time_horizon_years: Mapped[Optional[int]] = mapped_column(Integer)

    # Preferencias
    dividend_preference: Mapped[bool] = mapped_column(Boolean, default=False)
    liquidity_need: Mapped[str] = mapped_column(String(20), default="low")  # low|medium|high
    esg_preference: Mapped[bool] = mapped_column(Boolean, default=False)

    # Restricciones personalizadas (JSON para flexibilidad)
    excluded_sectors: Mapped[Optional[Dict]] = mapped_column(JSON, default=list)
    excluded_countries: Mapped[Optional[Dict]] = mapped_column(JSON, default=list)
    excluded_tickers: Mapped[Optional[Dict]] = mapped_column(JSON, default=list)
    preferred_sectors: Mapped[Optional[Dict]] = mapped_column(JSON, default=list)

    # Respuestas crudas del cuestionario (para recalcular si cambia la lógica)
    questionnaire_responses: Mapped[Optional[Dict]] = mapped_column(JSON)
    questionnaire_version: Mapped[str] = mapped_column(String(10), default="1.0")

    # AI Portfolio Health Score (calculado periódicamente)
    health_score: Mapped[Optional[int]] = mapped_column(Integer)             # 0-100
    health_score_breakdown: Mapped[Optional[Dict]] = mapped_column(JSON)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )

    user: Mapped["User"] = relationship(back_populates="investor_profile")


# ─── SUBSCRIPTIONS ───────────────────────────────────────────────────────────

class Subscription(Base):
    __tablename__ = "subscriptions"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=generate_uuid)
    user_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False, index=True
    )

    # Plan: starter | pro | elite | institutional
    plan_type: Mapped[str] = mapped_column(String(30), nullable=False)

    # Período: monthly | quarterly | annual | triennial | lifetime
    billing_period: Mapped[str] = mapped_column(String(20), nullable=False)

    # Estado: trialing | active | past_due | canceled | expired
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="trialing", index=True)

    # Precio que pagó (en ARS)
    amount_ars: Mapped[Optional[float]] = mapped_column(Float)
    is_founder_pricing: Mapped[bool] = mapped_column(Boolean, default=False)

    # Proveedores de pago
    stripe_subscription_id: Mapped[Optional[str]] = mapped_column(String(255), unique=True, index=True)
    stripe_customer_id: Mapped[Optional[str]] = mapped_column(String(255), index=True)
    mp_subscription_id: Mapped[Optional[str]] = mapped_column(String(255), unique=True, index=True)
    mp_preapproval_id: Mapped[Optional[str]] = mapped_column(String(255))

    # Fechas
    trial_ends_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))
    current_period_start: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))
    current_period_end: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))
    canceled_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))
    ended_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )

    user: Mapped["User"] = relationship(back_populates="subscriptions")
    invoices: Mapped[List["Invoice"]] = relationship(
        back_populates="subscription", cascade="all, delete-orphan"
    )


class Invoice(Base):
    __tablename__ = "invoices"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=generate_uuid)
    subscription_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("subscriptions.id", ondelete="CASCADE"), nullable=False
    )
    amount_ars: Mapped[float] = mapped_column(Float, nullable=False)
    status: Mapped[str] = mapped_column(String(20), nullable=False)  # paid | unpaid | void
    invoice_url: Mapped[Optional[str]] = mapped_column(String(512))
    external_invoice_id: Mapped[Optional[str]] = mapped_column(String(255))
    paid_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )

    subscription: Mapped["Subscription"] = relationship(back_populates="invoices")


# ─── ASSETS ──────────────────────────────────────────────────────────────────

class Asset(Base):
    __tablename__ = "assets"

    ticker: Mapped[str] = mapped_column(String(20), primary_key=True)
    name: Mapped[str] = mapped_column(String(255), nullable=False)

    # Tipo: etf | stock | cedear | bond_sovereign | bond_corporate | fci | caution
    asset_type: Mapped[str] = mapped_column(String(30), nullable=False, index=True)

    exchange: Mapped[Optional[str]] = mapped_column(String(20))   # NYSE | NASDAQ | BYMA | MAE
    currency: Mapped[str] = mapped_column(String(5), default="USD")
    country: Mapped[Optional[str]] = mapped_column(String(3), index=True)
    sector: Mapped[Optional[str]] = mapped_column(String(50), index=True)
    industry: Mapped[Optional[str]] = mapped_column(String(100))

    # Estado
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    is_operable_from_argentina: Mapped[bool] = mapped_column(Boolean, default=True)

    # Datos fundamentales cacheados (JSON)
    fundamental_data: Mapped[Optional[Dict]] = mapped_column(JSON)
    # Datos técnicos cacheados
    technical_data: Mapped[Optional[Dict]] = mapped_column(JSON)
    # Métricas de liquidez
    liquidity_data: Mapped[Optional[Dict]] = mapped_column(JSON)
    # Para bonos: YTM, duration, convexity, riesgo crediticio
    bond_data: Mapped[Optional[Dict]] = mapped_column(JSON)

    last_price: Mapped[Optional[float]] = mapped_column(Float)
    last_price_updated_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))
    data_updated_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )

    positions: Mapped[List["Position"]] = relationship(back_populates="asset")


# ─── PORTFOLIOS ───────────────────────────────────────────────────────────────

class Portfolio(Base):
    __tablename__ = "portfolios"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=generate_uuid)
    user_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False, index=True
    )

    name: Mapped[str] = mapped_column(String(100), nullable=False, default="Mi Portfolio")

    # Modelo de optimización usado: markowitz | black_litterman | risk_parity |
    # min_variance | max_sharpe
    optimization_model: Mapped[str] = mapped_column(String(30), default="markowitz")

    # Capital simulado (no real — plataforma analítica)
    simulated_capital_ars: Mapped[Optional[float]] = mapped_column(Float)

    # Métricas calculadas (se actualizan en cada rebalanceo)
    target_return: Mapped[Optional[float]] = mapped_column(Float)
    realized_return_ytd: Mapped[Optional[float]] = mapped_column(Float)
    realized_return_1y: Mapped[Optional[float]] = mapped_column(Float)

    sharpe_ratio: Mapped[Optional[float]] = mapped_column(Float)
    sortino_ratio: Mapped[Optional[float]] = mapped_column(Float)
    max_drawdown: Mapped[Optional[float]] = mapped_column(Float)
    current_drawdown: Mapped[Optional[float]] = mapped_column(Float)
    beta: Mapped[Optional[float]] = mapped_column(Float)
    alpha: Mapped[Optional[float]] = mapped_column(Float)
    volatility_annual: Mapped[Optional[float]] = mapped_column(Float)
    tracking_error: Mapped[Optional[float]] = mapped_column(Float)

    # VaR / CVaR al 95%
    var_95: Mapped[Optional[float]] = mapped_column(Float)
    cvar_95: Mapped[Optional[float]] = mapped_column(Float)

    # Health Score
    health_score: Mapped[Optional[int]] = mapped_column(Integer)
    health_breakdown: Mapped[Optional[Dict]] = mapped_column(JSON)

    # Estado del rebalanceo
    last_rebalanced_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))
    next_rebalance_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))
    rebalance_frequency: Mapped[str] = mapped_column(String(20), default="monthly")

    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )

    user: Mapped["User"] = relationship(back_populates="portfolios")
    positions: Mapped[List["Position"]] = relationship(
        back_populates="portfolio", cascade="all, delete-orphan"
    )
    rebalance_events: Mapped[List["RebalanceEvent"]] = relationship(
        back_populates="portfolio", cascade="all, delete-orphan"
    )


class Position(Base):
    __tablename__ = "positions"
    __table_args__ = (
        UniqueConstraint("portfolio_id", "ticker", name="uq_portfolio_ticker"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=generate_uuid)
    portfolio_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("portfolios.id", ondelete="CASCADE"),
        nullable=False, index=True
    )
    ticker: Mapped[str] = mapped_column(
        String(20), ForeignKey("assets.ticker"), nullable=False, index=True
    )

    # Peso recomendado por el quant engine (0-1)
    weight_recommended: Mapped[float] = mapped_column(Float, nullable=False)
    # Peso real si el usuario ajustó manualmente
    weight_actual: Mapped[Optional[float]] = mapped_column(Float)
    weight_is_manual: Mapped[bool] = mapped_column(Boolean, default=False)

    # Métricas de la posición
    contribution_to_return: Mapped[Optional[float]] = mapped_column(Float)
    contribution_to_risk: Mapped[Optional[float]] = mapped_column(Float)
    position_beta: Mapped[Optional[float]] = mapped_column(Float)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )

    portfolio: Mapped["Portfolio"] = relationship(back_populates="positions")
    asset: Mapped["Asset"] = relationship(back_populates="positions")


# ─── REBALANCE EVENTS ────────────────────────────────────────────────────────

class RebalanceEvent(Base):
    __tablename__ = "rebalance_events"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=generate_uuid)
    portfolio_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("portfolios.id", ondelete="CASCADE"),
        nullable=False, index=True
    )

    # Trigger: scheduled | volatility | drawdown | news_event | macro_event | manual
    trigger_type: Mapped[str] = mapped_column(String(30), nullable=False)
    trigger_detail: Mapped[Optional[str]] = mapped_column(Text)

    # Pesos antes y después
    old_weights: Mapped[Optional[Dict]] = mapped_column(JSON)
    new_weights: Mapped[Optional[Dict]] = mapped_column(JSON)

    # Métricas antes/después
    metrics_before: Mapped[Optional[Dict]] = mapped_column(JSON)
    metrics_after: Mapped[Optional[Dict]] = mapped_column(JSON)

    # Explicación generada por Claude
    ai_explanation: Mapped[Optional[str]] = mapped_column(Text)
    ai_tokens_used: Mapped[int] = mapped_column(Integer, default=0)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )

    portfolio: Mapped["Portfolio"] = relationship(back_populates="rebalance_events")


# ─── NEWS SIGNALS ────────────────────────────────────────────────────────────

class NewsSignal(Base):
    __tablename__ = "news_signals"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=generate_uuid)
    source: Mapped[str] = mapped_column(String(50), nullable=False, index=True)
    source_url: Mapped[Optional[str]] = mapped_column(String(512))
    headline: Mapped[str] = mapped_column(Text, nullable=False)
    body_summary: Mapped[Optional[str]] = mapped_column(Text)

    # Sentiment: -1.0 (muy negativo) a 1.0 (muy positivo)
    sentiment_score: Mapped[Optional[float]] = mapped_column(Float)
    # Clasificación: positive | negative | neutral | mixed
    sentiment_label: Mapped[Optional[str]] = mapped_column(String(20))

    # Impacto: low | medium | high | critical
    impact_level: Mapped[Optional[str]] = mapped_column(String(20), index=True)

    # Categoría: earnings | macro | geopolitical | sector | regulatory | fed | bcra
    event_category: Mapped[Optional[str]] = mapped_column(String(30), index=True)

    # Activos y sectores afectados
    affected_tickers: Mapped[Optional[Dict]] = mapped_column(JSON, default=list)
    affected_sectors: Mapped[Optional[Dict]] = mapped_column(JSON, default=list)
    affected_countries: Mapped[Optional[Dict]] = mapped_column(JSON, default=list)

    # Recomendación de rebalanceo generada
    rebalance_recommendation: Mapped[Optional[str]] = mapped_column(Text)

    published_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), index=True)
    processed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    # Deduplicación
    content_hash: Mapped[Optional[str]] = mapped_column(String(64), unique=True, index=True)


# ─── AI SESSIONS ─────────────────────────────────────────────────────────────

class AISession(Base):
    """
    Controla el uso de tokens de Claude por usuario/sesión.
    Hard limit: 5000 tokens por sesión (definido en settings).
    """
    __tablename__ = "ai_sessions"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=generate_uuid)
    user_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False, index=True
    )

    # Tokens consumidos en esta sesión
    tokens_used: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    tokens_limit: Mapped[int] = mapped_column(Integer, default=5000, nullable=False)

    # Contexto de la sesión
    session_context: Mapped[Optional[str]] = mapped_column(String(50))
    # portfolio_explanation | news_analysis | rebalance_explanation | general

    # Fecha — una sesión dura 24hs
    session_date: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), index=True
    )
    expires_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))
    is_exhausted: Mapped[bool] = mapped_column(Boolean, default=False)

    user: Mapped["User"] = relationship(back_populates="ai_sessions")


# ─── AUDIT LOG ───────────────────────────────────────────────────────────────

class AuditLog(Base):
    """Log inmutable de acciones críticas para compliance."""
    __tablename__ = "audit_logs"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    user_id: Mapped[Optional[str]] = mapped_column(
        String(36), ForeignKey("users.id"), index=True
    )
    action: Mapped[str] = mapped_column(String(100), nullable=False, index=True)
    resource_type: Mapped[Optional[str]] = mapped_column(String(50))
    resource_id: Mapped[Optional[str]] = mapped_column(String(255))
    details: Mapped[Optional[Dict]] = mapped_column(JSON)
    ip_address: Mapped[Optional[str]] = mapped_column(String(45))
    user_agent: Mapped[Optional[str]] = mapped_column(String(512))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False, index=True
    )

    user: Mapped[Optional["User"]] = relationship(back_populates="audit_logs")
