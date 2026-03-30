"""
News Scraper - MeroLagani, ShareSansar, OnlineKhabar.
Pulls latest news for each portfolio stock and caches it.
"""
import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'lib'))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

import json
import hashlib
import requests
from bs4 import BeautifulSoup
from datetime import datetime, timedelta
from typing import List, Optional

NEWS_CACHE_DIR = os.path.join(os.path.dirname(__file__), '..', 'data', 'news')
os.makedirs(NEWS_CACHE_DIR, exist_ok=True)

HEADERS = {
    "User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 Chrome/120.0.0.0 Safari/537.36"
}


def _cache_path(symbol: str, date_str: str) -> str:
    return os.path.join(NEWS_CACHE_DIR, f"{symbol}_{date_str}.json")


def _load_cache(symbol: str, max_age_hours: int = 6) -> Optional[List[dict]]:
    today = datetime.now().strftime("%Y%m%d")
    path = _cache_path(symbol, today)
    if os.path.exists(path):
        age = (datetime.now().timestamp() - os.path.getmtime(path)) / 3600
        if age < max_age_hours:
            with open(path) as f:
                return json.load(f)
    return None


def _save_cache(symbol: str, news: List[dict]):
    today = datetime.now().strftime("%Y%m%d")
    path = _cache_path(symbol, today)
    with open(path, "w") as f:
        json.dump(news, f, indent=2, default=str)


def scrape_merolagani_news(symbol: str) -> List[dict]:
    """Scrape company news from MeroLagani company page."""
    news = []
    try:
        url = f"https://merolagani.com/CompanyDetail.aspx?companySymbol={symbol}"
        r = requests.get(url, headers=HEADERS, timeout=15)
        soup = BeautifulSoup(r.text, "html.parser")

        # Find news section
        news_items = soup.find_all("div", class_="media") or \
                     soup.find_all("li", class_="media-list") or \
                     soup.select(".company-news li, .news-list li, #ctl00_ContentPlaceHolder1_divNews li")

        for item in news_items[:10]:
            title_tag = item.find("a") or item.find("h4") or item.find("h5")
            date_tag = item.find("span", class_="date") or item.find("small")
            if title_tag:
                news.append({
                    "symbol": symbol,
                    "title": title_tag.get_text(strip=True),
                    "date": date_tag.get_text(strip=True) if date_tag else "",
                    "source": "MeroLagani",
                    "url": url,
                    "sentiment": None,
                })

        # Also try the announcements API
        ann_url = f"https://merolagani.com/handlers/webrequesthandler.ashx?type=company_floorsheet&symbol={symbol}&page=1"
        r2 = requests.get(ann_url, headers=HEADERS, timeout=10)
        try:
            data = r2.json()
            if isinstance(data, dict) and "d" in data:
                for item in (data["d"] or [])[:5]:
                    news.append({
                        "symbol": symbol,
                        "title": str(item.get("remarks", item.get("description", "Market activity"))),
                        "date": str(item.get("date", "")),
                        "source": "MeroLagani Floorsheet",
                        "url": ann_url,
                        "sentiment": None,
                    })
        except Exception:
            pass

    except Exception as e:
        print(f"[!] MeroLagani news error ({symbol}): {e}")

    return news


def scrape_sharesansar_news(symbol: str) -> List[dict]:
    """Scrape news from ShareSansar."""
    news = []
    try:
        url = f"https://www.sharesansar.com/company/{symbol.lower()}"
        r = requests.get(url, headers=HEADERS, timeout=15)
        soup = BeautifulSoup(r.text, "html.parser")

        # News articles on company page
        articles = soup.select(".featured-news-list li, .news-list li, article.news-item")
        for a in articles[:8]:
            title_tag = a.find("a")
            date_tag = a.find("span", class_="date") or a.find("time")
            if title_tag:
                news.append({
                    "symbol": symbol,
                    "title": title_tag.get_text(strip=True),
                    "date": date_tag.get_text(strip=True) if date_tag else "",
                    "source": "ShareSansar",
                    "url": f"https://www.sharesansar.com" + (title_tag.get("href", "")),
                    "sentiment": None,
                })
    except Exception as e:
        print(f"[!] ShareSansar news error ({symbol}): {e}")
    return news


def scrape_general_nepse_news() -> List[dict]:
    """Scrape general NEPSE market news from ShareSansar."""
    news = []
    try:
        url = "https://www.sharesansar.com/category/latest"
        r = requests.get(url, headers=HEADERS, timeout=15)
        soup = BeautifulSoup(r.text, "html.parser")
        articles = soup.select("div.featured-news-list li, .news-wrap article")
        for a in articles[:15]:
            title_tag = a.find("a")
            date_tag = a.find("span", class_="date") or a.find("time")
            if title_tag and title_tag.get_text(strip=True):
                news.append({
                    "symbol": "MARKET",
                    "title": title_tag.get_text(strip=True),
                    "date": date_tag.get_text(strip=True) if date_tag else "",
                    "source": "ShareSansar",
                    "url": "https://www.sharesansar.com" + (title_tag.get("href", "")),
                    "sentiment": None,
                })
    except Exception as e:
        print(f"[!] General news error: {e}")
    return news


def get_latest_news(symbol: Optional[str] = None, days: int = 3, use_cache: bool = True) -> List[dict]:
    """
    Get latest news for a symbol (or all portfolio stocks if symbol=None).
    Returns list of news items sorted by date desc.
    """
    symbols = [symbol] if symbol else list(__import__('portfolio.config', fromlist=['PORTFOLIO']).PORTFOLIO.keys())
    all_news = []

    for sym in symbols:
        # Try cache first
        if use_cache:
            cached = _load_cache(sym)
            if cached:
                all_news.extend(cached)
                continue

        # Scrape fresh
        sym_news = []
        sym_news.extend(scrape_merolagani_news(sym))
        sym_news.extend(scrape_sharesansar_news(sym))
        _save_cache(sym, sym_news)
        all_news.extend(sym_news)

    # Add general market news
    market_news = _load_cache("MARKET")
    if not market_news or not use_cache:
        market_news = scrape_general_nepse_news()
        _save_cache("MARKET", market_news)
    all_news.extend(market_news)

    return all_news[:50]  # Return top 50 most recent


if __name__ == "__main__":
    import sys
    sym = sys.argv[1] if len(sys.argv) > 1 else "ALICL"
    print(f"\nFetching news for {sym}...")
    news = get_latest_news(sym, use_cache=False)
    for n in news[:10]:
        print(f"\n[{n['source']}] {n['date']}")
        print(f"  {n['title']}")
