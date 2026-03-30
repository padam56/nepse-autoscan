"""
NEPSE News Sentiment Analyzer.
Scrapes financial news from ShareSansar and MeroLagani, computes per-stock
sentiment scores using keyword-based analysis (with optional LLM scoring).
"""
import sys
import os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'lib'))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

import json
import re
import time
import requests
from bs4 import BeautifulSoup
from pathlib import Path
from datetime import datetime, timedelta
from typing import List, Dict, Optional, Tuple

from scrapers.sentiment_keywords import (
    BULLISH_KEYWORDS, BEARISH_KEYWORDS,
    BULLISH_WEIGHTED, BEARISH_WEIGHTED,
    COMPANY_ALIASES, SECTOR_KEYWORDS,
)

ROOT = Path(__file__).resolve().parent.parent
CACHE_FILE = ROOT / 'data' / 'news_sentiment.json'
SECTORS_FILE = ROOT / 'data' / 'sectors.json'
DATA_DIR = ROOT / 'data'

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
        "Chrome/120.0.0.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.5",
}

REQUEST_DELAY = 0.5  # seconds between requests


class NEPSESentimentAnalyzer:
    """Scrape NEPSE financial news and compute per-stock sentiment scores."""

    def __init__(self):
        self.session = requests.Session()
        self.session.headers.update(HEADERS)
        self._known_symbols = set()
        self._symbol_to_sector = {}
        self._load_symbols()

    def _load_symbols(self):
        """Load all known NEPSE symbols from sectors.json."""
        try:
            with open(SECTORS_FILE) as f:
                data = json.load(f)
            # Collect all symbols from sector lists
            for sector, symbols in data.get('sectors', {}).items():
                for sym in symbols:
                    self._known_symbols.add(sym.upper())
                    self._symbol_to_sector[sym.upper()] = sector
            # Also load from symbol_to_sector mapping if present
            for sym, sector in data.get('symbol_to_sector', {}).items():
                self._known_symbols.add(sym.upper())
                self._symbol_to_sector[sym.upper()] = sector
        except (FileNotFoundError, json.JSONDecodeError) as e:
            print(f"[!] Could not load sectors.json: {e}")

    # ------------------------------------------------------------------
    # News Fetching
    # ------------------------------------------------------------------

    def fetch_news(self, days: int = 3) -> List[dict]:
        """Fetch recent news headlines from ShareSansar and MeroLagani.

        Returns list of dicts with keys: title, source, date, url
        Deduplicates by normalized title text.
        """
        all_articles = []

        # Fetch from both sources
        ss_articles = self._fetch_sharesansar(pages=min(days, 5))
        all_articles.extend(ss_articles)

        time.sleep(REQUEST_DELAY)

        ml_articles = self._fetch_merolagani(pages=min(days, 5))
        all_articles.extend(ml_articles)

        # Deduplicate by normalized title
        seen = set()
        unique = []
        for article in all_articles:
            key = self._normalize_title(article['title'])
            if key and key not in seen:
                seen.add(key)
                unique.append(article)

        return unique

    def _fetch_sharesansar(self, pages: int = 3) -> List[dict]:
        """Scrape news from ShareSansar latest news pages."""
        articles = []
        base_url = "https://www.sharesansar.com/category/latest"

        for page in range(1, pages + 1):
            try:
                url = base_url if page == 1 else f"{base_url}?page={page}"
                resp = self.session.get(url, timeout=15)
                resp.raise_for_status()
                soup = BeautifulSoup(resp.text, 'html.parser')

                # Try multiple selectors for robustness
                items = (
                    soup.select('div.featured-news-list li') or
                    soup.select('.news-wrap article') or
                    soup.select('.category-news .media') or
                    soup.select('div.news-list .media') or
                    soup.select('.latestnews-list li')
                )

                for item in items:
                    title_tag = item.find('a')
                    date_tag = (
                        item.find('span', class_='date') or
                        item.find('time') or
                        item.find('span', class_='text-muted')
                    )
                    if title_tag and title_tag.get_text(strip=True):
                        title = title_tag.get_text(strip=True)
                        href = title_tag.get('href', '')
                        article_url = href if href.startswith('http') else f"https://www.sharesansar.com{href}"
                        articles.append({
                            'title': title,
                            'source': 'ShareSansar',
                            'date': date_tag.get_text(strip=True) if date_tag else '',
                            'url': article_url,
                        })

                if page < pages:
                    time.sleep(REQUEST_DELAY)

            except requests.RequestException as e:
                print(f"[!] ShareSansar page {page} error: {e}")
                break
            except Exception as e:
                print(f"[!] ShareSansar parse error (page {page}): {e}")
                break

        return articles

    def _fetch_merolagani(self, pages: int = 3) -> List[dict]:
        """Scrape news from MeroLagani news list."""
        articles = []
        base_url = "https://merolagani.com/NewsList.aspx"

        for page in range(1, pages + 1):
            try:
                if page == 1:
                    resp = self.session.get(base_url, timeout=15)
                else:
                    # MeroLagani uses ASP.NET postback for pagination.
                    # We attempt a simple GET with page param; if that fails,
                    # we just use the first page.
                    resp = self.session.get(
                        f"{base_url}?page={page}", timeout=15
                    )
                resp.raise_for_status()
                soup = BeautifulSoup(resp.text, 'html.parser')

                # Try multiple selectors
                items = (
                    soup.select('.media-news .media') or
                    soup.select('.news-list .media') or
                    soup.select('#newslist .media') or
                    soup.select('.category-list .media') or
                    soup.select('div.latest-news-list li')
                )

                for item in items:
                    title_tag = item.find('a')
                    date_tag = (
                        item.find('span', class_='date') or
                        item.find('small') or
                        item.find('span', class_='text-muted')
                    )
                    if title_tag and title_tag.get_text(strip=True):
                        title = title_tag.get_text(strip=True)
                        href = title_tag.get('href', '')
                        if href and not href.startswith('http'):
                            article_url = f"https://merolagani.com/{href.lstrip('/')}"
                        else:
                            article_url = href or base_url
                        articles.append({
                            'title': title,
                            'source': 'MeroLagani',
                            'date': date_tag.get_text(strip=True) if date_tag else '',
                            'url': article_url,
                        })

                if page < pages:
                    time.sleep(REQUEST_DELAY)

            except requests.RequestException as e:
                print(f"[!] MeroLagani page {page} error: {e}")
                break
            except Exception as e:
                print(f"[!] MeroLagani parse error (page {page}): {e}")
                break

        return articles

    @staticmethod
    def _normalize_title(title: str) -> str:
        """Normalize a headline for deduplication."""
        text = title.lower().strip()
        text = re.sub(r'[^\w\s]', '', text)
        text = re.sub(r'\s+', ' ', text)
        return text

    # ------------------------------------------------------------------
    # Symbol Extraction
    # ------------------------------------------------------------------

    def extract_symbols(self, headline: str) -> List[str]:
        """Extract NEPSE stock symbols mentioned in a headline.

        Checks for:
        1. Exact symbol matches (uppercase words that are known symbols)
        2. Company name aliases (e.g. 'Nabil Bank' -> 'NABIL')
        3. Parenthetical symbols like '(NLIC)'
        4. Sector-level keywords returning all sector stocks
        """
        found = set()
        headline_upper = headline.upper()
        headline_lower = headline.lower()

        # 1. Check for parenthetical symbols: "(NABIL)", "(HBL)" etc.
        paren_matches = re.findall(r'\(([A-Z]{2,10})\)', headline)
        for match in paren_matches:
            if match in self._known_symbols:
                found.add(match)

        # 2. Check for exact symbol matches as whole words
        words = re.findall(r'\b([A-Z]{2,10})\b', headline_upper)
        for word in words:
            if word in self._known_symbols:
                # Avoid false positives for very short common words
                if len(word) >= 3 or word in ('NTC', 'API', 'NMB', 'HBL',
                                                'EBL', 'NBL', 'KBL', 'SBI',
                                                'SCB', 'CBL', 'MBL', 'LBL',
                                                'NCC', 'SHL', 'OHL', 'NIL',
                                                'GIC', 'SIL', 'CHL', 'UML',
                                                'HDL', 'BNL', 'GHL', 'TPC',
                                                'NBI', 'HGI', 'BGI', 'IGI',
                                                'SGI', 'GFL', 'JFL', 'NFL',
                                                'NFS', 'PFL', 'SFL', 'UFL',
                                                'SEF'):
                    found.add(word)

        # 3. Check company name aliases
        for alias, symbol in COMPANY_ALIASES.items():
            if alias in headline_lower:
                found.add(symbol)

        return sorted(found)

    # ------------------------------------------------------------------
    # Sentiment Scoring
    # ------------------------------------------------------------------

    def score_sentiment(self, headline: str, use_llm: bool = False) -> float:
        """Score a headline's sentiment from -1.0 (bearish) to +1.0 (bullish).

        Tries LLM-based scoring first if use_llm=True, falls back to keyword.
        """
        if use_llm:
            llm_score = self._score_with_llm(headline)
            if llm_score is not None:
                return llm_score

        return self._score_with_keywords(headline)

    def _score_with_keywords(self, headline: str) -> float:
        """Keyword-based sentiment scoring.

        Checks weighted keywords first for strong signals, then falls back
        to simple keyword counting.
        """
        text = headline.lower()
        score = 0.0
        matches = 0

        # Check weighted keywords first (stronger signals)
        for keyword, weight in BULLISH_WEIGHTED:
            if keyword.lower() in text:
                score += weight
                matches += 1

        for keyword, weight in BEARISH_WEIGHTED:
            if keyword.lower() in text:
                score += weight  # weight is already negative
                matches += 1

        # If weighted keywords gave a strong signal, use that
        if matches > 0 and abs(score) >= 0.5:
            return max(-1.0, min(1.0, score))

        # Fall back to simple keyword counting
        bullish_count = 0
        bearish_count = 0

        for kw in BULLISH_KEYWORDS:
            if kw.lower() in text:
                bullish_count += 1

        for kw in BEARISH_KEYWORDS:
            if kw.lower() in text:
                bearish_count += 1

        total = bullish_count + bearish_count
        if total == 0:
            return 0.0

        # Compute score as normalized difference
        raw = (bullish_count - bearish_count) / total
        # Scale by confidence (more keyword hits = more confident)
        confidence = min(1.0, total / 3.0)
        return round(raw * confidence, 3)

    def _score_with_llm(self, headline: str) -> Optional[float]:
        """Score sentiment using Ollama/Qwen LLM.

        Returns float in [-1.0, 1.0] or None if LLM is unavailable.
        """
        try:
            prompt = (
                "You are a NEPSE (Nepal Stock Exchange) financial analyst. "
                "Rate the sentiment of this news headline on a scale from "
                "-1.0 (very bearish/negative for stock price) to "
                "+1.0 (very bullish/positive for stock price). "
                "Reply with ONLY a number, nothing else.\n\n"
                f"Headline: {headline}\n\nSentiment score:"
            )

            resp = requests.post(
                "http://localhost:11434/api/generate",
                json={
                    "model": "qwen2.5:7b",
                    "prompt": prompt,
                    "stream": False,
                    "options": {"temperature": 0.1, "num_predict": 10},
                },
                timeout=30,
            )

            if resp.status_code == 200:
                result = resp.json()
                text = result.get('response', '').strip()
                # Extract the numeric score
                match = re.search(r'(-?\d+\.?\d*)', text)
                if match:
                    val = float(match.group(1))
                    return max(-1.0, min(1.0, val))
        except (requests.RequestException, ValueError, KeyError):
            pass

        return None

    # ------------------------------------------------------------------
    # Full Pipeline
    # ------------------------------------------------------------------

    def analyze(self, days: int = 3, use_llm: bool = False) -> dict:
        """Full pipeline: fetch news -> extract symbols -> score sentiment.

        Args:
            days: Number of days of news to fetch (controls pagination depth).
            use_llm: If True, attempt LLM-based sentiment scoring first.

        Returns dict with:
            - date: analysis date
            - articles: list of all processed articles
            - per_stock: {symbol: {score, articles, headlines}}
            - market_sentiment: average sentiment across all articles
        """
        print(f"[*] Fetching news (last {days} days)...")
        articles = self.fetch_news(days=days)
        print(f"[*] Found {len(articles)} unique articles")

        processed = []
        per_stock: Dict[str, dict] = {}
        total_score = 0.0
        scored_count = 0

        for article in articles:
            title = article['title']
            sentiment = self.score_sentiment(title, use_llm=use_llm)
            symbols = self.extract_symbols(title)

            entry = {
                'title': title,
                'source': article['source'],
                'date': article['date'],
                'url': article['url'],
                'sentiment': sentiment,
                'symbols': symbols,
            }
            processed.append(entry)

            total_score += sentiment
            scored_count += 1

            # Accumulate per-stock data
            for sym in symbols:
                if sym not in per_stock:
                    per_stock[sym] = {
                        'score': 0.0,
                        'articles': 0,
                        'headlines': [],
                        'total_sentiment': 0.0,
                    }
                per_stock[sym]['articles'] += 1
                per_stock[sym]['total_sentiment'] += sentiment
                per_stock[sym]['headlines'].append(title)

        # Compute averages
        for sym, data in per_stock.items():
            if data['articles'] > 0:
                data['score'] = round(
                    data['total_sentiment'] / data['articles'], 3
                )
            del data['total_sentiment']

        market_sentiment = round(
            total_score / scored_count, 3
        ) if scored_count > 0 else 0.0

        result = {
            'date': datetime.now().strftime('%Y-%m-%d'),
            'generated_at': datetime.now().isoformat(),
            'total_articles': len(processed),
            'articles': processed,
            'per_stock': dict(sorted(per_stock.items())),
            'market_sentiment': market_sentiment,
        }

        # Cache results
        self._save_cache(result)

        print(f"[*] Market sentiment: {market_sentiment}")
        print(f"[*] Stocks with news: {len(per_stock)}")

        return result

    def get_sentiment_features(self, symbols: List[str]) -> Dict[str, dict]:
        """Return sentiment scores as features for the ML pipeline.

        Loads from cache if available and recent (< 6 hours old),
        otherwise runs a fresh analysis.

        Args:
            symbols: List of stock symbols to get features for.

        Returns:
            {symbol: {'news_sentiment': float, 'news_count': int}}
        """
        cached = self._load_cache(max_age_hours=6)

        if cached is None:
            print("[*] No recent sentiment cache, running analysis...")
            cached = self.analyze(days=3, use_llm=False)

        per_stock = cached.get('per_stock', {})
        market_sentiment = cached.get('market_sentiment', 0.0)

        features = {}
        for sym in symbols:
            sym = sym.upper()
            if sym in per_stock:
                features[sym] = {
                    'news_sentiment': per_stock[sym]['score'],
                    'news_count': per_stock[sym]['articles'],
                }
            else:
                # No direct news -- use market-level sentiment as fallback
                features[sym] = {
                    'news_sentiment': market_sentiment * 0.3,
                    'news_count': 0,
                }

        return features

    # ------------------------------------------------------------------
    # Caching
    # ------------------------------------------------------------------

    def _save_cache(self, data: dict):
        """Save analysis results to JSON cache."""
        try:
            DATA_DIR.mkdir(parents=True, exist_ok=True)
            with open(CACHE_FILE, 'w') as f:
                json.dump(data, f, indent=2, default=str)
            print(f"[*] Cached sentiment data to {CACHE_FILE}")
        except OSError as e:
            print(f"[!] Failed to save cache: {e}")

    def _load_cache(self, max_age_hours: int = 6) -> Optional[dict]:
        """Load cached results if they exist and are fresh enough."""
        try:
            if not CACHE_FILE.exists():
                return None
            age_hours = (
                time.time() - CACHE_FILE.stat().st_mtime
            ) / 3600
            if age_hours > max_age_hours:
                return None
            with open(CACHE_FILE) as f:
                return json.load(f)
        except (OSError, json.JSONDecodeError) as e:
            print(f"[!] Cache load error: {e}")
            return None

    # ------------------------------------------------------------------
    # Display helpers
    # ------------------------------------------------------------------

    def print_summary(self, result: dict):
        """Print a human-readable summary of sentiment analysis."""
        print("\n" + "=" * 60)
        print("  NEPSE News Sentiment Analysis")
        print(f"  Date: {result['date']}  |  Articles: {result['total_articles']}")
        print(f"  Market Sentiment: {result['market_sentiment']}")
        print("=" * 60)

        per_stock = result.get('per_stock', {})
        if per_stock:
            # Sort by absolute score descending
            sorted_stocks = sorted(
                per_stock.items(),
                key=lambda x: abs(x[1]['score']),
                reverse=True
            )
            print("\n  Per-Stock Sentiment (top movers):")
            print(f"  {'Symbol':<10} {'Score':>8} {'Articles':>10}")
            print("  " + "-" * 30)
            for sym, data in sorted_stocks[:20]:
                indicator = "+" if data['score'] > 0 else ""
                print(
                    f"  {sym:<10} {indicator}{data['score']:>7.3f} "
                    f"{data['articles']:>10}"
                )

        # Show most bullish and bearish headlines
        articles = result.get('articles', [])
        if articles:
            bullish = sorted(articles, key=lambda x: x['sentiment'], reverse=True)
            bearish = sorted(articles, key=lambda x: x['sentiment'])

            print("\n  Most Bullish Headlines:")
            for a in bullish[:3]:
                if a['sentiment'] > 0:
                    print(f"    [+{a['sentiment']:.2f}] {a['title'][:70]}")

            print("\n  Most Bearish Headlines:")
            for a in bearish[:3]:
                if a['sentiment'] < 0:
                    print(f"    [{a['sentiment']:.2f}] {a['title'][:70]}")

        print("\n" + "=" * 60)


