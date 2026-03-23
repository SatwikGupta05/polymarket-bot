"""
Sentiment Analyzer — Free-Tier Compatible
==========================================
FIX: Original used OpenRouter API key (→ 401 error when key is absent).
Now routes through free_model_client (Groq/Gemini) when no paid keys exist,
and gracefully disables itself when no AI keys are available at all.
"""

import asyncio
import hashlib
from dataclasses import dataclass
from typing import Dict, List, Optional

from src.utils.logging_setup import TradingLoggerMixin
from src.data.news_aggregator import NewsAggregator, NewsArticle


@dataclass
class SentimentResult:
    score: float       # -1.0 to +1.0
    confidence: float  # 0.0 to 1.0
    reasoning: str


@dataclass
class ArticleSentiment:
    article: NewsArticle
    sentiment: SentimentResult
    relevance_score: float


@dataclass
class MarketSentiment:
    overall_score: float
    article_sentiments: List[ArticleSentiment]
    relevance_weighted_score: float
    num_articles: int


class SentimentAnalyzer(TradingLoggerMixin):
    """
    AI-powered sentiment scorer.
    Works with free-tier (Groq/Gemini) OR paid (OpenRouter/XAI).
    Falls back to keyword-based scoring when no AI keys are available.
    """

    def __init__(self, news_aggregator: Optional[NewsAggregator] = None) -> None:
        self._news  = news_aggregator or NewsAggregator()
        self._cache: Dict[str, SentimentResult] = {}
        self.total_cost: float = 0.0
        self.request_count: int = 0
        self.logger.info("SentimentAnalyzer initialized (free-tier compatible)")

    async def _score_text(self, text: str, market_title: str) -> SentimentResult:
        """Score text sentiment. Uses AI if available, keywords otherwise."""
        from src.clients.free_model_client import active_tier, get_free_completion
        import os

        tier = active_tier()

        # ── AI path (free or paid) ────────────────────────────────────────
        if tier in ("free", "paid"):
            prompt = (
                f"Market question: {market_title}\n\n"
                f"News text: {text[:500]}\n\n"
                "Does this news make the market question MORE or LESS likely to resolve YES?\n"
                "Respond ONLY with JSON: "
                '{"score": 0.7, "confidence": 0.8, "reasoning": "brief"}\n'
                "score: -1.0 (very negative for YES) to +1.0 (very positive for YES)"
            )
            try:
                if tier == "free":
                    raw = await get_free_completion(prompt)
                else:
                    # Try OpenRouter then XAI
                    raw = await self._paid_completion(prompt)

                if raw:
                    import json, re
                    m = re.search(r'\{.*\}', raw, re.DOTALL)
                    if m:
                        d = json.loads(m.group(0))
                        return SentimentResult(
                            score      = float(d.get("score", 0.0)),
                            confidence = float(d.get("confidence", 0.5)),
                            reasoning  = str(d.get("reasoning", "")),
                        )
            except Exception as e:
                self.logger.debug(f"AI sentiment failed: {e}")

        # ── Keyword fallback ──────────────────────────────────────────────
        return self._keyword_score(text)

    async def _paid_completion(self, prompt: str) -> Optional[str]:
        """Try OpenRouter or XAI for paid-tier sentiment."""
        import os
        openrouter_key = os.getenv("OPENROUTER_API_KEY", "")
        if openrouter_key:
            try:
                import httpx
                resp = httpx.post(
                    "https://openrouter.ai/api/v1/chat/completions",
                    headers={"Authorization": f"Bearer {openrouter_key}",
                             "Content-Type": "application/json"},
                    json={"model": "google/gemini-flash-1.5",
                          "messages": [{"role": "user", "content": prompt}],
                          "temperature": 0, "max_tokens": 200},
                    timeout=20,
                )
                if resp.status_code == 200:
                    return resp.json()["choices"][0]["message"]["content"]
            except Exception:
                pass
        return None

    def _keyword_score(self, text: str) -> SentimentResult:
        """Fast keyword-based sentiment when no AI available."""
        text_lower = text.lower()
        positive = ["wins", "approved", "passes", "confirmed", "yes", "likely",
                    "expected", "beats", "exceeds", "rises", "increases", "gains"]
        negative = ["fails", "rejected", "unlikely", "no", "drops", "falls",
                    "loses", "denied", "below", "misses", "declines", "crashes"]
        pos = sum(1 for w in positive if w in text_lower)
        neg = sum(1 for w in negative if w in text_lower)
        total = pos + neg
        if total == 0:
            return SentimentResult(0.0, 0.3, "no keywords found")
        score = (pos - neg) / total
        return SentimentResult(round(score, 2), 0.5, f"{pos}+ {neg}- keywords")

    async def get_market_sentiment_summary(self, market_title: str) -> str:
        """
        Return a short news sentiment summary string for a market.
        Used by decide.py as the news_summary input.
        """
        try:
            await asyncio.wait_for(self._news.fetch_all(), timeout=15.0)
            relevant_articles = self._news.get_relevant_articles(market_title, max_articles=3)
            articles = [article for article, _score in relevant_articles]
        except Exception:
            articles = []

        if not articles:
            return f"No recent news found for: {market_title}"

        summaries = []
        for article in articles[:3]:
            key = hashlib.md5(f"{article.title}{market_title}".encode()).hexdigest()
            if key not in self._cache:
                self._cache[key] = await self._score_text(
                    f"{article.title}. {article.summary}", market_title
                )
            result = self._cache[key]
            direction = "positive" if result.score > 0.1 else (
                        "negative" if result.score < -0.1 else "neutral")
            summaries.append(f"• {article.title[:80]} [{direction}]")

        return "\n".join(summaries) if summaries else f"Limited news context for: {market_title}"
