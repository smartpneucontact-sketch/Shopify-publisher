"""
Kleinanzeigen scraper — fetches seller's active listings and extracts SKUs.

Scrapes the public seller profile page to get all active ads,
then matches SKUs from the description text (looks for "SKU: XXXX" pattern).
"""

import re
import asyncio
import logging
from datetime import datetime
from typing import List, Dict, Any, Optional
from html.parser import HTMLParser

import httpx

from app.config import settings

logger = logging.getLogger(__name__)

BASE_URL = "https://www.kleinanzeigen.de"
HEADERS = {
    "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "de-DE,de;q=0.9,en;q=0.8",
    "Accept-Encoding": "gzip, deflate, br",
    "Connection": "keep-alive",
    "Cache-Control": "no-cache",
}


class ListingItem:
    """Represents a single Kleinanzeigen listing."""
    def __init__(self):
        self.title: str = ""
        self.price: Optional[float] = None
        self.url: str = ""
        self.ad_id: str = ""
        self.sku: str = ""
        self.listed_date: str = ""


# ── Simple HTML parser (no BeautifulSoup dependency) ────────────────

class AdListParser(HTMLParser):
    """Parse the seller profile page to extract ad items."""

    def __init__(self):
        super().__init__()
        self.ads: List[Dict[str, str]] = []
        self._current_ad: Optional[Dict[str, str]] = None
        self._in_article = False
        self._in_title = False
        self._in_price = False
        self._in_date = False
        self._capture_text = ""
        self._depth = 0

    def handle_starttag(self, tag, attrs):
        attr_dict = dict(attrs)
        classes = attr_dict.get("class", "")

        # Detect article.aditem
        if tag == "article" and "aditem" in classes:
            self._in_article = True
            self._current_ad = {"title": "", "price": "", "url": "", "date": ""}
            return

        if not self._in_article:
            return

        # Title link
        if tag == "a" and "ellipsis" in classes:
            self._in_title = True
            self._capture_text = ""
            href = attr_dict.get("href", "")
            if href and self._current_ad is not None:
                self._current_ad["url"] = href

        # Price
        if "aditem-main--middle--price-shipping--price" in classes:
            self._in_price = True
            self._capture_text = ""

        # Date
        if "aditem-main--top--right" in classes:
            self._in_date = True
            self._capture_text = ""

    def handle_endtag(self, tag):
        if tag == "article" and self._in_article:
            self._in_article = False
            if self._current_ad and self._current_ad.get("url"):
                self.ads.append(self._current_ad)
            self._current_ad = None
            return

        if self._in_title and tag == "a":
            self._in_title = False
            if self._current_ad is not None:
                self._current_ad["title"] = self._capture_text.strip()

        if self._in_price and tag in ("p", "div", "span"):
            self._in_price = False
            if self._current_ad is not None:
                self._current_ad["price"] = self._capture_text.strip()

        if self._in_date and tag in ("div", "span"):
            self._in_date = False
            if self._current_ad is not None:
                self._current_ad["date"] = self._capture_text.strip()

    def handle_data(self, data):
        if self._in_title or self._in_price or self._in_date:
            self._capture_text += data


class AdDetailParser(HTMLParser):
    """Parse an individual ad page to extract description text."""

    def __init__(self):
        super().__init__()
        self.description: str = ""
        self._in_desc = False
        self._capture = ""

    def handle_starttag(self, tag, attrs):
        attr_dict = dict(attrs)
        attr_id = attr_dict.get("id", "")
        if attr_id == "viewad-description-text":
            self._in_desc = True
            self._capture = ""

    def handle_endtag(self, tag):
        if self._in_desc and tag in ("p", "div", "section"):
            self._in_desc = False
            self.description = self._capture.strip()

    def handle_data(self, data):
        if self._in_desc:
            self._capture += data + "\n"


# ── Scraper functions ───────────────────────────────────────────────

