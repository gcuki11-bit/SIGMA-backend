"""
QuantAdvisor — Portfolio Schemas (Pydantic v2)
Request/Response models para todos los endpoints de portfolios.
"""
from datetime import datetime
from typing import Dict, List, Optional
from pydantic import BaseModel, Field, field_validator


class PortfolioCreate(BaseModel):
    name: str = Field(default="Mi Portfolio", max_length=100)
    optimization_model: str = Field(default="markowitz")
    simulated_capital_ars: Optional[float] = Field(default=None, ge=0)
    rebalance_frequency: str = Field(default="monthly")

    @field_validator("optimization_model")
    @classmethod
    def validate_optimizer(cls, v):
        valid = {"markowitz", "max_sharpe", "min_variance", "risk_parity", "black_litterman"}
        if v not in valid:
            raise ValueError(f"Optimizer debe ser uno de: {valid}")
        return v

    @field_validator("rebalance_frequency")
    @classmethod
    def validate_frequency(cls, v):
        valid = {"monthly", "quarterly", "tactical", "manual"}
        if v not in valid:
            raise ValueError(f"Frecuencia debe ser una de: {valid}")
        return v


class PositionResponse(BaseModel):
    ticker: str
    weight_recommended: float
    weight_actual: Optional[float]
    weight_is_manual: bool

    model_config = {"from_attributes": True}


class PortfolioResponse(BaseModel):
    id: str
    name: str
    optimization_model: str
    simulated_capital_ars: Optional[float]
    sharpe_ratio: Optional[float]
    sortino_ratio: Optional[float]
    max_drawdown: Optional[float]
    beta: Optional[float]
    var_95: Optional[float]
    volatility_annual: Optional[float]
    health_score: Optional[int]
    last_rebalanced_at: Optional[datetime]
    created_at: datetime

    model_config = {"from_attributes": True}


class OptimizeRequest(BaseModel):
    optimization_model: Optional[str] = Field(default="markowitz")
    max_assets: Optional[int] = Field(default=15, ge=5, le=50)
    include_tickers: Optional[List[str]] = Field(default_factory=list)
    exclude_tickers: Optional[List[str]] = Field(default_factory=list)
    # Views para Black-Litterman (Elite)
    views: Optional[List[Dict]] = None


class OptimizationResponse(BaseModel):
    portfolio_id: str
    weights: Dict[str, float]
    metrics: Dict[str, Optional[float]]
    health_score: Optional[Dict]
    optimization_model: str
    assets_used: List[str]
    rejected_assets: List[str]
    ai_explanation: Optional[str]
    disclaimer: str


class RebalanceResponse(BaseModel):
    portfolio_id: str
    trigger_type: str
    old_weights: Dict[str, float]
    new_weights: Dict[str, float]
    metrics_delta: Dict[str, float]
    ai_explanation: Optional[str]
    disclaimer: str


class HealthScoreResponse(BaseModel):
    portfolio_id: str
    total: int
    breakdown: Dict[str, int]
    label: str
