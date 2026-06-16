"""
QuantAdvisor — News Engine
Pipeline de análisis de noticias financieras:
  1. Ingesta desde NewsAPI + feeds RSS
  2. Deduplicación por hash de contenido
  3. NLP: FinBERT sentiment analysis
  4. Clasificación de impacto y sectores afectados
  5. Generación de señales de rebalanceo

FinBERT: modelo pre-entrenado en textos financieros.
Más preciso que BERT genérico para sentiment en noticias de mercado.
"""
import hashlib
import logging
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Dict, List, Optional

import httpx
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.models.models import NewsSignal
from app.services.news.finnhub_news import FinnhubNewsIngester, is_market_relevant

logger = logging.getLogger(__name__)


# ─── Sector Mapping ───────────────────────────────────────────────────────────

KEYWORD_SECTOR_MAP: Dict[str, str] = {
    # Tech
    "nvidia": "Technology", "semiconductor": "Technology", "ai": "Technology",
    "artificial intelligence": "Technology", "chip": "Technology", "microsoft": "Technology",
    "apple": "Technology", "google": "Technology", "meta": "Technology",
    # Financial
    "fed": "Financials", "federal reserve": "Financials", "interest rate": "Financials",
    "rate hike": "Financials", "tasa": "Financials", "banco": "Financials",
    "inflation": "Financials", "inflación": "Financials",
    # Energy
    "oil": "Energy", "petróleo": "Energy", "opec": "Energy", "exxon": "Energy",
    "crude": "Energy", "gas": "Energy",
    # Argentina-specific
    "bcra": "Argentina", "dólar": "Argentina", "cepo": "Argentina",
    "bonos": "Argentina", "merval": "Argentina", "cedear": "Argentina",
    "fmi": "Argentina", "imf": "Argentina", "deuda": "Argentina",
    # Macro
    "recession": "Macro", "gdp": "Macro", "cpi": "Macro", "pmi": "Macro",
    "earnings": "Earnings", "revenue": "Earnings", "guidance": "Earnings",
}

KEYWORD_TICKER_MAP: Dict[str, List[str]] = {
    "nvidia": ["NVDA"], "apple": ["AAPL"], "microsoft": ["MSFT"],
    "google": ["GOOGL"], "amazon": ["AMZN"], "meta": ["META"],
    "tesla": ["TSLA"], "jpmorgan": ["JPM"], "exxon": ["XOM"],
    "ypf": ["YPF"], "pampa": ["PAMPA"], "al30": ["AL30"], "gd30": ["GD30"],
}


# ─── FinBERT Analyzer ────────────────────────────────────────────────────────

class FinBERTAnalyzer:
    """
    Sentiment analysis con FinBERT (ProsusAI/finbert).
    Labels: positive, negative, neutral.
    """
    _model = None
    _tokenizer = None
    _initialized = False

    def _init_model(self):
        """Lazy init para no cargar el modelo en startup."""
        if self._initialized:
            return
        try:
            from transformers import pipeline
            logger.info("Loading FinBERT model...")
            self._pipeline = pipeline(
                "text-classification",
                model="ProsusAI/finbert",
                tokenizer="ProsusAI/finbert",
                device=-1,  # CPU — sin GPU en Railway básico
                max_length=512,
                truncation=True,
            )
            self._initialized = True
            logger.info("FinBERT loaded successfully")
        except Exception as e:
            logger.error(f"FinBERT load error: {e}. Using rule-based fallback.")
            self._initialized = False

    def analyze(self, text: str) -> Dict:
        """
        Analiza el sentiment de un texto financiero.
        Retorna: {label, score}
        """
        if len(text) < 10:
            return {"label": "neutral", "score": 0.0}

        # Intentar FinBERT
        self._init_model()
        if self._initialized:
            try:
                # Truncar a 512 tokens (límite BERT)
                text_truncated = text[:1024]
                result = self._pipeline(text_truncated)[0]
                label = result["label"].lower()
                score_raw = result["score"]
                # Normalizar: positive=+score, negative=-score, neutral=0
                if label == "positive":
                    return {"label": "positive", "score": round(score_raw, 3)}
                elif label == "negative":
                    return {"label": "negative", "score": round(-score_raw, 3)}
                else:
                    return {"label": "neutral", "score": 0.0}
            except Exception as e:
                logger.warning(f"FinBERT inference error: {e}. Falling back to rule-based.")

        # Fallback: análisis basado en keywords
        return self._rule_based_sentiment(text)

    def _rule_based_sentiment(self, text: str) -> Dict:
        """Fallback de sentiment analysis por keywords."""
        text_lower = text.lower()
        positive_words = [
            "sube", "gana", "rally", "récord", "crecimiento", "beat", "supera",
            "positive", "bullish", "gain", "rise", "growth", "upgrade",
        ]
        negative_words = [
            "baja", "cae", "crisis", "recesión", "colapso", "miss", "pierde",
            "bearish", "fall", "drop", "decline", "downgrade", "default",
        ]
        pos_count = sum(1 for w in positive_words if w in text_lower)
        neg_count = sum(1 for w in negative_words if w in text_lower)

        if pos_count > neg_count:
            return {"label": "positive", "score": min(0.6, pos_count * 0.15)}
        elif neg_count > pos_count:
            return {"label": "negative", "score": max(-0.6, -neg_count * 0.15)}
        return {"label": "neutral", "score": 0.0}


