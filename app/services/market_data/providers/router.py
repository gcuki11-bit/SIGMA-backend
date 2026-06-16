"""
ProviderRouter
==============
Selecciona el mejor proveedor por clase de activo, con failover automatico
y circuit breaker. Devuelve siempre datos normalizados (Quote / History) con
metadata de provenance para que el frontend muestre la calidad del dato.

Orden de prioridad (configurable): los proveedores redistribuibles y de mayor
calidad van primero; yfinance queda como ultimo recurso.
"""
from __future__ import annotations

import logging
from typing import Dict, List, Optional

import httpx

from app.core.config import settings

from .base import (
    AssetClass,
    CircuitBreaker,
    DataProvider,
    History,
    NotSupportedError,
    ProviderError,
    Quote,
)
from .coingecko import CoinGeckoProvider
from .finnhub import FinnhubProvider
from .yfinance_provider import YFinanceProvider

logger = logging.getLogger(__name__)


# Prioridad por clase de activo. El router recorre la lista en orden y usa el
# primer proveedor disponible (con key configurada y circuito cerrado).
DEFAULT_PRIORITY: Dict[AssetClass, List[str]] = {
    AssetClass.US_EQUITY: ["finnhub", "yfinance"],
    AssetClass.ETF: ["finnhub", "yfinance"],
    AssetClass.CRYPTO: ["coingecko", "finnhub", "yfinance"],
    AssetClass.FOREX: ["finnhub", "yfinance"],
    AssetClass.COMMODITY: ["yfinance"],
    AssetClass.CEDEAR: ["yfinance"],      # IOL se maneja aparte en MarketDataService
    AssetClass.AR_EQUITY: ["yfinance"],
    AssetClass.BOND: ["yfinance"],
    AssetClass.UNKNOWN: ["yfinance"],
}


class ProviderRouter:
    def __init__(self, http: Optional[httpx.AsyncClient] = None):
        self.http = http or httpx.AsyncClient(timeout=15.0)
        self.providers: Dict[str, DataProvider] = self._build()
        self.breakers: Dict[str, CircuitBreaker] = {
            name: CircuitBreaker() for name in self.providers
        }
        self.priority = DEFAULT_PRIORITY

    def _build(self) -> Dict[str, DataProvider]:
        """Instancia solo los proveedores con credenciales disponibles."""
        providers: Dict[str, DataProvider] = {}

        fh_key = getattr(settings, "FINNHUB_API_KEY", "") or ""
        if fh_key:
            providers["finnhub"] = FinnhubProvider(fh_key, http=self.http)

        cg_key = getattr(settings, "COINGECKO_API_KEY", "") or ""
        providers["coingecko"] = CoinGeckoProvider(cg_key, http=self.http)  # funciona sin key

        if getattr(settings, "YFINANCE_ENABLED", True):
            providers["yfinance"] = YFinanceProvider()

        logger.info("ProviderRouter activo con: %s", list(providers.keys()))
        return providers

    def _chain(self, asset_class: AssetClass) -> List[str]:
        return self.priority.get(asset_class, ["yfinance"])

    def health(self) -> List[dict]:
        """Estado de cada proveedor (para el panel admin / health)."""
        return [
            {
                "provider": name,
                "redistributable": getattr(p, "is_redistributable", False),
                "circuit_open": self.breakers[name].is_open,
                "supported": [a.value for a in getattr(p, "supported", [])],
            }
            for name, p in self.providers.items()
        ]

    async def _try(self, method: str, asset_class: AssetClass, *args, **kwargs):
        """Recorre la cadena de proveedores hasta que uno responda."""
        errors: List[str] = []
        for name in self._chain(asset_class):
            provider = self.providers.get(name)
            if not provider:
                continue
            breaker = self.breakers[name]
            if breaker.is_open:
                errors.append(f"{name}: circuito abierto")
                continue
            if not provider.supports(asset_class):
                continue
            try:
                result = await getattr(provider, method)(*args, asset_class=asset_class, **kwargs)
                breaker.record_success()
                return result
            except NotSupportedError:
                continue
            except ProviderError as e:
                breaker.record_failure()
                errors.append(f"{name}: {e}")
                logger.warning("Provider %s fallo (%s): %s", name, method, e)
            except Exception as e:  # noqa: BLE001
                breaker.record_failure()
                errors.append(f"{name}: error inesperado {type(e).__name__}")
                logger.exception("Provider %s error inesperado", name)
        raise ProviderError(
            f"Todos los proveedores fallaron para {asset_class.value}: {'; '.join(errors)}"
        )

    async def get_quote(self, symbol: str, asset_class: AssetClass) -> Quote:
        return await self._try("get_quote", asset_class, symbol)

    async def get_history(self, symbol: str, asset_class: AssetClass, days: int = 504) -> History:
        return await self._try("get_history", asset_class, symbol, days=days)
