"""
Leboncoin scraper — fetches seller's active listings using Playwright.

DataDome blocks cloudscraper/httpx, so we use a real headless Chromium
browser via Playwright to render pages and extract __NEXT_DATA__ JSON.

Strategy:
1. Try search page with owner_id to discover all ads at once
2. Fall back to scraping individual ad URLs provided via config
3. Extract SKU from attributes.custom_ref in __NEXT_DATA__
"""

import re
import json
import asyncio
import logging
from datetime import datetime
from typing import List, Dict, Any, Optional

from app.config import settings

logger = logging.getLogger(__name__)


# ── JSON extraction & parsing ──────────────────────────────────────

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

        attributes = {a["key"]: a.get("value", "") for a in ad.get("attributes", []) if "key" in a}
        sku = attributes.get("custom_ref", "")

        price = None
        price_list = ad.get("price", [])
        if isinstance(price_list, list):
            for p in price_list:
                if isinstance(p, (int, float)):
                    price = float(p)
                    break
        elif isinstance(price_list, (int, float)):
            price = float(price_list)

        ad_url = ad.get("url", "")
        if ad_url and not ad_url.startswith("http"):
            ad_url = "https://www.leboncoin.fr" + ad_url

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
    """Extract listings from a search/profile page's __NEXT_DATA__."""
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

            attributes = {}
            for a in ad.get("attributes", []):
                if isinstance(a, dict) and "key" in a:
                    attributes[a["key"]] = a.get("value", "")

            sku = attributes.get("custom_ref", "")

            price = None
            price_list = ad.get("price", [])
            if isinstance(price_list, list):
                for p in price_list:
                    if isinstance(p, (int, float)):
                        price = float(p)
                        break
            elif isinstance(price_list, (int, float)):
                price = float(price_list)

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


# ── Playwright-based scraper ───────────────────────────────────────

async def _fetch_page(browser, url: str, wait_ms: int = 5000) -> Optional[str]:
    """Fetch a page using Playwright, wait for content to load."""
    page = await browser.new_page()
    try:
        logger.info(f"Navigating to: {url}")
        response = await page.goto(url, wait_until="domcontentloaded", timeout=30000)

        if response and response.status != 200:
            logger.warning(f"  HTTP {response.status}")
            # Still try — some pages return 403 initially then load via JS
            if response.status == 403:
                # Wait for potential DataDome challenge resolution
                await page.wait_for_timeout(wait_ms)

        # Wait for __NEXT_DATA__ to appear
        try:
            await page.wait_for_selector('script#__NEXT_DATA__', timeout=15000)
        except Exception:
            logger.warning("  __NEXT_DATA__ script tag not found within timeout")

        html = await page.content()
        logger.info(f"  Got {len(html)} bytes")
        return html

    except Exception as e:
        logger.error(f"  Playwright error: {e}")
        return None
    finally:
        await page.close()


async def _scrape_with_playwright(user_id: str, ad_urls: List[str]) -> Dict[str, Any]:
    """Main scraping logic using Playwright headless browser."""
    try:
        from playwright.async_api import async_playwright
    except ImportError:
        return {"error": "Playwright not installed. Run: pip install playwright && playwright install chromium", "listings": []}

    async with async_playwright() as p:
        browser = await p.chromium.launch(
            headless=True,
            args=[
                '--no-sandbox',
                '--disable-blink-features=AutomationControlled',
                '--disable-dev-shm-usage',
            ]
        )

        context = await browser.new_context(
            user_agent='Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36',
            viewport={'width': 1920, 'height': 1080},
            locale='fr-FR',
        )

        try:
            # Strategy 1: Try search page with owner_id
            if user_id:
                search_url = f"https://www.leboncoin.fr/recherche?owner_id={user_id}"
                html = await _fetch_page(context, search_url, wait_ms=8000)
                if html:
                    data = extract_next_data(html)
                    if data:
                        listings = parse_search_results(data)
                        if listings:
                            logger.info(f"Found {len(listings)} listings via search page")
                            return {
                                "method": "search_page",
                                "scraped_at": datetime.utcnow().isoformat(),
                                "count": len(listings),
                                "listings": listings,
                            }

            # Strategy 2: Scrape individual ad pages
            if not ad_urls:
                return {"error": "Search page didn't work and no ad URLs provided", "listings": []}

            # First pass: scrape known URLs and discover more from page links
            all_urls = list(ad_urls)
            listings = []

            for i, url in enumerate(all_urls):
                logger.info(f"Scraping ad {i+1}/{len(all_urls)}: {url}")
                html = await _fetch_page(context, url)
                if not html:
                    continue

                # Check for captcha
                if "captcha" in html.lower() and "__NEXT_DATA__" not in html:
                    logger.warning("  Captcha detected — stopping")
                    break

                data = extract_next_data(html)
                if not data:
                    logger.warning("  No __NEXT_DATA__ found")
                    continue

                listing = parse_ad_from_next_data(data)
                if listing:
                    listings.append(listing)
                    logger.info(f"  OK: {listing['title'][:50]} | SKU: {listing['sku']}")

                # On first page, try to discover more ads from same seller
                if i == 0:
                    # Look for other ad links in the HTML
                    ad_links = re.findall(r'href="(/ad/[^"]+)"', html)
                    discovered = 0
                    for link in ad_links:
                        full_url = "https://www.leboncoin.fr" + link
                        if full_url not in all_urls:
                            all_urls.append(full_url)
                            discovered += 1
                    if discovered:
                        logger.info(f"  Discovered {discovered} additional ad URLs")

                # Rate limit
                if i < len(all_urls) - 1:
                    await asyncio.sleep(3)

            return {
                "method": "individual_pages",
                "scraped_at": datetime.utcnow().isoformat(),
                "count": len(listings),
                "provided_urls": len(ad_urls),
                "total_urls_scraped": len(all_urls),
                "listings": listings,
            }

        finally:
            await context.close()
            await browser.close()


# ── Public async entry point ───────────────────────────────────────

async def scrape_leboncoin(ad_urls: Optional[List[str]] = None) -> Dict[str, Any]:
    """
    Scrape Leboncoin listings using Playwright headless browser.

    Args:
        ad_urls: Optional list of known ad URLs to scrape
    """
    user_id = settings.LEBONCOIN_USER_ID
    urls = list(ad_urls or [])

    # Also load stored URLs from settings
    if settings.LEBONCOIN_AD_URLS:
        stored = [u.strip() for u in settings.LEBONCOIN_AD_URLS.split(",") if u.strip()]
        for u in stored:
            if u not in urls:
                urls.append(u)

    if not user_id and not urls:
        return {"error": "LEBONCOIN_USER_ID not configured and no ad URLs provided", "listings": []}

    logger.info(f"Starting Leboncoin scrape (user_id={user_id}, {len(urls)} URLs)")

    result = await _scrape_with_playwright(user_id, urls)

    logger.info(f"Scrape finished: {result.get('count', 0)} listings")
    return result
