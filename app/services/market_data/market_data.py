"""
QuantAdvisor — Market Data Service
Fuentes: IOL (principal AR) → yfinance (fallback/global) → Alpha Vantage (fundamentales)
         BCRA (macro AR gratuito) → NewsAPI (noticias)

Estrategia de datos:
  - Precios argentinos (Cedears/Bonos/ONs): IOL primero
  - Precios globales (ETFs/acciones US): yfinance
  - Fundamentales: yfinance info + Alpha Vantage (free tier)
  - Datos macro AR: BCRA API (gratuita, sin key)
"""
import asyncio
import hashlib
import logging
from datetime import datetime, timedelta, timezone
from typing import Dict, List, Optional, Tuple

import httpx
import pandas as pd
import yfinance as yf
from redis.asyncio import Redis

from app.core.config import settings

logger = logging.getLogger(__name__)

# ─── IOL Client ──────────────────────────────────────────────────────────────

class IOLClient:
    """
    Cliente para la API de InvertirOnline.
    Documentación: https://api.invertironline.com
    Auth: OAuth2 con token Bearer.
    """
    BASE_URL = settings.IOL_BASE_URL
    _token: Optional[str] = None
    _token_expires: Optional[datetime] = None

    def __init__(self):
        self.http = httpx.AsyncClient(
            timeout=30.0,
            headers={"User-Agent": "QuantAdvisor/1.0"},
        )

    async def _get_token(self) -> str:
        """Obtiene o renueva el token de autenticación."""
        if (
            self._token
            and self._token_expires
            and datetime.now(timezone.utc) < self._token_expires
        ):
            return self._token

        response = await self.http.post(
            f"{self.BASE_URL}/token",
            data={
                "username": settings.IOL_USERNAME,
                "password": settings.IOL_PASSWORD,
                "grant_type": "password",
            },
        )
        response.raise_for_status()
        data = response.json()
        self._token = data["access_token"]
        self._token_expires = datetime.now(timezone.utc) + timedelta(
            seconds=data.get("expires_in", 3600) - 60
        )
        return self._token

    async def get_cotizacion(self, ticker: str, mercado: str = "bCBA") -> Dict:
        """Obtiene cotización en tiempo real de un activo en BYMA."""
        token = await self._get_token()
        response = await self.http.get(
            f"{self.BASE_URL}/api/v2/{mercado}/Titulos/{ticker}/CotizacionDetalle",
            headers={"Authorization": f"Bearer {token}"},
        )
        if response.status_code == 404:
            return {}
        response.raise_for_status()
        return response.json()

    async def get_historico(
        self,
        ticker: str,
        mercado: str = "bCBA",
        desde: Optional[str] = None,
        hasta: Optional[str] = None,
    ) -> pd.DataFrame:
        """
        Histórico de precios de un activo en BYMA.
        Retorna DataFrame con columnas: fecha, apertura, maximo, minimo, cierre, volumen.
        """
        token = await self._get_token()
        params = {"ajustada": "sinAjustar", "tipo": "Acciones"}
        if desde:
            params["desde"] = desde
        if hasta:
            params["hasta"] = hasta

        response = await self.http.get(
            f"{self.BASE_URL}/api/v2/{mercado}/Titulos/{ticker}/Cotizacion/seriehistorica",
            params=params,
            headers={"Authorization": f"Bearer {token}"},
        )
        if response.status_code != 200:
            return pd.DataFrame()

        data = response.json()
        if not data:
            return pd.DataFrame()

        df = pd.DataFrame(data)
        df["fecha"] = pd.to_datetime(df["fechaHora"])
        df = df.set_index("fecha").sort_index()
        return df

    async def get_opciones_activos(self, mercado: str = "bCBA") -> List[Dict]:
        """Lista todos los activos disponibles en un mercado."""
        token = await self._get_token()
        response = await self.http.get(
            f"{self.BASE_URL}/api/v2/{mercado}/Titulos/cotizacion",
            params={"tipo": "Acciones"},
            headers={"Authorization": f"Bearer {token}"},
        )
        response.raise_for_status()
        return response.json()


# ─── BCRA Client (gratuito, sin key) ─────────────────────────────────────────

