"""
Leboncoin scraper — fetches seller's active listings via individual ad pages.

Uses cloudscraper to bypass Cloudflare/DataDome protection.
Each Leboncoin ad page is a Next.js server-rendered page with all data
embedded in a __NEXT_DATA__ JSON blob inside a <script> tag.

Strategy:
1. Try search page with owner_id to discover all ads
2. Parse __NEXT_DATA__ JSON for structured listing data
3. Extract SKU from attributes.custom_ref
"""

import re
import json
import asyncio
import logging
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from typing import List, Dict, Any, Optional

import cloudscraper

from app.config import settings

logger = logging.getLogger(__name__)

# Thread pool for running synchronous cloudscraper in async context
_executor = ThreadPoolExecutor(max_workers=2)


# ── JSON extraction ────────────────────────────────────────────────

def extract_next_data(html: str) -> Optional[dict]:
    """Extract __NEXT_DATA__ JSON from a Leboncoin page."""
    match = re.search(
        r'<script\s+id="__NEXT_DATA__"\s+type="application/json"[^>]*>(.*?)</script>',
        html, re.DOTALL
    )
    if not match:
        return None
    try:
        return json.loads(match.group(1))
    except json.JSONDecodeError:
        logger.error("Failed to parse __NEXT_DATA__ JSON")
        return None


def parse_ad_from_next_data(data: dict) -> Optional[Dict[str, Any]]:
    """Extract listing info from a single ad page's __NEXT_DATA__."""
    try:
        props = data.get("props", {}).get("pageProps", {})
        ad = props.get("ad", {})
        if not ad:
            return None

        # Extract attributes
        attributes = {a["key"]: a.get("value", "") for a in ad.get("attributes", []) if "key" in a}
        sku = attributes.get("custom_ref", "")

        # Price
        price = None
        price_list = ad.get("price", [])
        if isinstance(price_list, list):
            for p in price_list:
                if isinstance(p, (int, float)):
                    price = float(p)
                    break
        elif isinstance(price_list, (int, float)):
            price = float(price_list)

        # URL
        ad_url = ad.get("url", "")
        if ad_url and not ad_url.startswith("http"):
            ad_url = "https://www.leboncoin.fr" + ad_url

        # Ad ID
        ad_id = str(ad.get("list_id", ""))

        return {
            "title": ad.get("subject", ""),
            "price": price,
            "url": ad_url,
            "ad_id": ad_id,
            "sku": sku,
            "platform": "leboncoin",
        }
    except Exception as e:
        logger.error(f"Error parsing ad from __NEXT_DATA__: {e}")
        return None


def parse_search_results(data: dict) -> List[Dict[str, Any]]:
    """Extract listings from a search page's __NEXT_DATA__."""
    listings = []
    try:
        props = data.get("props", {}).get("pageProps", {})

        # Try multiple possible locations for search results
        ads = (
            props.get("searchData", {}).get("ads", []) or
            props.get("ads", []) or
            props.get("initialProps", {}).get("searchData", {}).get("ads", []) or
            []
        )

        for ad in ads:
            if not isinstance(ad, dict):
                continue

            # Extract attributes
            attributes = {}
            for a in ad.get("attributes", []):
                if isinstance(a, dict) and "key" in a:
                    attributes[a["key"]] = a.get("value", "")

            sku = attributes.get("custom_ref", "")

            # Price
            price = None
            price_list = ad.get("price", [])
            if isinstance(price_list, list):
                for p in price_list:
                    if isinstance(p, (int, float)):
                        price = float(p)
                        break
            elif isinstance(price_list, (int, float)):
                price = float(price_list)

            # URL
            ad_url = ad.get("url", "")
            if ad_url and not ad_url.startswith("http"):
                ad_url = "https://www.leboncoin.fr" + ad_url

            ad_id = str(ad.get("list_id", ""))

            listings.append({
                "title": ad.get("subject", ""),
                "price": price,
                "url": ad_url,
                "ad_id": ad_id,
                "sku": sku,
                "platform": "leboncoin",
            })
    except Exception as e:
        logger.error(f"Error parsing search results: {e}")

    return listings


# ── Synchronous scraper (runs in thread pool) ──────────────────────

def _create_scraper():
    """Create a cloudscraper instance with browser-like settings."""
    return cloudscraper.create_scraper(
        browser={
            'browser': 'chrome',
            'platform': 'darwin',
            'desktop': True,
        }
    )


def _try_search_page(scraper, user_id: str) -> Optional[List[Dict[str, Any]]]:
    """Try to get all ads via the search page filtered by owner."""
    search_urls = [
        f"https://www.leboncoin.fr/recherche?owner_id={user_id}",
        f"https://www.leboncoin.fr/recherche?owner_type=pro&owner_id={user_id}",
    ]

    for url in search_urls:
        logger.info(f"Trying search page: {url}")
        try:
            resp = scraper.get(url, timeout=30)
            logger.info(f"  -> HTTP {resp.status_code} ({len(resp.text)} bytes)")

            if resp.status_code != 200:
                continue

            # Check for captcha/challenge
            if "captcha" in resp.text.lower() or "challenge" in resp.text.lower():
                logger.warning("Captcha detected on search page")
                continue

            data = extract_next_data(resp.text)
            if not data:
                continue

            listings = parse_search_results(data)
            if listings:
                logger.info(f"Found {len(listings)} listings via search page")
                return listings

        except Exception as e:
            logger.warning(f"Search page failed: {e}")
            continue

    return None