# ------------------------------------------------------------------
# CLI Entry Point
# ------------------------------------------------------------------

if __name__ == '__main__':
    import argparse

    parser = argparse.ArgumentParser(
        description='NEPSE News Sentiment Analyzer'
    )
    parser.add_argument(
        '--days', type=int, default=3,
        help='Number of days of news to analyze (default: 3)'
    )
    parser.add_argument(
        '--llm', action='store_true',
        help='Use LLM (Ollama/Qwen) for sentiment scoring'
    )
    parser.add_argument(
        '--symbols', nargs='+', default=None,
        help='Get sentiment features for specific symbols'
    )
    parser.add_argument(
        '--cached', action='store_true',
        help='Use cached results only (do not fetch new news)'
    )

    args = parser.parse_args()
    analyzer = NEPSESentimentAnalyzer()

    if args.symbols:
        features = analyzer.get_sentiment_features(args.symbols)
        print("\nSentiment Features:")
        for sym, feat in features.items():
            print(f"  {sym}: sentiment={feat['news_sentiment']:.3f}, "
                  f"count={feat['news_count']}")
    elif args.cached:
        cached = analyzer._load_cache(max_age_hours=24)
        if cached:
            analyzer.print_summary(cached)
        else:
            print("[!] No cached data found. Run without --cached first.")
    else:
        result = analyzer.analyze(days=args.days, use_llm=args.llm)
        analyzer.print_summary(result)
