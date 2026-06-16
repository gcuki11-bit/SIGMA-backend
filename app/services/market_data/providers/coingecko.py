"""
CoinGecko provider adapter (cripto).
====================================
API publica sin key (rate-limited) o con Demo/Pro key. Permite uso comercial
con atribucion -> `is_redistributable = True`. Ideal como fuente cripto primaria
o como redundancia de Finnhub.
Docs: https://www.coingecko.com/api/documentation
"""
from __future__ import annotations

import time
from datetime import datetime, timezone
from typing import Dict, List, Optional

import httpx
import pandas as pd

from .base import (
    AssetClass,
    DataProvider,
    DataQuality,
    History,
    NotSupportedError,
    Provenance,
    ProviderError,
    Quote,
    RateLimitError,
)

# Mapa minimo simbolo -> id de CoinGecko (extensible)
_COIN_IDS: Dict[str, str] = {
    "BTC": "bitcoin", "ETH": "ethereum", "SOL": "solana", "BNB": "binancecoin",
    "XRP": "ripple", "ADA": "cardano", "DOGE": "dogecoin", "AVAX": "avalanche-2",
    "MATIC": "matic-network", "DOT": "polkadot", "LINK": "chainlink",
    "LTC": "litecoin", "USDT": "tether", "USDC": "usd-coin",
}


class CoinGeckoProvider:
    name = "coingecko"
    is_redistributable = True

    supported: List[AssetClass] = [AssetClass.CRYPTO]

    BASE = "https://api.coingecko.com/api/v3"

    def __init__(self, api_key: str = "", http: Optional[httpx.AsyncClient] = None):
        self.api_key = api_key
        self.http = http or httpx.AsyncClient(timeout=15.0)

    def supports(self, asset_class: AssetClass) -> bool:
        return asset_class == AssetClass.CRYPTO

    def _headers(self) -> dict:
        return {"x-cg-demo-api-key": self.api_key} if self.api_key else {}

    @staticmethod
    def _coin_id(symbol: str) -> str:
        base = symbol.upper().replace("-USD", "").replace("USDT", "").replace("USD", "")
        return _COIN_IDS.get(base, base.lower())

    async def _get(self, path: str, params: dict) -> dict | list:
        t0 = time.monotonic()
        r = await self.http.get(f"{self.BASE}{path}", params=params, headers=self._headers())
        latency = (time.monotonic() - t0) * 1000
        if r.status_code == 429:
            raise RateLimitError("coingecko 429")
        if r.status_code != 200:
            raise ProviderError(f"coingecko {r.status_code}")
        data = r.json()
        if isinstance(data, dict):
            data["_latency_ms"] = latency
        return data

    async def get_quote(self, symbol: str, asset_class: AssetClass) -> Quote:
        if not self.supports(asset_class):
            raise NotSupportedError("coingecko solo cripto")
        cid = self._coin_id(symbol)
        d = await self._get(
            "/simple/price",
            {"ids": cid, "vs_currencies": "usd",
             "include_24hr_change": "true", "include_24hr_vol": "true"},
        )
        if cid not in d:
            raise ProviderError(f"coingecko sin id {cid}")
        row = d[cid]
        price = float(row["usd"])
        chg_pct = _n(row.get("usd_24h_change"))
        return Quote(
            symbol=symbol,
            price=price,
            change_pct=chg_pct,
            change=price * chg_pct / 100 if chg_pct is not None else None,
            volume=_n(row.get("usd_24h_vol")),
            currency="USD",
            provenance=Provenance(
                provider=self.name,
                quality=DataQuality.REALTIME,
                is_redistributable=True,
                latency_ms=d.get("_latency_ms"),
                as_of=datetime.now(timezone.utc).isoformat(),
                note="CoinGecko (atribucion requerida).",
            ),
        )

    async def get_history(self, symbol: str, asset_class: AssetClass, days: int = 504) -> History:
        if not self.supports(asset_class):
            raise NotSupportedError("coingecko solo cripto")
        cid = self._coin_id(symbol)
        d = await self._get(
            f"/coins/{cid}/market_chart",
            {"vs_currency": "usd", "days": str(days), "interval": "daily"},
        )
        prices = d.get("prices", []) if isinstance(d, dict) else []
        vols = d.get("total_volumes", []) if isinstance(d, dict) else []
        if not prices:
            raise ProviderError(f"coingecko sin historico {cid}")

        close = pd.Series(
            [p[1] for p in prices],
            index=pd.to_datetime([p[0] for p in prices], unit="ms"),
        )
        vol = pd.Series(
            [v[1] for v in vols],
            index=pd.to_datetime([v[0] for v in vols], unit="ms"),
        ) if vols else pd.Series(dtype=float)

        df = pd.DataFrame({
            "open": close, "high": close, "low": close, "close": close,
            "volume": vol.reindex(close.index) if not vol.empty else None,
        }).sort_index()

        return History(
            symbol=symbol, df=df,
            provenance=Provenance(
                provider=self.name,
                quality=DataQuality.EOD,
                is_redistributable=True,
                latency_ms=d.get("_latency_ms") if isinstance(d, dict) else None,
                as_of=datetime.now(timezone.utc).isoformat(),
            ),
        )


def _n(v) -> Optional[float]:
    try:
        return float(v) if v is not None else None
    except (TypeError, ValueError):
        return None


_check: DataProvider = CoinGeckoProvider()