class BCRAClient:
    """
    Cliente para la API del BCRA.
    Documentación: https://api.bcra.gob.ar
    Sin autenticación — datos macro argentinos gratuitos.
    """
    BASE_URL = settings.BCRA_BASE_URL

    def __init__(self):
        self.http = httpx.AsyncClient(timeout=15.0)

    async def get_principales_variables(self) -> List[Dict]:
        """Retorna las principales variables macroeconómicas del BCRA."""
        response = await self.http.get(
            f"{self.BASE_URL}/estadisticas/v2.0/principalesvariables",
            headers={"Accept": "application/json"},
        )
        response.raise_for_status()
        return response.json().get("results", [])

    async def get_variable(self, id_variable: int, desde: str, hasta: str) -> List[Dict]:
        """
        Variables útiles:
          1  = Reservas internacionales del BCRA (en millones de USD)
          4  = Tipo de cambio minorista (Banco Nación)
          5  = Tipo de cambio mayorista
          6  = Tipo de cambio CCL
          27 = Tasa de política monetaria
          28 = BADLAR bancos privados
          29 = Tasa de pases pasivos a 1 día
          30 = Inflación mensual
          31 = Inflación interanual
        """
        response = await self.http.get(
            f"{self.BASE_URL}/estadisticas/v2.0/datosvariable/{id_variable}/{desde}/{hasta}",
            headers={"Accept": "application/json"},
        )
        if response.status_code != 200:
            return []
        return response.json().get("results", [])

    async def get_macro_dashboard(self) -> Dict:
        """Retorna un resumen del contexto macro argentino."""
        try:
            variables = await self.get_principales_variables()
            macro = {}
            for v in variables:
                vid = v.get("idVariable")
                if vid == 4:
                    macro["usd_minorista"] = v.get("valor")
                elif vid == 5:
                    macro["usd_mayorista"] = v.get("valor")
                elif vid == 27:
                    macro["tasa_politica_monetaria"] = v.get("valor")
                elif vid == 30:
                    macro["inflacion_mensual"] = v.get("valor")
                elif vid == 31:
                    macro["inflacion_interanual"] = v.get("valor")
                elif vid == 1:
                    macro["reservas_usd_mm"] = v.get("valor")
            return macro
        except Exception as e:
            logger.error(f"BCRA API error: {e}")
            return {}


# ─── Market Data Service ──────────────────────────────────────────────────────

