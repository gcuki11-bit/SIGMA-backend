"""
QuantAdvisor — API v1 Router
Todos los endpoints organizados por dominio.
"""
from fastapi import APIRouter

from app.api.v1.endpoints import (
    auth,
    users,
    portfolios,
    subscriptions,
    market_data,
    news,
    ai_advisor,
    attribution,
)

api_router = APIRouter()

# Auth
api_router.include_router(auth.router, prefix="/auth", tags=["Authentication"])

# Users & Profiles
api_router.include_router(users.router, prefix="/users", tags=["Users"])

# Portfolios & Quant Engine
api_router.include_router(portfolios.router, prefix="/portfolios", tags=["Portfolios"])

# Subscriptions & Billing
api_router.include_router(subscriptions.router, prefix="/subscriptions", tags=["Subscriptions"])

# Market Data
api_router.include_router(market_data.router, prefix="/market", tags=["Market Data"])

# News & Signals
api_router.include_router(news.router, prefix="/news", tags=["News & Signals"])

# AI Advisor
api_router.include_router(ai_advisor.router, prefix="/ai", tags=["AI Advisor"])

# Factor & Risk Attribution
api_router.include_router(attribution.router, prefix="/attribution", tags=["Attribution"])
