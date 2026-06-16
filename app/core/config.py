"""
QuantAdvisor Platform â€” Core Configuration
Todas las variables de entorno centralizadas con validaciÃ³n Pydantic.
"""
from functools import lru_cache
from typing import List, Optional
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
    )

    # â”€â”€â”€ APP â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
    APP_NAME: str = "QuantAdvisor"
    APP_VERSION: str = "0.1.0"
    ENVIRONMENT: str = "development"          # development | staging | production
    DEBUG: bool = False
    SECRET_KEY: str                           # openssl rand -hex 32
    ALLOWED_ORIGINS: List[str] = [
        "http://localhost:3000",
        "https://quantadvisor.vercel.app",
        "https://sigma-one-flame.vercel.app",  # Production Vercel deployment
    ]

    # â”€â”€â”€ DATABASE â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
    DATABASE_URL: str = "sqlite+aiosqlite:////tmp/sigma.db"
    DATABASE_PUBLIC_URL: str = ""   # Railway public PostgreSQL URL (fallback if DATABASE_URL auth fails)
    DATABASE_POOL_SIZE: int = 10
    DATABASE_MAX_OVERFLOW: int = 20

    # â”€â”€â”€ REDIS â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
    REDIS_URL: str = "redis://localhost:6379/0"  # Railway Redis URL
    REDIS_SESSION_TTL: int = 3600             # 1 hora
    REDIS_CACHE_TTL: int = 300               # 5 min default cache

    # â”€â”€â”€ AUTH â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
    JWT_SECRET_KEY: str
    JWT_ALGORITHM: str = "HS256"
    JWT_ACCESS_TOKEN_EXPIRE_MINUTES: int = 60
    JWT_REFRESH_TOKEN_EXPIRE_DAYS: int = 30

    GOOGLE_CLIENT_ID: str = ""
    GOOGLE_CLIENT_SECRET: str = ""

    NEXTAUTH_SECRET: str = ""
    NEXTAUTH_URL: str = "http://localhost:3000"

    # â”€â”€â”€ CLAUDE AI â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
    ANTHROPIC_API_KEY: str
    CLAUDE_MODEL: str = "claude-sonnet-4-20250514"
    CLAUDE_MAX_TOKENS: int = 1000
    CLAUDE_TOKENS_PER_USER_SESSION: int = 5000   # Hard limit por usuario/sesiÃ³n

    # â”€â”€â”€ MARKET DATA â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
    # IOL (Invertir Online) â€” principal para mercado argentino
    IOL_USERNAME: str = ""
    IOL_PASSWORD: str = ""
    IOL_BASE_URL: str = "https://api.invertironline.com"

    # Alpha Vantage â€” free tier para datos US
    ALPHA_VANTAGE_API_KEY: str = ""
    ALPHA_VANTAGE_BASE_URL: str = "https://www.alphavantage.co/query"

    # BCRA â€” gratuito, datos macro argentinos
    BCRA_BASE_URL: str = "https://api.bcra.gob.ar"

    # yfinance no necesita key â€” fallback gratuito
    YFINANCE_ENABLED: bool = True

    # Finnhub â€” acciones US / forex / cripto (real-time, redistribuible en plan pago)
    FINNHUB_API_KEY: str = ""

    # CoinGecko â€” cripto (funciona sin key; key Demo/Pro sube rate limit)
    COINGECKO_API_KEY: str = ""

    # TwelveData â€” multi-asset opcional (forex/commodities/equities)
    TWELVEDATA_API_KEY: str = ""

    # Activa la nueva capa de proveedores con failover. Si False, usa el path legacy.
    FEATURE_PROVIDER_ROUTER: bool = True

    # NewsAPI â€” free tier para noticias
    NEWS_API_KEY: str = ""
    NEWS_API_BASE_URL: str = "https://newsapi.org/v2"

    # â”€â”€â”€ PAGOS â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
    STRIPE_SECRET_KEY: str = ""
    STRIPE_WEBHOOK_SECRET: str = ""
    STRIPE_PUBLISHABLE_KEY: str = ""

    MERCADOPAGO_ACCESS_TOKEN: str = ""
    MERCADOPAGO_PUBLIC_KEY: str = ""
    MERCADOPAGO_WEBHOOK_SECRET: str = ""

    # Internal secret for Vercel→Railway webhook calls (optional; set to enable auth)
    INTERNAL_WEBHOOK_SECRET: str = ""

    # â”€â”€â”€ EMAIL â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
    SMTP_HOST: str = "smtp.resend.com"
    SMTP_PORT: int = 587
    SMTP_USER: str = ""
    SMTP_PASSWORD: str = ""
    EMAIL_FROM: str = "noreply@quantadvisor.com"
    EMAIL_FROM_NAME: str = "QuantAdvisor"

    # â”€â”€â”€ RATE LIMITING â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
    RATE_LIMIT_PER_MINUTE: int = 60
    RATE_LIMIT_AI_PER_HOUR: int = 20          # LÃ­mite de requests AI por hora/usuario
    RATE_LIMIT_QUANT_PER_HOUR: int = 10       # Optimizaciones pesadas

    # â”€â”€â”€ QUANT ENGINE â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
    QUANT_CACHE_TTL_PRICES: int = 300         # 5 min
    QUANT_CACHE_TTL_FUNDAMENTALS: int = 86400 # 24 hs
    QUANT_CACHE_TTL_PORTFOLIO: int = 3600     # 1 hora
    QUANT_MIN_HISTORY_DAYS: int = 252         # 1 aÃ±o mÃ­nimo de historia
    QUANT_DEFAULT_RISK_FREE_RATE: float = 0.05  # 5% anual (referencia)

    # â”€â”€â”€ FEATURE FLAGS â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
    FEATURE_MONTE_CARLO: bool = True
    FEATURE_BACKTESTING: bool = False         # Fase 3
    FEATURE_REAL_TIME_SIGNALS: bool = False   # Fase 2

    @property
    def is_production(self) -> bool:
        return self.ENVIRONMENT == "production"

    @property
    def is_development(self) -> bool:
        return self.ENVIRONMENT == "development"


@lru_cache()
def get_settings() -> Settings:
    """Singleton — se cachea al primer acceso."""
    return Settings()


settings = get_settings()