# ─── News Ingester ────────────────────────────────────────────────────────────

class NewsIngester:
    """Obtiene noticias desde NewsAPI y feeds configurados."""

    def __init__(self):
        self.http = httpx.AsyncClient(timeout=20.0)

    async def fetch_financial_news(
        self,
        query: str = "Argentina mercado financiero OR fed rates OR earnings",
        language: str = "es",
        max_results: int = 20,
    ) -> List[Dict]:
        """Obtiene noticias de NewsAPI (free tier: 100 requests/día)."""
        if not settings.NEWS_API_KEY:
            logger.warning("NEWS_API_KEY not configured. Skipping news fetch.")
            return []

        try:
            response = await self.http.get(
                f"{settings.NEWS_API_BASE_URL}/everything",
                params={
                    "q": query,
                    "language": language,
                    "sortBy": "publishedAt",
                    "pageSize": max_results,
                    "apiKey": settings.NEWS_API_KEY,
                    "from": (datetime.now() - timedelta(hours=24)).isoformat(),
                },
            )
            response.raise_for_status()
            data = response.json()
            return data.get("articles", [])
        except Exception as e:
            logger.error(f"NewsAPI error: {e}")
            return []

    async def fetch_english_news(self, max_results: int = 20) -> List[Dict]:
        """Noticias financieras en inglés (Reuters, Bloomberg, FT)."""
        return await self.fetch_financial_news(
            query=(
                "stock market OR federal reserve OR earnings report OR "
                "S&P 500 OR nasdaq OR inflation rate OR interest rate"
            ),
            language="en",
            max_results=max_results,
        )


# ─── Impact Classifier ───────────────────────────────────────────────────────

class ImpactClassifier:
    """
    Clasifica el impacto de una noticia sobre el mercado.
    Determina: nivel de impacto, sectores y tickers afectados.
    """

    HIGH_IMPACT_KEYWORDS = [
        "fed rate", "interest rate decision", "inflation data", "cpi report",
        "earnings miss", "earnings beat", "default", "recession", "crisis",
        "bcra", "cepo", "devaluación", "default soberano", "fmi acuerdo",
    ]

    CRITICAL_KEYWORDS = [
        "market crash", "black swan", "circuit breaker", "trading halt",
        "colapso", "pánico", "corrida bancaria",
    ]

    def classify(self, headline: str, body: str, sentiment: Dict) -> Dict:
        """
        Retorna clasificación de impacto de la noticia.
        """
        text = f"{headline} {body}".lower()

        # Nivel de impacto
        impact = "low"
        if any(kw in text for kw in self.CRITICAL_KEYWORDS):
            impact = "critical"
        elif any(kw in text for kw in self.HIGH_IMPACT_KEYWORDS):
            impact = "high"
        elif abs(sentiment.get("score", 0)) > 0.5:
            impact = "medium"
        elif abs(sentiment.get("score", 0)) > 0.2:
            impact = "medium"

        # Sectores afectados
        affected_sectors = []
        for keyword, sector in KEYWORD_SECTOR_MAP.items():
            if keyword in text and sector not in affected_sectors:
                affected_sectors.append(sector)

        # Tickers afectados
        affected_tickers = []
        for keyword, tickers in KEYWORD_TICKER_MAP.items():
            if keyword in text:
                affected_tickers.extend(tickers)

        # Categoría del evento
        category = self._classify_category(text)

        return {
            "impact_level": impact,
            "affected_sectors": list(set(affected_sectors))[:5],
            "affected_tickers": list(set(affected_tickers))[:10],
            "event_category": category,
        }

    def _classify_category(self, text: str) -> str:
        if any(w in text for w in ["earnings", "revenue", "guidance", "eps"]):
            return "earnings"
        if any(w in text for w in ["fed", "federal reserve", "interest rate", "bce", "bcra"]):
            return "macro"
        if any(w in text for w in ["war", "geopolítico", "sanctions", "tensión"]):
            return "geopolitical"
        if any(w in text for w in ["regulation", "sec", "cnv", "regulación"]):
            return "regulatory"
        if any(w in text for w in ["inflation", "inflación", "cpi", "pce"]):
            return "macro"
        return "sector"


# ─── Rebalance Signal Generator ──────────────────────────────────────────────

