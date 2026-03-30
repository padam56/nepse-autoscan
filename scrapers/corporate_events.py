"""
scrapers/corporate_events.py -- Corporate Events Calendar

Scrapes corporate events from Sharesansar that affect stock prices:
- Book closure dates (dividends, rights, bonus)
- Rights share announcements
- Bonus share announcements
- AGM dates
- Merger/acquisition news

These events create predictable price patterns (run-up before book closure,
drop after ex-date) that the scanner should account for.
"""

import json
import os
import re
from datetime import datetime, timedelta, timezone
from pathlib import Path

import requests
from bs4 import BeautifulSoup

ROOT = Path(__file__).resolve().parent.parent
CACHE_FILE = ROOT / "data" / "corporate_events.json"

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml,*/*",
    "Accept-Language": "en-US,en;q=0.9",
}

# Category URLs on Sharesansar
CATEGORY_URLS = {
    "dividend":    "https://www.sharesansar.com/category/dividend",
    "right-share": "https://www.sharesansar.com/category/right-share",
    "bonus-share": "https://www.sharesansar.com/category/bonus-share",
    "agm":         "https://www.sharesansar.com/category/agm",
}

# Book closure pages (more structured data)
BOOK_CLOSURE_URL = "https://www.sharesansar.com/book-closure"


class CorporateEventScraper:
    """Scrape and cache upcoming corporate events from Sharesansar."""

    CACHE_FILE = CACHE_FILE
    CACHE_MAX_AGE_HOURS = 12

    def __init__(self):
        self.session = requests.Session()
        self.session.headers.update(HEADERS)
        os.makedirs(self.CACHE_FILE.parent, exist_ok=True)

    # -- Public API --------------------------------------------------------

    def fetch_events(self, days_ahead=30) -> list:
        """Fetch upcoming corporate events.

        Tries cache first (if fresh enough), otherwise scrapes live.
        Returns list of dicts with keys:
            symbol, event_type, date, details, source_url
        """
        cached = self._load_cache()
        if cached is not None:
            return self._filter_upcoming(cached, days_ahead)

        events = []
        events.extend(self._scrape_book_closures())
        for category, url in CATEGORY_URLS.items():
            try:
                events.extend(self._scrape_category(category, url))
            except Exception as e:
                print(f"[EVENTS] Failed to scrape {category}: {e}")

        # Deduplicate by (symbol, event_type, date)
        seen = set()
        unique = []
        for ev in events:
            key = (ev.get("symbol", ""), ev.get("event_type", ""), ev.get("date", ""))
            if key not in seen:
                seen.add(key)
                unique.append(ev)

        self._save_cache(unique)
        return self._filter_upcoming(unique, days_ahead)

    def get_upcoming_for_symbol(self, symbol: str) -> list:
        """Get events for a specific stock in the next 30 days."""
        symbol = symbol.upper()
        events = self.fetch_events(days_ahead=30)
        return [e for e in events if e.get("symbol", "").upper() == symbol]

    def get_exclusion_list(self, days_threshold=5) -> list:
        """Return symbols that should be avoided (book closure within N days).

        Buying before book closure at a premium means instant loss after
        ex-date when the price adjusts downward.
        """
        events = self.fetch_events(days_ahead=days_threshold)
        exclusions = []
        for ev in events:
            if ev.get("event_type") in ("book_closure", "dividend", "bonus", "right"):
                exclusions.append(ev["symbol"])
        return list(set(exclusions))

    def format_warnings(self, picks: list) -> list:
        """Check if any picks have upcoming corporate events.

        Args:
            picks: list of pick dicts, each with a 'symbol' key

        Returns:
            Warning strings for each affected pick.
        """
        events = self.fetch_events(days_ahead=15)
        if not events:
            return []

        event_map = {}
        for ev in events:
            sym = ev.get("symbol", "").upper()
            if sym not in event_map:
                event_map[sym] = []
            event_map[sym].append(ev)

        warnings = []
        for pick in picks:
            sym = pick.get("symbol", "").upper()
            if sym in event_map:
                for ev in event_map[sym]:
                    ev_type = ev.get("event_type", "event").replace("_", " ").title()
                    ev_date = ev.get("date", "unknown date")
                    details = ev.get("details", "")
                    msg = f"[WARNING] {sym}: {ev_type} on {ev_date}"
                    if details:
                        msg += f" -- {details}"
                    warnings.append(msg)

        return warnings

    # -- Scraping methods --------------------------------------------------

    def _scrape_book_closures(self) -> list:
        """Scrape the book closure table from Sharesansar."""
        events = []
        try:
            resp = self.session.get(BOOK_CLOSURE_URL, timeout=30)
            resp.raise_for_status()
        except requests.RequestException as e:
            print(f"[EVENTS] Book closure fetch failed: {e}")
            return events

        soup = BeautifulSoup(resp.text, "lxml")
        table = soup.find("table", class_="table")
        if not table:
            # Try any table on the page
            table = soup.find("table")
        if not table:
            print("[EVENTS] No book closure table found")
            return events

        rows = table.find_all("tr")
        for row in rows[1:]:  # skip header
            cells = row.find_all("td")
            if len(cells) < 4:
                continue
            try:
                symbol = cells[1].get_text(strip=True).upper()
                # Clean symbol: remove any extra text
                symbol = re.split(r"\s", symbol)[0] if symbol else ""
                if not symbol or len(symbol) > 10:
                    continue

                date_text = cells[2].get_text(strip=True) if len(cells) > 2 else ""
                event_date = self._parse_date(date_text)

                details = cells[3].get_text(strip=True) if len(cells) > 3 else ""

                # Determine event sub-type from details
                event_type = "book_closure"
                details_lower = details.lower()
                if "bonus" in details_lower:
                    event_type = "bonus"
                elif "right" in details_lower:
                    event_type = "right"
                elif "dividend" in details_lower or "cash" in details_lower:
                    event_type = "dividend"

                events.append({
                    "symbol": symbol,
                    "event_type": event_type,
                    "date": event_date,
                    "details": details,
                    "source_url": BOOK_CLOSURE_URL,
                })
            except Exception:
                continue

        print(f"[EVENTS] Scraped {len(events)} book closure entries")
        return events

    def _scrape_category(self, category: str, url: str) -> list:
        """Scrape news articles from a Sharesansar category page."""
        events = []
        try:
            resp = self.session.get(url, timeout=30)
            resp.raise_for_status()
        except requests.RequestException as e:
            print(f"[EVENTS] {category} fetch failed: {e}")
            return events

        soup = BeautifulSoup(resp.text, "lxml")

        # Sharesansar category pages list articles with titles
        articles = soup.find_all("div", class_="featured-news-list")
        if not articles:
            articles = soup.find_all("div", class_="news-list")
        if not articles:
            # Fallback: look for article links
            articles = soup.find_all("article")

        event_type_map = {
            "dividend": "dividend",
            "right-share": "right",
            "bonus-share": "bonus",
            "agm": "agm",
        }
        event_type = event_type_map.get(category, category)

        for article in articles[:20]:  # limit to recent articles
            try:
                # Get the title/link
                link_tag = article.find("a")
                if not link_tag:
                    continue
                title = link_tag.get_text(strip=True)
                href = link_tag.get("href", "")

                # Try to extract symbol from title
                symbol = self._extract_symbol_from_title(title)
                if not symbol:
                    continue

                # Try to extract date from the article
                date_tag = article.find("span", class_="date")
                if not date_tag:
                    date_tag = article.find("time")
                date_text = date_tag.get_text(strip=True) if date_tag else ""
                event_date = self._parse_date(date_text) if date_text else ""

                events.append({
                    "symbol": symbol,
                    "event_type": event_type,
                    "date": event_date,
                    "details": title,
                    "source_url": href if href.startswith("http") else f"https://www.sharesansar.com{href}",
                })
            except Exception:
                continue

        print(f"[EVENTS] Scraped {len(events)} {category} articles")
        return events

    # -- Helpers -----------------------------------------------------------

    def _extract_symbol_from_title(self, title: str) -> str:
        """Try to extract a NEPSE stock symbol from an article title.

        Common patterns:
        - "NLIC announces 20% dividend"
        - "Asian Life Insurance (ALICL) book closure"
        - "Bonus share of BARUN"
        """
        if not title:
            return ""

        # Pattern 1: symbol in parentheses like (ALICL)
        paren_match = re.search(r"\(([A-Z]{2,10})\)", title)
        if paren_match:
            return paren_match.group(1)

        # Pattern 2: first word is all-caps and looks like a symbol
        words = title.split()
        if words:
            first = words[0].strip(",:;-")
            if re.match(r"^[A-Z]{2,10}$", first):
                return first

        # Pattern 3: "of SYMBOL" or "by SYMBOL"
        prep_match = re.search(r"\b(?:of|by|from)\s+([A-Z]{2,10})\b", title)
        if prep_match:
            return prep_match.group(1)

        return ""

    def _parse_date(self, text: str) -> str:
        """Parse various date formats into YYYY-MM-DD string.

        Handles:
        - 2026-03-28
        - March 28, 2026
        - 28 Mar 2026
        - 2082/12/15 (Bikram Sambat -- detected and skipped)
        """
        if not text:
            return ""

        text = text.strip()

        # Detect Bikram Sambat dates (year > 2050) and skip them.
        # BS years are typically 2070-2090+ range. These cannot be parsed
        # by datetime.strptime and would crash downstream code.
        bs_match = re.match(r"(\d{4})[/\-](\d{1,2})[/\-](\d{1,2})", text)
        if bs_match and int(bs_match.group(1)) > 2050:
            return ""

        # Already ISO format
        iso_match = re.match(r"(\d{4}-\d{2}-\d{2})", text)
        if iso_match:
            return iso_match.group(1)

        # Common English date formats
        for fmt in ("%B %d, %Y", "%b %d, %Y", "%d %B %Y", "%d %b %Y",
                     "%Y/%m/%d", "%m/%d/%Y", "%d/%m/%Y"):
            try:
                dt = datetime.strptime(text, fmt)
                return dt.strftime("%Y-%m-%d")
            except ValueError:
                continue

        # Unrecognized format -- return empty to avoid downstream crashes
        return ""

    def _load_cache(self) -> list | None:
        """Load cached events if cache is fresh enough."""
        if not self.CACHE_FILE.exists():
            return None
        try:
            data = json.loads(self.CACHE_FILE.read_text())
            cached_at = data.get("cached_at", "")
            if cached_at:
                cache_time = datetime.fromisoformat(cached_at)
                # Ensure both datetimes are timezone-aware or both naive
                now = datetime.now(cache_time.tzinfo) if cache_time.tzinfo else datetime.now()
                age_hours = (now - cache_time).total_seconds() / 3600
                if age_hours < self.CACHE_MAX_AGE_HOURS:
                    return data.get("events", [])
        except (json.JSONDecodeError, ValueError, OSError):
            pass
        return None

    def _save_cache(self, events: list) -> None:
        """Save events to cache file."""
        try:
            payload = {
                "cached_at": datetime.now().isoformat(),
                "count": len(events),
                "events": events,
            }
            self.CACHE_FILE.write_text(json.dumps(payload, indent=2, default=str))
        except OSError as e:
            print(f"[EVENTS] Cache save failed: {e}")

    def _filter_upcoming(self, events: list, days_ahead: int) -> list:
        """Filter events to only those within the next N days."""
        NPT = timezone(timedelta(hours=5, minutes=45))
        today = datetime.now(NPT).date()
        cutoff = today + timedelta(days=days_ahead)

        result = []
        for ev in events:
            date_str = ev.get("date", "")
            if not date_str:
                # No date parsed -- include it anyway (manual review)
                result.append(ev)
                continue
            try:
                ev_date = datetime.strptime(date_str, "%Y-%m-%d").date()
                if today <= ev_date <= cutoff:
                    result.append(ev)
            except ValueError:
                # Unparseable date (BS calendar etc) -- include for safety
                result.append(ev)

        return result


if __name__ == "__main__":
    scraper = CorporateEventScraper()

    print("Fetching corporate events...")
    events = scraper.fetch_events(days_ahead=30)
    print(f"Found {len(events)} upcoming events\n")

    for ev in events[:20]:
        print(f"  {ev.get('symbol', '?'):<8} {ev.get('event_type', '?'):<14} "
              f"{ev.get('date', '?'):<12} {ev.get('details', '')[:60]}")

    print(f"\nExclusion list (book closure within 5 days):")
    excluded = scraper.get_exclusion_list()
    print(f"  {', '.join(excluded) if excluded else '(none)'}")
