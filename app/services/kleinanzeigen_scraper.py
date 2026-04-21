"""
Kleinanzeigen scraper — fetches seller's active listings and extracts SKUs.

Uses cloudscraper to bypass Cloudflare protection.
Scrapes the public seller profile page, then fetches each ad's detail page
to extract the SKU from the description text.
"""

import re
import asyncio
import logging
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from typing import List, Dict, Any, Optional
from html.parser import HTMLParser

import cloudscraper

from app.config import settings

logger = logging.getLogger(__name__)

BASE_URL = "https://www.kleinanzeigen.de"

# Thread pool for running synchronous cloudscraper in async context
_executor = ThreadPoolExecutor(max_workers=2)


# ── HTML Parsers ────────────────────────────────────────────────────

class AdListParser(HTMLParser):
    """Parse the seller profile page to extract ad items."""

    def __init__(self):
        super().__init__()
        self.ads: List[Dict[str, str]] = []
        self._current_ad: Optional[Dict[str, str]] = None
        self._in_article = False
        self._in_title_link = False
        self._in_price = False
        self._capture_text = ""

    def handle_starttag(self, tag, attrs):
        attr_dict = dict(attrs)
        classes = attr_dict.get("class", "")

        if tag == "article" and "aditem" in classes:
            self._in_article = True
            self._current_ad = {"title": "", "price": "", "url": ""}
            return

        if not self._in_article or self._current_ad is None:
            return

        if tag == "a" and "ellipsis" in classes:
            self._in_title_link = True
            self._capture_text = ""
            href = attr_dict.get("href", "")
            if href:
                self._current_ad["url"] = href

        if "aditem-main--middle--price-shipping--price" in classes or "aditem-main--middle--price" in classes:
            self._in_price = True
            self._capture_text = ""

    def handle_endtag(self, tag):
        if tag == "article" and self._in_article:
            self._in_article = False
            if self._current_ad and self._current_ad.get("url"):
                self.ads.append(self._current_ad)
            self._current_ad = None
            return

        if self._in_title_link and tag == "a":
            self._in_title_link = False
            if self._current_ad is not None:
                self._current_ad["title"] = self._capture_text.strip()

        if self._in_price:
            self._in_price = False
            if self._current_ad is not None and self._capture_text.strip():
                self._current_ad["price"] = self._capture_text.strip()

    def handle_data(self, data):
        if self._in_title_link or self._in_price:
            self._capture_text += data


class AdDetailParser(HTMLParser):
    """Parse an individual ad page to extract description text."""

    def __init__(self):
        super().__init__()
        self.description: str = ""
        self._in_desc = False
        self._depth = 0
        self._capture = ""

    def handle_starttag(self, tag, attrs):
        attr_dict = dict(attrs)
        attr_id = attr_dict.get("id", "")

        if attr_id == "viewad-description-text":
            self._in_desc = True
            self._depth = 0
            self._capture = ""

        if self._in_desc:
            self._depth += 1
            if tag == "br":
                self._capture += "\n"

    def handle_endtag(self, tag):
        if self._in_desc:
            self._depth -= 1
            if self._depth <= 0:
                self._in_desc = False
                self.description = self._capture.strip()

    def handle_data(self, data):
        if self._in_desc:
            self._capture += data


# ── Helper functions ────────────────────────────────────────────────

def parse_price(price_str: str) -> Optional[float]:
    """Extract numeric price from string like '120 €' or '1.200 €'."""
    if not price_str:
        return None
    cleaned = price_str.replace("€", "").replace("VB", "").replace("\xa0", "").strip()
    cleaned = cleaned.replace(".", "").replace(",", ".")
    try:
        return float(cleaned)
    except ValueError:
        return None


def extract_sku(text: str) -> str:
    """Extract SKU from description text. Looks for 'SKU: XXXX' pattern."""
    if not text:
        return ""
    match = re.search(r'SKU[:\s]+(\d+)', text, re.IGNORECASE)
    return match.group(1) if match else ""


