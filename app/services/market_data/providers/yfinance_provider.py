"""
yfinance provider adapter.
==========================
Fallback universal gratuito. IMPORTANTE: los terminos de uso de Yahoo NO
permiten redistribucion comercial -> `is_redistributable = False`.
El router lo usa como ultimo recurso y el frontend debe marcar el dato como
"referencial / no apto para decisiones de clientes" cuando proviene de aca.
"""
from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from typing import List, Optional

import pandas as pd
import yfinance as yf

from .base import (
    AssetClass,
    DataProvider,
    DataQuality,
    History,
    Provenance,
    ProviderError,
    Quote,
)


class YFinanceProvider:
    name = "yfinance"
    is_redistributable = False

    supported: List[AssetClass] = [
        AssetClass.US_EQUITY, AssetClass.ETF, AssetClass.CEDEAR,
        AssetClass.AR_EQUITY, AssetClass.CRYPTO, AssetClass.FOREX,
        AssetClass.COMMODITY, AssetClass.BOND, AssetClass.UNKNOWN,
    ]

    def supports(self, asset_class: AssetClass) -> bool:
        return True  # ultimo recurso para todo

    @staticmethod
    def _yf_symbol(symbol: str, asset_class: AssetClass) -> str:
        if asset_class == AssetClass.CRYPTO:
            s = symbol.upper().replace("USDT", "").replace("-USD", "").replace("USD", "")
            return f"{s}-USD"
        if asset_class == AssetClass.FOREX:
            s = symbol.upper().replace("/", "").replace("_", "")
            return f"{s}=X"
        if asset_class in (AssetClass.CEDEAR, AssetClass.AR_EQUITY):
            return symbol if symbol.endswith(".BA") else f"{symbol}.BA"
        return symbol.upper()

    async def get_history(self, symbol: str, asset_class: AssetClass, days: int = 504) -> History:
        yfs = self._yf_symbol(symbol, asset_class)
        loop = asyncio.get_event_loop()
        df = await loop.run_in_executor(
            None,
            lambda: yf.download(
                yfs, period=f"{max(days // 30, 1)}mo",
                progress=False, auto_adjust=True,
            ),
        )
        if df is None or df.empty:
            raise ProviderError(f"yfinance sin datos para {symbol}")

        # yfinance puede devolver MultiIndex de columnas
        if isinstance(df.columns, pd.MultiIndex):
            df.columns = df.columns.get_level_values(0)

        out = pd.DataFrame({
            "open": df.get("Open"),
            "high": df.get("High"),
            "low": df.get("Low"),
            "close": df.get("Close"),
            "volume": df.get("Volume"),
        })
        out = out.dropna(how="all").sort_index()

        return History(
            symbol=symbol,
            df=out,
            provenance=Provenance(
                provider=self.name,
                quality=DataQuality.DELAYED,
                is_redistributable=False,
                as_of=datetime.now(timezone.utc).isoformat(),
                note="Dato referencial (Yahoo). No redistribuible comercialmente.",
            ),
        )

    async def get_quote(self, symbol: str, asset_class: AssetClass) -> Quote:
        hist = await self.get_history(symbol, asset_class, days=7)
        if hist.df.empty or len(hist.df) < 1:
            raise ProviderError(f"yfinance sin precio para {symbol}")
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
            provenance=hist.provenance,
        )


def _n(v) -> Optional[float]:
    try:
        return float(v) if v is not None and not pd.isna(v) else None
    except (TypeError, ValueError):
        return None


_check: DataProvider = YFinanceProvider()