def _scrape_individual_ads(scraper, ad_urls: List[str]) -> List[Dict[str, Any]]:
    """Scrape individual ad pages to extract listing data."""
    listings = []

    for i, url in enumerate(ad_urls):
        logger.info(f"Fetching ad {i+1}/{len(ad_urls)}: {url}")
        try:
            resp = scraper.get(url, timeout=30)
            logger.info(f"  -> HTTP {resp.status_code}")

            if resp.status_code != 200:
                logger.warning(f"  Skipping — HTTP {resp.status_code}")
                continue

            if "captcha" in resp.text.lower():
                logger.warning("  Captcha detected — stopping")
                break

            data = extract_next_data(resp.text)
            if not data:
                logger.warning("  No __NEXT_DATA__ found")
                continue

            listing = parse_ad_from_next_data(data)
            if listing:
                listings.append(listing)
                logger.info(f"  Parsed: {listing['title'][:50]} | SKU: {listing['sku']}")

            # Be polite — don't hammer the server
            if i < len(ad_urls) - 1:
                time.sleep(2)

        except Exception as e:
            logger.error(f"  Error fetching {url}: {e}")
            continue

    return listings


def _discover_seller_ads(scraper, start_url: str) -> List[str]:
    """
    From a single ad page, try to discover other ads by the same seller.
    Looks for 'other ads from this seller' section in __NEXT_DATA__.
    """
    logger.info(f"Discovering seller ads from: {start_url}")
    try:
        resp = scraper.get(start_url, timeout=30)
        if resp.status_code != 200:
            return []

        data = extract_next_data(resp.text)
        if not data:
            return []

        # Look for other seller ads in props
        props = data.get("props", {}).get("pageProps", {})

        # Check various possible locations
        other_ads = (
            props.get("sellerAds", []) or
            props.get("otherSellerAds", []) or
            props.get("sameSellerAds", []) or
            []
        )

        urls = []
        for ad in other_ads:
            if isinstance(ad, dict):
                url = ad.get("url", "")
                if url:
                    if not url.startswith("http"):
                        url = "https://www.leboncoin.fr" + url
                    urls.append(url)

        # Also look in the HTML for ad links from the same seller
        # Pattern: /ad/equipement_auto/XXXXXXX
        ad_links = re.findall(r'href="(/ad/[^"]+)"', resp.text)
        for link in ad_links:
            full_url = "https://www.leboncoin.fr" + link
            if full_url not in urls and full_url != start_url:
                urls.append(full_url)

        logger.info(f"Discovered {len(urls)} other ads from seller")
        return urls

    except Exception as e:
        logger.error(f"Discovery failed: {e}")
        return []


def _scrape_sync(user_id: str, ad_urls: List[str]) -> Dict[str, Any]:
    """
    Synchronous scrape using cloudscraper.

    Strategy:
    1. Try search page with owner_id
    2. If that fails, scrape provided ad URLs + discover more from each page
    """
    scraper = _create_scraper()

    # Strategy 1: Try search page
    if user_id:
        listings = _try_search_page(scraper, user_id)
        if listings:
            return {
                "method": "search_page",
                "scraped_at": datetime.utcnow().isoformat(),
                "count": len(listings),
                "listings": listings,
            }

    # Strategy 2: Scrape individual ad URLs
    if not ad_urls:
        return {"error": "No ad URLs to scrape and search page didn't work", "listings": []}

    # Try to discover more ads from the first URL
    all_urls = list(ad_urls)
    discovered = _discover_seller_ads(scraper, ad_urls[0])
    for url in discovered:
        if url not in all_urls:
            all_urls.append(url)

    time.sleep(2)

    # Scrape all known ad pages
    listings = _scrape_individual_ads(scraper, all_urls)

    return {
        "method": "individual_pages",
        "scraped_at": datetime.utcnow().isoformat(),
        "count": len(listings),
        "provided_urls": len(ad_urls),
        "discovered_urls": len(discovered),
        "total_urls": len(all_urls),
        "listings": listings,
    }


# ── Async wrapper ───────────────────────────────────────────────────

async def scrape_leboncoin(ad_urls: Optional[List[str]] = None) -> Dict[str, Any]:
    """
    Async entry point: runs the synchronous cloudscraper in a thread pool.

    Args:
        ad_urls: Optional list of known ad URLs to scrape
    """
    user_id = settings.LEBONCOIN_USER_ID
    urls = ad_urls or []

    # Also load stored URLs from settings
    if settings.LEBONCOIN_AD_URLS:
        stored = [u.strip() for u in settings.LEBONCOIN_AD_URLS.split(",") if u.strip()]
        for u in stored:
            if u not in urls:
                urls.append(u)

    if not user_id and not urls:
        return {"error": "LEBONCOIN_USER_ID not configured and no ad URLs provided", "listings": []}

    logger.info(f"Starting Leboncoin scrape (user_id={user_id}, {len(urls)} URLs)")

    loop = asyncio.get_event_loop()
    result = await loop.run_in_executor(_executor, _scrape_sync, user_id, urls)

    logger.info(f"Scrape finished: {result.get('count', 0)} listings")
    return result