class RebalanceSignalGenerator:
    """
    Determina si una noticia amerita recomendación de rebalanceo táctico.
    """

    def should_rebalance(
        self,
        impact: str,
        sentiment: Dict,
        affected_sectors: List[str],
        portfolio_exposure: Dict[str, float],  # sector → peso
    ) -> Optional[str]:
        """
        Retorna recomendación de rebalanceo o None.
        Solo recomienda si el portfolio tiene exposición significativa.
        """
        if impact not in ("high", "critical"):
            return None

        total_exposure = sum(
            portfolio_exposure.get(s, 0) for s in affected_sectors
        )

        if total_exposure < 0.15:  # Menos del 15% de exposición → sin rebalanceo
            return None

        sentiment_label = sentiment.get("label", "neutral")
        sentiment_score = sentiment.get("score", 0)

        if sentiment_label == "negative" and sentiment_score < -0.4:
            sectors_str = ", ".join(affected_sectors[:3])
            return (
                f"Evento negativo de alto impacto detectado en {sectors_str}. "
                f"Tu portfolio tiene {total_exposure:.0%} de exposición. "
                f"Considerar reducción táctica o revisión de pesos."
            )
        elif sentiment_label == "positive" and sentiment_score > 0.5:
            sectors_str = ", ".join(affected_sectors[:3])
            return (
                f"Catalizador positivo en {sectors_str}. "
                f"Podría justificar incremento táctico en el sector."
            )

        return None


# ─── Main News Service ────────────────────────────────────────────────────────

class NewsService:
    """
    Servicio central del News Engine.
    Orquesta ingesta → NLP → clasificación → persistencia → señales.
    """

    def __init__(self):
        self.ingester = NewsIngester()
        self.finnhub_ingester = FinnhubNewsIngester()
        self.analyzer = FinBERTAnalyzer()
        self.classifier = ImpactClassifier()
        self.signal_generator = RebalanceSignalGenerator()

    async def process_news_batch(self, db: AsyncSession) -> int:
        """
        Procesa un batch de noticias (llamado por Celery Beat cada 15 min).
        Retorna número de señales nuevas creadas.
        """
        # Obtener noticias: NewsAPI (es/en) + Finnhub (mercado, con licencia)
        articles_es = await self.ingester.fetch_financial_news()
        articles_en = await self.ingester.fetch_english_news()
        articles_finnhub = await self.finnhub_ingester.fetch_market_news(max_results=50)
        all_articles = articles_es + articles_en + articles_finnhub

        new_signals = 0
        filtered_out = 0

        for article in all_articles:
            try:
                headline = article.get("title", "")
                body = article.get("description") or article.get("content") or ""
                url = article.get("url", "")
                source = article.get("source", {}).get("name", "Unknown")
                published_raw = article.get("publishedAt")

                if not headline or len(headline) < 10:
                    continue

                # Deduplicación por hash
                content_hash = hashlib.sha256(
                    f"{headline}{url}".encode()
                ).hexdigest()

                existing = await db.execute(
                    select(NewsSignal).where(NewsSignal.content_hash == content_hash)
                )
                if existing.scalar_one_or_none():
                    continue  # Ya procesada

                # NLP
                text_for_analysis = f"{headline}. {body[:500]}"
                sentiment = self.analyzer.analyze(text_for_analysis)

                # Clasificación
                classification = self.classifier.classify(headline, body, sentiment)

                # Sembrar tickers provistos por la fuente (Finnhub 'related')
                related = article.get("related_tickers") or []
                if related:
                    merged = list(dict.fromkeys(
                        (classification.get("affected_tickers") or []) + related
                    ))
                    classification["affected_tickers"] = merged[:10]

                # FILTRO: solo guardar noticias con incidencia en el mercado
                if not is_market_relevant(headline, classification, sentiment):
                    filtered_out += 1
                    continue

                # Parsear fecha
                published_at = None
                if published_raw:
                    try:
                        published_at = datetime.fromisoformat(
                            published_raw.replace("Z", "+00:00")
                        )
                    except Exception:
                        pass

                # Persistir señal
                signal = NewsSignal(
                    source=source,
                    source_url=url,
                    headline=headline,
                    body_summary=body[:500] if body else None,
                    sentiment_score=sentiment["score"],
                    sentiment_label=sentiment["label"],
                    impact_level=classification["impact_level"],
                    event_category=classification["event_category"],
                    affected_tickers=classification["affected_tickers"],
                    affected_sectors=classification["affected_sectors"],
                    published_at=published_at,
                    content_hash=content_hash,
                )
                db.add(signal)
                new_signals += 1

            except Exception as e:
                logger.error(f"Error processing article: {e}")
                continue

        await db.flush()
        logger.info(
            "News batch: %s señales nuevas, %s descartadas por irrelevancia, de %s artículos",
            new_signals, filtered_out, len(all_articles),
        )
        return new_signals

    async def get_recent_signals(
        self,
        db: AsyncSession,
        hours: int = 24,
        min_impact: str = "medium",
        limit: int = 50,
    ) -> List[NewsSignal]:
        """Retorna señales recientes filtradas por impacto."""
        impact_levels = {"low": 0, "medium": 1, "high": 2, "critical": 3}
        min_level = impact_levels.get(min_impact, 1)

        since = datetime.now(timezone.utc) - timedelta(hours=hours)

        result = await db.execute(
            select(NewsSignal)
            .where(
                NewsSignal.processed_at >= since,
                NewsSignal.impact_level.in_([
                    k for k, v in impact_levels.items() if v >= min_level
                ]),
            )
            .order_by(NewsSignal.processed_at.desc())
            .limit(limit)
        )
        return result.scalars().all()


# Singleton
news_service = NewsService()