def extract_ad_id(url: str) -> str:
    """Extract ad ID from URL like /s-anzeige/.../3376755886-223-309"""
    match = re.search(r'/(\d+)-\d+-\d+$', url)
    return match.group(1) if match else ""


# ── Synchronous scraper (runs in thread pool) ──────────────────────

def _scrape_sync(user_id: str) -> Dict[str, Any]:
    """
    Synchronous scrape using cloudscraper.
    Called from async context via executor.
    """
    scraper = cloudscraper.create_scraper(
        browser={
            'browser': 'chrome',
            'platform': 'darwin',
            'desktop': True,
        }
    )

    # Step 1: Fetch all ads from seller profile
    all_ads = []
    page = 1

    while True:
        url = f"{BASE_URL}/s-bestandsliste.html?userId={user_id}&pageNum={page}"
        logger.info(f"Fetching seller page {page}: {url}")

        try:
            resp = scraper.get(url, timeout=30)
            logger.info(f"  -> HTTP {resp.status_code} ({len(resp.text)} bytes)")

            if resp.status_code == 403:
                if page == 1:
                    return {"error": "Blocked by Cloudflare (403). Try again later.", "listings": []}
                break
            if resp.status_code != 200:
                if page == 1:
                    return {"error": f"HTTP {resp.status_code} from Kleinanzeigen", "listings": []}
                break

            html = resp.text

            # Check for Cloudflare challenge
            if "Just a moment" in html or "challenge-platform" in html:
                return {"error": "Cloudflare challenge detected — cloudscraper couldn't solve it. Try again later.", "listings": []}

            parser = AdListParser()
            parser.feed(html)

            if not parser.ads:
                if page == 1:
                    logger.warning("No ads found on page 1 — HTML might have unexpected structure")
                    # Return a debug snippet to help troubleshoot
                    snippet = html[:500] if len(html) > 0 else "(empty)"
                    return {"error": f"No ads found on profile page. Page length: {len(html)} chars", "listings": [], "debug_snippet": snippet}
                break

            all_ads.extend(parser.ads)
            logger.info(f"  Page {page}: {len(parser.ads)} ads (total: {len(all_ads)})")

            if f"pageNum={page + 1}" not in html:
                break

            page += 1
            time.sleep(2)

        except Exception as e:
            logger.error(f"Error on page {page}: {e}")
            if page == 1:
                return {"error": f"Failed to fetch profile: {str(e)}", "listings": []}
            break

    logger.info(f"Found {len(all_ads)} ads total")

    # Build listings from profile data (no individual ad fetches — too slow)
    listings = []
    for ad in all_ads:
        ad_url = ad.get("url", "")
        full_url = BASE_URL + ad_url if ad_url.startswith("/") else ad_url

        listing = {
            "title": ad.get("title", ""),
            "price": parse_price(ad.get("price", "")),
            "url": full_url,
            "ad_id": extract_ad_id(ad_url),
            "sku": "",  # SKU matching done in sync logic via title/existing data
            "platform": "kleinanzeigen",
        }
        listings.append(listing)

    return {
        "user_id": user_id,
        "scraped_at": datetime.utcnow().isoformat(),
        "count": len(listings),
        "listings": listings,
    }


# ── Async wrapper ───────────────────────────────────────────────────

async def scrape_kleinanzeigen() -> Dict[str, Any]:
    """
    Async entry point: runs the synchronous cloudscraper in a thread pool.
    """
    user_id = settings.KLEINANZEIGEN_USER_ID
    if not user_id:
        return {"error": "KLEINANZEIGEN_USER_ID not configured", "listings": []}

    logger.info(f"Starting Kleinanzeigen scrape for user {user_id}")

    loop = asyncio.get_event_loop()
    result = await loop.run_in_executor(_executor, _scrape_sync, user_id)

    logger.info(f"Scrape finished: {result.get('count', 0)} listings")
    return result