def parse_price(price_str: str) -> Optional[float]:
    """Extract numeric price from string like '120 €' or '1.200 €'."""
    if not price_str:
        return None
    # Remove currency symbol, "VB", whitespace
    cleaned = price_str.replace("€", "").replace("VB", "").replace("\xa0", "").strip()
    # Handle German number format: 1.200,50 → 1200.50
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


async def fetch_seller_listings(user_id: str) -> List[Dict[str, Any]]:
    """
    Fetch all active listings from a seller's profile page.
    Returns list of {title, price, url, ad_id, date}.
    """
    if not user_id:
        raise ValueError("KLEINANZEIGEN_USER_ID not configured")

    all_ads = []
    page = 1

    async with httpx.AsyncClient(headers=HEADERS, follow_redirects=True, timeout=30) as client:
        while True:
            url = f"{BASE_URL}/s-bestandsliste.html?userId={user_id}&pageNum={page}"
            logger.info(f"Fetching seller page {page}: {url}")

            try:
                resp = await client.get(url)
                if resp.status_code == 403:
                    logger.warning("Got 403 — Kleinanzeigen may be blocking. Try again later.")
                    break
                if resp.status_code != 200:
                    logger.warning(f"HTTP {resp.status_code} for page {page}")
                    break

                html = resp.text
                parser = AdListParser()
                parser.feed(html)

                if not parser.ads:
                    break  # No more ads

                all_ads.extend(parser.ads)
                logger.info(f"Page {page}: found {len(parser.ads)} ads")

                # Check if there's a next page (look for pagination link)
                if f"pageNum={page + 1}" not in html:
                    break

                page += 1
                await asyncio.sleep(1.5)  # Be polite

            except httpx.HTTPError as e:
                logger.error(f"HTTP error fetching page {page}: {e}")
                break

    return all_ads


async def fetch_ad_sku(ad_url: str) -> str:
    """Fetch an individual ad page and extract SKU from description."""
    full_url = BASE_URL + ad_url if ad_url.startswith("/") else ad_url

    async with httpx.AsyncClient(headers=HEADERS, follow_redirects=True, timeout=20) as client:
        try:
            resp = await client.get(full_url)
            if resp.status_code != 200:
                return ""
            parser = AdDetailParser()
            parser.feed(resp.text)
            return extract_sku(parser.description)
        except httpx.HTTPError:
            return ""


def extract_ad_id(url: str) -> str:
    """Extract ad ID from URL like /s-anzeige/.../3376755886-223-309"""
    match = re.search(r'/(\d+)-\d+-\d+$', url)
    return match.group(1) if match else ""


async def scrape_kleinanzeigen() -> Dict[str, Any]:
    """
    Full scrape: get all seller listings, extract SKUs, return structured data.
    """
    user_id = settings.KLEINANZEIGEN_USER_ID
    if not user_id:
        return {"error": "KLEINANZEIGEN_USER_ID not configured", "listings": []}

    # Step 1: Get all ads from profile
    raw_ads = await fetch_seller_listings(user_id)
    logger.info(f"Found {len(raw_ads)} total ads")

    # Step 2: For each ad, try to extract SKU from detail page
    listings = []
    for i, ad in enumerate(raw_ads):
        sku = ""
        ad_id = extract_ad_id(ad.get("url", ""))

        # Fetch detail page for SKU (with rate limiting)
        if ad.get("url"):
            sku = await fetch_ad_sku(ad["url"])
            if i < len(raw_ads) - 1:
                await asyncio.sleep(1)  # Rate limit

        listing = {
            "title": ad.get("title", ""),
            "price": parse_price(ad.get("price", "")),
            "url": BASE_URL + ad["url"] if ad.get("url", "").startswith("/") else ad.get("url", ""),
            "ad_id": ad_id,
            "sku": sku,
            "date": ad.get("date", ""),
            "platform": "kleinanzeigen",
        }
        listings.append(listing)
        logger.info(f"  [{i+1}/{len(raw_ads)}] SKU={sku or '?'} — {listing['title'][:50]}")

    return {
        "user_id": user_id,
        "scraped_at": datetime.utcnow().isoformat(),
        "count": len(listings),
        "listings": listings,
    }
