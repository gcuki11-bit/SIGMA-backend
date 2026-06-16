"""
Finnhub provider adapter.
=========================
Cubre acciones US, forex y cripto con cotizacion casi en tiempo real.
Free tier para desarrollo; los planes pagos permiten redistribucion comercial.
Docs: https://finnhub.io/docs/api
"""
from __future__ import annotations

import time
from datetime import datetime, timezone
from typing import List, Optional

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


class FinnhubProvider:
    name = "finnhub"
    is_redistributable = True  # depende del plan; True asumiendo plan pago

    supported: List[AssetClass] = [
        AssetClass.US_EQUITY,
        AssetClass.ETF,
        AssetClass.FOREX,
        AssetClass.CRYPTO,
    ]

    BASE = "https://finnhub.io/api/v1"

    def __init__(self, api_key: str, http: Optional[httpx.AsyncClient] = None):
        self.api_key = api_key
        self.http = http or httpx.AsyncClient(timeout=15.0)

    def supports(self, asset_class: AssetClass) -> bool:
        return bool(self.api_key) and asset_class in self.supported

    # ─── helpers ──────────────────────────────────────────────────────────────

    async def _get(self, path: str, params: dict) -> dict:
        params = {**params, "token": self.api_key}
        t0 = time.monotonic()
        r = await self.http.get(f"{self.BASE}{path}", params=params)
        latency = (time.monotonic() - t0) * 1000
        if r.status_code == 429:
            raise RateLimitError("finnhub 429")
        if r.status_code != 200:
            raise ProviderError(f"finnhub {r.status_code}: {r.text[:120]}")
        data = r.json()
        data["_latency_ms"] = latency
        return data

    @staticmethod
    def _fh_symbol(symbol: str, asset_class: AssetClass) -> str:
        if asset_class == AssetClass.CRYPTO:
            # Finnhub usa formato EXCHANGE:PAIR, p.ej. BINANCE:BTCUSDT
            if ":" in symbol:
                return symbol
            base = symbol.upper().replace("-USD", "").replace("USD", "").replace("USDT", "")
            return f"BINANCE:{base}USDT"
        if asset_class == AssetClass.FOREX:
            # p.ej. EURUSD -> OANDA:EUR_USD
            s = symbol.upper().replace("/", "").replace("_", "")
            if len(s) == 6:
                return f"OANDA:{s[:3]}_{s[3:]}"
            return symbol
        return symbol.upper()

    # ─── quote ─────────────────────────────────────────────────────────────────

    async def get_quote(self, symbol: str, asset_class: AssetClass) -> Quote:
        if not self.supports(asset_class):
            raise NotSupportedError(f"finnhub no cubre {asset_class}")

        fh = self._fh_symbol(symbol, asset_class)
        # /quote solo aplica a equities; cripto/forex usan candles ultimo cierre
        if asset_class in (AssetClass.US_EQUITY, AssetClass.ETF):
            d = await self._get("/quote", {"symbol": fh})
            price = d.get("c")
            if not price:
                raise ProviderError(f"finnhub sin precio para {symbol}")
            return Quote(
                symbol=symbol,
                price=float(price),
                change=_n(d.get("d")),
                change_pct=_n(d.get("dp")),
                open=_n(d.get("o")),
                high=_n(d.get("h")),
                low=_n(d.get("l")),
                prev_close=_n(d.get("pc")),
                currency="USD",
                provenance=Provenance(
                    provider=self.name,
                    quality=DataQuality.REALTIME,
                    is_redistributable=self.is_redistributable,
                    latency_ms=d.get("_latency_ms"),
                    as_of=datetime.now(timezone.utc).isoformat(),
                ),
            )

        # cripto / forex: derivar de la ultima vela
        hist = await self.get_history(symbol, asset_class, days=5)
        if hist.df.empty:
            raise ProviderError(f"finnhub sin datos para {symbol}")
        last = hist.df.iloc[-1]
        prev = hist.df.iloc[-2] if len(hist.df) > 1 else last
        price = float(last["close"])
        prev_close = float(prev["close"])
        return Quote(
            symbol=symbol,
            price=price,
            change=price - prev_close,
            change_pct=(price / prev_close - 1) * 100 if prev_close else None,
            open=_n(last.get("open")),
            high=_n(last.get("high")),
            low=_n(last.get("low")),
            prev_close=prev_close,
            volume=_n(last.get("volume")),
            currency="USD",
            provenance=hist.provenance,
        )

    # ─── history ────────────────────────────────────────────────────────────────

    async def get_history(self, symbol: str, asset_class: AssetClass, days: int = 504) -> History:
        if not self.supports(asset_class):
            raise NotSupportedError(f"finnhub no cubre {asset_class}")

        fh = self._fh_symbol(symbol, asset_class)
        now = int(time.time())
        frm = now - days * 86400

        if asset_class == AssetClass.CRYPTO:
            path = "/crypto/candle"
        elif asset_class == AssetClass.FOREX:
            path = "/forex/candle"
        else:
            path = "/stock/candle"

        d = await self._get(path, {"symbol": fh, "resolution": "D", "from": frm, "to": now})
        if d.get("s") != "ok":
            raise ProviderError(f"finnhub candles status={d.get('s')} para {symbol}")

        df = pd.DataFrame({
            "open": d.get("o", []),
            "high": d.get("h", []),
            "low": d.get("l", []),
            "close": d.get("c", []),
            "volume": d.get("v", []),
        })
        df.index = pd.to_datetime(d.get("t", []), unit="s")
        df = df.sort_index()

        return History(
            symbol=symbol,
            df=df,
            provenance=Provenance(
                provider=self.name,
                quality=DataQuality.EOD,
                is_redistributable=self.is_redistributable,
                latency_ms=d.get("_latency_ms"),
                as_of=datetime.now(timezone.utc).isoformat(),
            ),
        )


def _n(v) -> Optional[float]:
    try:
        return float(v) if v is not None else None
    except (TypeError, ValueError):
        return None


# Aserta en import que cumple el contrato
_check: DataProvider = FinnhubProvider(api_key="")  # type: ignore[assignment]