class MarketDataService:
    """
    Servicio central de datos de mercado.
    Abstrae las fuentes y maneja caché en Redis.
    """

    def __init__(self, redis: Optional[Redis] = None):
        self.redis = redis
        self.iol = IOLClient()
        self.bcra = BCRAClient()

    # ─── Prices ──────────────────────────────────────────────────────────────

    async def get_price_history(
        self,
        ticker: str,
        asset_type: str,
        days: int = 504,  # 2 años por default
    ) -> pd.Series:
        """
        Retorna serie de retornos diarios.
        Estrategia: IOL para activos argentinos, yfinance para el resto.
        """
        cache_key = f"price_hist:{ticker}:{days}"

        # Check cache
        if self.redis:
            cached = await self.redis.get(cache_key)
            if cached:
                import pickle
                return pickle.loads(cached)

        if asset_type in ("cedear", "bond_sovereign", "bond_corporate", "fci"):
            prices = await self._get_iol_prices(ticker, days)
        else:
            prices = await self._get_yfinance_prices(ticker, days)

        # Calcular retornos logarítmicos
        if prices is not None and len(prices) > 5:
            returns = prices.pct_change().dropna()

            # Cachear 5 minutos
            if self.redis:
                import pickle
                await self.redis.setex(
                    cache_key,
                    settings.QUANT_CACHE_TTL_PRICES,
                    pickle.dumps(returns),
                )
            return returns

        # Fallback: retorno vacío
        return pd.Series(dtype=float)

    async def _get_iol_prices(self, ticker: str, days: int) -> Optional[pd.Series]:
        """Precios históricos desde IOL."""
        try:
            desde = (datetime.now() - timedelta(days=days)).strftime("%Y-%m-%d")
            hasta = datetime.now().strftime("%Y-%m-%d")
            df = await self.iol.get_historico(ticker, desde=desde, hasta=hasta)

            if df.empty:
                # Fallback a yfinance para Cedears que también cotizan en US
                return await self._get_yfinance_prices(ticker, days)

            close_col = next(
                (c for c in df.columns if "cierr" in c.lower() or "ultimo" in c.lower()),
                None
            )
            if close_col:
                return df[close_col].astype(float)
            return None

        except Exception as e:
            logger.warning(f"IOL error for {ticker}: {e}. Trying yfinance.")
            return await self._get_yfinance_prices(ticker, days)

    async def _get_yfinance_prices(self, ticker: str, days: int) -> Optional[pd.Series]:
        """Precios históricos desde yfinance (gratuito, global)."""
        try:
            # yfinance es sync — correr en executor
            loop = asyncio.get_event_loop()
            df = await loop.run_in_executor(
                None,
                lambda: yf.download(
                    ticker,
                    period=f"{max(days // 30, 1)}mo",
                    progress=False,
                    auto_adjust=True,
                )
            )
            if df.empty:
                return None
            return df["Close"]
        except Exception as e:
            logger.error(f"yfinance error for {ticker}: {e}")
            return None

    # ─── Fundamentals ────────────────────────────────────────────────────────

    async def get_fundamentals(self, ticker: str, asset_type: str) -> Dict:
        """
        Datos fundamentales de un activo.
        yfinance info es gratuito y cubre la mayoría de los casos.
        """
        cache_key = f"fundamentals:{ticker}"

        if self.redis:
            cached = await self.redis.get(cache_key)
            if cached:
                import json
                return json.loads(cached)

        if asset_type in ("stock", "cedear", "etf"):
            data = await self._get_yfinance_info(ticker)
        else:
            data = {}

        if self.redis and data:
            import json
            await self.redis.setex(
                cache_key,
                settings.QUANT_CACHE_TTL_FUNDAMENTALS,
                json.dumps(data),
            )
        return data

    async def _get_yfinance_info(self, ticker: str) -> Dict:
        """yfinance .info para fundamentales. Gratuito."""
        try:
            loop = asyncio.get_event_loop()
            info = await loop.run_in_executor(
                None,
                lambda: yf.Ticker(ticker).info,
            )

            return {
                "roe": info.get("returnOnEquity"),
                "roic": info.get("returnOnAssets"),          # Proxy ROIC
                "ev_ebitda": info.get("enterpriseToEbitda"),
                "peg_ratio": info.get("pegRatio"),
                "debt_equity": info.get("debtToEquity"),
                "current_ratio": info.get("currentRatio"),
                "net_margin": info.get("profitMargins"),
                "revenue_growth": info.get("revenueGrowth"),
                "fcf_growth": None,                          # Requiere cálculo adicional
                "market_cap": info.get("marketCap"),
                "sector": info.get("sector"),
                "industry": info.get("industry"),
                "country": info.get("country"),
                "currency": info.get("currency"),
                "avg_volume": info.get("averageVolume"),
                "fifty_two_week_high": info.get("fiftyTwoWeekHigh"),
                "fifty_two_week_low": info.get("fiftyTwoWeekLow"),
                "beta": info.get("beta"),
                "trailing_pe": info.get("trailingPE"),
                "forward_pe": info.get("forwardPE"),
                "dividend_yield": info.get("dividendYield"),
                "name": info.get("longName") or info.get("shortName"),
            }
        except Exception as e:
            logger.error(f"yfinance info error for {ticker}: {e}")
            return {}

    # ─── Technical Indicators ────────────────────────────────────────────────

    async def compute_technical_indicators(self, returns: pd.Series, prices: pd.Series) -> Dict:
        """
        Calcula indicadores técnicos sobre la serie de precios.
        Todo en pandas/numpy — sin dependencias externas de pago.
        """
        if len(prices) < 50:
            return {}

        try:
            # SMA 200
            sma_200 = prices.rolling(200).mean()
            above_sma_200 = float(prices.iloc[-1]) > float(sma_200.iloc[-1]) if len(prices) >= 200 else None

            # EMA 50
            ema_50 = prices.ewm(span=50, adjust=False).mean()
            above_ema_50 = float(prices.iloc[-1]) > float(ema_50.iloc[-1]) if len(prices) >= 50 else None

            # RSI 14
            rsi_14 = self._compute_rsi(prices, 14)

            # MACD (12, 26, 9)
            ema_12 = prices.ewm(span=12).mean()
            ema_26 = prices.ewm(span=26).mean()
            macd = ema_12 - ema_26
            macd_signal = macd.ewm(span=9).mean()
            macd_histogram = macd - macd_signal
            macd_bullish = float(macd_histogram.iloc[-1]) > 0

            # ATR (Average True Range) - percentil vs histórico
            high_low = prices.rolling(2).max() - prices.rolling(2).min()
            atr = high_low.rolling(14).mean()
            atr_pct = float(atr.rank(pct=True).iloc[-1]) if len(atr.dropna()) > 0 else None

            # Momentum 3 meses
            mom_3m = float(prices.iloc[-1] / prices.iloc[-63] - 1) if len(prices) >= 63 else None

            # Bollinger Bands (20, 2)
            sma_20 = prices.rolling(20).mean()
            std_20 = prices.rolling(20).std()
            bb_upper = sma_20 + 2 * std_20
            bb_lower = sma_20 - 2 * std_20
            bb_pct = float(
                (prices.iloc[-1] - bb_lower.iloc[-1]) /
                (bb_upper.iloc[-1] - bb_lower.iloc[-1])
            ) if bb_upper.iloc[-1] != bb_lower.iloc[-1] else 0.5

            return {
                "rsi_14": float(rsi_14.iloc[-1]) if rsi_14 is not None else None,
                "macd_signal": 1 if macd_bullish else -1,
                "macd_histogram": float(macd_histogram.iloc[-1]),
                "above_sma_200": above_sma_200,
                "above_ema_50": above_ema_50,
                "momentum_3m": mom_3m,
                "atr_percentile": atr_pct,
                "bollinger_pct": bb_pct,
            }

        except Exception as e:
            logger.error(f"Technical indicators error: {e}")
            return {}

    def _compute_rsi(self, prices: pd.Series, period: int = 14) -> Optional[pd.Series]:
        """RSI clásico de Wilder."""
        delta = prices.diff()
        gain = delta.clip(lower=0)
        loss = -delta.clip(upper=0)
        avg_gain = gain.ewm(com=period - 1, min_periods=period).mean()
        avg_loss = loss.ewm(com=period - 1, min_periods=period).mean()
        rs = avg_gain / avg_loss
        return 100 - (100 / (1 + rs))

    # ─── Macro ───────────────────────────────────────────────────────────────

    async def get_macro_context(self) -> Dict:
        """Contexto macro combinado: AR (BCRA) + Global (yfinance)."""
        ar_macro = await self.bcra.get_macro_dashboard()

        # VIX y datos globales via yfinance (gratuito)
        try:
            loop = asyncio.get_event_loop()
            vix_data = await loop.run_in_executor(
                None,
                lambda: yf.download("^VIX", period="5d", progress=False)["Close"].iloc[-1]
            )
            spy_1y = await loop.run_in_executor(
                None,
                lambda: yf.download("SPY", period="1y", progress=False)["Close"]
            )
            spy_return_1y = float(spy_1y.iloc[-1] / spy_1y.iloc[0] - 1)
        except Exception:
            vix_data = None
            spy_return_1y = None

        return {
            "argentina": ar_macro,
            "global": {
                "vix": float(vix_data) if vix_data is not None else None,
                "spy_return_1y": spy_return_1y,
            }
        }

    # ─── Universe Builder ─────────────────────────────────────────────────────

    async def build_asset_universe(
        self,
        asset_types: List[str],
        plan_features,
    ) -> List[str]:
        """
        Retorna la lista de tickers disponibles según plan y tipos de activo.
        Universe base — se expande en fases.
        """
        universe = []

        if "etf" in asset_types:
            universe.extend(ETF_UNIVERSE)

        if "stock" in asset_types:
            universe.extend(US_STOCK_UNIVERSE)

        if "cedear" in asset_types and plan_features.access_cedears:
            universe.extend(CEDEAR_UNIVERSE)

        if "bond_sovereign" in asset_types and plan_features.access_bonds_sovereign:
            universe.extend(BOND_SOVEREIGN_UNIVERSE)

        if "bond_corporate" in asset_types and plan_features.access_bonds_corporate:
            universe.extend(BOND_CORPORATE_UNIVERSE)

        return universe


