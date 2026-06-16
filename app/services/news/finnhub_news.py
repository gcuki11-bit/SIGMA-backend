"""
QuantAdvisor — Finnhub News Ingester + filtro de relevancia de mercado
======================================================================
Reemplaza el scraping de cuentas de X (legalmente riesgoso) por una fuente de
noticias financieras con licencia de redistribucion y baja latencia.

- `FinnhubNewsIngester` devuelve articulos en el MISMO shape que NewsAPI
  (title/description/url/source/publishedAt) para que `NewsService.process_news_batch`
  los consuma sin cambios. Ademas adjunta `related_tickers` cuando Finnhub los provee.

- `is_market_relevant(...)` es el filtro que pediste: descarta lo que NO mueve mercado.
  Se aplica despues de FinBERT + ImpactClassifier, asi que decide con sentiment + impacto
  + tickers/sectores afectados, no por keywords sueltas.

Equivalencia con tu pedido original:
  · "noticias del terminal Bloomberg al segundo"  -> feed `category=general` de Finnhub
    (titulares de Reuters/Bloomberg/CNBC con licencia, no scrapeados de @DeItaone).
  · "@AlertasNews24 filtrando las que inciden en mercados" -> `is_market_relevant`.
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone, timedelta
from typing import Dict, List, Optional

import httpx

from app.core.config import settings

logger = logging.getLogger(__name__)


class FinnhubNewsIngester:
    """Noticias de mercado con licencia (Finnhub). Requiere FINNHUB_API_KEY."""

    BASE = "https://finnhub.io/api/v1"

    def __init__(self, http: Optional[httpx.AsyncClient] = None):
        self.http = http or httpx.AsyncClient(timeout=20.0)

    @property
    def enabled(self) -> bool:
        return bool(getattr(settings, "FINNHUB_API_KEY", ""))

    async def _get(self, path: str, params: dict) -> list:
        params = {**params, "token": settings.FINNHUB_API_KEY}
        r = await self.http.get(f"{self.BASE}{path}", params=params)
        if r.status_code != 200:
            logger.warning("Finnhub news %s -> %s", path, r.status_code)
            return []
        data = r.json()
        return data if isinstance(data, list) else []

    @staticmethod
    def _normalize(item: dict) -> Optional[dict]:
        """Mapea un item de Finnhub al shape de NewsAPI que espera el pipeline."""
        headline = (item.get("headline") or "").strip()
        if not headline or len(headline) < 10:
            return None
        ts = item.get("datetime")
        published_at = None
        if ts:
            try:
                published_at = datetime.fromtimestamp(int(ts), tz=timezone.utc).isoformat()
            except (ValueError, OSError):
                published_at = None
        related = item.get("related") or ""
        related_tickers = [t for t in related.split(",") if t][:10]
        return {
            "title": headline,
            "description": item.get("summary") or "",
            "content": item.get("summary") or "",
            "url": item.get("url") or "",
            "source": {"name": item.get("source") or "Finnhub"},
            "publishedAt": published_at,
            "related_tickers": related_tickers,
        }

    async def fetch_market_news(self, max_results: int = 50) -> List[Dict]:
        """Feed general de mercado (equivalente 'rapido' al terminal)."""
        if not self.enabled:
            logger.info("FINNHUB_API_KEY no configurada; se omite Finnhub news.")
            return []
        raw = await self._get("/news", {"category": "general"})
        out = [n for n in (self._normalize(i) for i in raw) if n]
        return out[:max_results]

    async def fetch_company_news(
        self, symbols: List[str], days_back: int = 2, per_symbol: int = 10
    ) -> List[Dict]:
        """Noticias por ticker (para los activos en watchlists/portfolios)."""
        if not self.enabled or not symbols:
            return []
        to = datetime.now(timezone.utc).date()
        frm = to - timedelta(days=days_back)
        out: List[Dict] = []
        for sym in symbols[:25]:
            raw = await self._get(
                "/company-news",
                {"symbol": sym.upper(), "from": frm.isoformat(), "to": to.isoformat()},
            )
            for item in raw[:per_symbol]:
                n = self._normalize(item)
                if n:
                    if sym.upper() not in n["related_tickers"]:
                        n["related_tickers"].insert(0, sym.upper())
                    out.append(n)
        return out


# ─── Filtro: ¿esta noticia incide en el mercado? ──────────────────────────────

# Categorias de evento que, por definicion, son relevantes para mercados.
RELEVANT_CATEGORIES = {"earnings", "macro", "geopolitical", "regulatory"}

# Ruido tipico que NO mueve mercados (lifestyle, deportes, etc.) — corta temprano.
NOISE_HINTS = (
    "horoscopo", "horóscopo", "receta", "celebrity", "farándula", "farandula",
    "fútbol", "futbol", "soccer", "celebrities", "lifestyle", "viral",
)


def is_market_relevant(
    headline: str,
    classification: Dict,
    sentiment: Dict,
    min_impact: str = "medium",
    sentiment_threshold: float = 0.30,
) -> bool:
    """
    Devuelve True si la noticia tiene incidencia plausible en el mercado.

    Mantiene la noticia si CUALQUIERA de estas se cumple:
      · impacto >= min_impact (low|medium|high|critical)
      · tiene tickers o sectores afectados detectados
      · categoria del evento es de mercado (earnings/macro/geopolitical/regulatory)
      · |sentiment| supera el umbral (señal fuerte aunque no matchee keywords)
    Y descarta de entrada el ruido evidente (lifestyle/deportes/etc.).
    """
    text = (headline or "").lower()
    if any(h in text for h in NOISE_HINTS):
        return False

    order = {"low": 0, "medium": 1, "high": 2, "critical": 3}
    impact = classification.get("impact_level", "low")
    if order.get(impact, 0) >= order.get(min_impact, 1):
        return True
    if classification.get("affected_tickers") or classification.get("affected_sectors"):
        return True
    if classification.get("event_category") in RELEVANT_CATEGORIES:
        return True
    if abs(float(sentiment.get("score", 0) or 0)) >= sentiment_threshold:
        return True
    return False
