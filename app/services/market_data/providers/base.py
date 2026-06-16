"""
QuantAdvisor - Data Provider Abstraction
========================================
Contrato comun para todas las fuentes de datos de mercado.

Objetivo: que el resto del sistema NO dependa de un proveedor concreto
(yfinance / Finnhub / TwelveData / IOL). Cada proveedor implementa la misma
interfaz y el `ProviderRouter` decide cual usar segun la clase de activo,
con failover automatico y metadata de calidad de dato (provenance).

Esto resuelve el riesgo principal del producto: que el dato sea confiable,
trazable y legalmente redistribuible para clientes que pagan.
"""
from __future__ import annotations

import time
from dataclasses import dataclass, field
from enum import Enum
from typing import List, Optional, Protocol, runtime_checkable

import pandas as pd


# ─── Clasificacion de activos ─────────────────────────────────────────────────

class AssetClass(str, Enum):
    US_EQUITY = "us_equity"          # Acciones US (NYSE/Nasdaq)
    ETF = "etf"
    CEDEAR = "cedear"                # Cedears AR (BYMA)
    AR_EQUITY = "ar_equity"          # Acciones argentinas
    BOND = "bond"                    # Bonos soberanos / ONs
    CRYPTO = "crypto"
    FOREX = "forex"
    COMMODITY = "commodity"
    UNKNOWN = "unknown"

    @classmethod
    def from_legacy(cls, asset_type: str) -> "AssetClass":
        """Mapea el `asset_type` legacy del sistema a una AssetClass."""
        mapping = {
            "stock": cls.US_EQUITY,
            "us_equity": cls.US_EQUITY,
            "etf": cls.ETF,
            "cedear": cls.CEDEAR,
            "ar_equity": cls.AR_EQUITY,
            "bond_sovereign": cls.BOND,
            "bond_corporate": cls.BOND,
            "bond": cls.BOND,
            "fci": cls.BOND,
            "crypto": cls.CRYPTO,
            "forex": cls.FOREX,
            "fx": cls.FOREX,
            "commodity": cls.COMMODITY,
        }
        return mapping.get((asset_type or "").lower(), cls.UNKNOWN)


# ─── Calidad / trazabilidad del dato ──────────────────────────────────────────

class DataQuality(str, Enum):
    REALTIME = "realtime"            # Tiempo real (proveedor con licencia)
    DELAYED = "delayed"              # Demorado (tipicamente 15 min)
    EOD = "eod"                      # Fin de dia
    SYNTHETIC = "synthetic"          # Generado/derivado (no apto para decidir)
    UNKNOWN = "unknown"


@dataclass
class Provenance:
    """
    Metadata de origen de cada dato. Se expone al frontend para que el usuario
    sepa exactamente que esta mirando (clave para confianza y compliance).
    """
    provider: str
    quality: DataQuality = DataQuality.UNKNOWN
    is_redistributable: bool = False     # ¿La licencia permite mostrarlo a clientes?
    latency_ms: Optional[float] = None
    as_of: Optional[str] = None          # ISO timestamp del dato
    note: Optional[str] = None

    def to_dict(self) -> dict:
        return {
            "provider": self.provider,
            "quality": self.quality.value,
            "is_redistributable": self.is_redistributable,
            "latency_ms": round(self.latency_ms, 1) if self.latency_ms is not None else None,
            "as_of": self.as_of,
            "note": self.note,
        }


# ─── Modelos de datos normalizados ────────────────────────────────────────────

@dataclass
class Quote:
    """Cotizacion puntual normalizada (cualquier proveedor -> misma forma)."""
    symbol: str
    price: float
    change: Optional[float] = None
    change_pct: Optional[float] = None
    open: Optional[float] = None
    high: Optional[float] = None
    low: Optional[float] = None
    prev_close: Optional[float] = None
    volume: Optional[float] = None
    currency: str = "USD"
    provenance: Optional[Provenance] = None

    def to_dict(self) -> dict:
        return {
            "symbol": self.symbol,
            "price": self.price,
            "change": self.change,
            "change_pct": self.change_pct,
            "open": self.open,
            "high": self.high,
            "low": self.low,
            "prev_close": self.prev_close,
            "volume": self.volume,
            "currency": self.currency,
            "provenance": self.provenance.to_dict() if self.provenance else None,
        }


@dataclass
class History:
    """
    Serie historica OHLCV normalizada.
    `df` indexado por fecha con columnas: open, high, low, close, volume.
    """
    symbol: str
    df: pd.DataFrame
    provenance: Optional[Provenance] = None

    @property
    def close(self) -> pd.Series:
        return self.df["close"] if "close" in self.df else pd.Series(dtype=float)

    @property
    def returns(self) -> pd.Series:
        return self.close.pct_change().dropna()

    def candles(self) -> List[dict]:
        """Formato listo para TradingView Lightweight Charts."""
        out = []
        for idx, row in self.df.iterrows():
            ts = idx
            time_val = ts.strftime("%Y-%m-%d") if hasattr(ts, "strftime") else str(ts)
            out.append({
                "time": time_val,
                "open": _f(row.get("open")),
                "high": _f(row.get("high")),
                "low": _f(row.get("low")),
                "close": _f(row.get("close")),
                "value": _f(row.get("volume")),  # para serie de volumen
            })
        return out


def _f(v) -> Optional[float]:
    try:
        if v is None or pd.isna(v):
            return None
        return float(v)
    except (TypeError, ValueError):
        return None


# ─── Errores ──────────────────────────────────────────────────────────────────

class ProviderError(Exception):
    """Error recuperable de un proveedor (se intenta failover al siguiente)."""


class RateLimitError(ProviderError):
    """El proveedor devolvio 429 / limite de cuota."""


class NotSupportedError(ProviderError):
    """El proveedor no cubre esta clase de activo."""


# ─── Contrato del proveedor ───────────────────────────────────────────────────

@runtime_checkable
class DataProvider(Protocol):
    """Interfaz que todo proveedor debe implementar."""

    name: str
    is_redistributable: bool
    supported: List[AssetClass]

    def supports(self, asset_class: AssetClass) -> bool: ...

    async def get_quote(self, symbol: str, asset_class: AssetClass) -> Quote: ...

    async def get_history(
        self, symbol: str, asset_class: AssetClass, days: int = 504
    ) -> History: ...


# ─── Circuit breaker simple ───────────────────────────────────────────────────

@dataclass
class CircuitBreaker:
    """
    Evita martillar un proveedor caido. Tras `threshold` fallos consecutivos,
    el proveedor queda 'abierto' (saltado) durante `cooldown_s` segundos.
    """
    threshold: int = 3
    cooldown_s: float = 60.0
    _failures: int = field(default=0, init=False)
    _opened_at: Optional[float] = field(default=None, init=False)

    @property
    def is_open(self) -> bool:
        if self._opened_at is None:
            return False
        if (time.monotonic() - self._opened_at) >= self.cooldown_s:
            # cooldown cumplido -> half-open: permitir un intento
            self._opened_at = None
            self._failures = 0
            return False
        return True

    def record_success(self) -> None:
        self._failures = 0
        self._opened_at = None

    def record_failure(self) -> None:
        self._failures += 1
        if self._failures >= self.threshold:
            self._opened_at = time.monotonic()