# ─── Universe Definitions ────────────────────────────────────────────────────

ETF_UNIVERSE = [
    "SPY", "QQQ", "DIA", "IWM", "EEM", "VTI", "VEA", "VWO",
    "GLD", "TLT", "LQD", "HYG", "SHY",
    # Sectoriales
    "XLK", "XLF", "XLV", "XLE", "XLI", "XLY", "XLP", "XLU", "XLB", "XLRE",
    # Factor
    "VIG", "SCHD", "NOBL",  # Dividendos
    "VBR", "VIOV",           # Value
    "VUG", "VONG",           # Growth
    "MTUM",                  # Momentum
    # Volatilidad baja
    "USMV", "SPLV",
]

US_STOCK_UNIVERSE = [
    # Mega Cap Tech
    "AAPL", "MSFT", "NVDA", "GOOGL", "META", "AMZN", "TSLA",
    # Financieras
    "JPM", "BAC", "GS", "BRK-B",
    # Healthcare
    "JNJ", "UNH", "LLY", "ABBV",
    # Consumer
    "KO", "PG", "MCD", "WMT",
    # Industriales
    "CAT", "HON", "RTX",
    # Energía
    "XOM", "CVX",
]

CEDEAR_UNIVERSE = [
    # Tech
    "AAPL", "MSFT", "GOOGL", "META", "AMZN", "NVDA", "TSLA",
    "ORCL", "CRM", "ADBE",
    # Financieras
    "JPM", "BAC", "GS",
    # Energía
    "XOM", "CVX",
    # Consumer
    "KO", "MCD", "WMT",
    # ETFs como Cedears
    "SPY", "QQQ", "DIA",
]

BOND_SOVEREIGN_UNIVERSE = [
    "AL29", "AL30", "AL35",
    "GD29", "GD30", "GD35", "GD38", "GD41", "GD46",
    "AE38",  # Strip de cupón
    "T2V25", "T3X4",  # Bonos en pesos ajustados CER
]

BOND_CORPORATE_UNIVERSE = [
    # ONs de empresas argentinas — expandir en Fase 3
    "YPF", "TLGD", "PAMPA",
]


# Singleton
market_data_service = MarketDataService()
