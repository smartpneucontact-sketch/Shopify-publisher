"""
Listings API routes — track Kleinanzeigen / Leboncoin publications.
Includes scraper endpoint to sync from Kleinanzeigen seller profile.
"""

import logging
from datetime import datetime
from typing import Optional

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from app.services.sales_db import sales_db
from app.services.kleinanzeigen_scraper import scrape_kleinanzeigen

logger = logging.getLogger(__name__)

router = APIRouter()


# ── Models ──────────────────────────────────────────────────────────

class ListingRequest(BaseModel):
    sku: str = Field(..., description="Product SKU")
    platform: str = Field(..., description="Platform: kleinanzeigen, leboncoin")
    title: Optional[str] = Field(default="", description="Listing title")
    price: Optional[float] = Field(default=None, description="Listing price")
    currency: str = Field(default="EUR")
    listing_url: Optional[str] = Field(default="", description="URL of the listing")
    status: str = Field(default="active", description="active, sold, expired")
    listed_at: Optional[str] = Field(default=None, description="ISO date when listed")


class StatusUpdate(BaseModel):
    status: str = Field(..., description="New status: active, sold, expired")


class UrlUpdate(BaseModel):
    listing_url: str = Field(..., description="Listing URL")


# ── CRUD ────────────────────────────────────────────────────────────

@router.post("/")
async def create_listing(listing: ListingRequest):
    """Record a new listing (called from Kleinanzeigen/Leboncoin tool)."""
    try:
        result = await sales_db.add_listing(listing.model_dump())
        return result
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.get("/")
async def list_listings(limit: int = 500, offset: int = 0, platform: str = None, status: str = None):
    """List all listings with optional filters."""
    return await sales_db.list_listings(limit=limit, offset=offset, platform=platform, status=status)


@router.patch("/{listing_id}/status")
async def update_status(listing_id: int, body: StatusUpdate):
    """Update listing status."""
    if body.status not in ("active", "sold", "expired"):
        raise HTTPException(status_code=400, detail="Status must be: active, sold, or expired")
    updated = await sales_db.update_listing_status(listing_id, body.status)
    if not updated:
        raise HTTPException(status_code=404, detail="Listing not found")
    return {"ok": True}


@router.patch("/{listing_id}/url")
async def update_url(listing_id: int, body: UrlUpdate):
    """Update listing URL."""
    updated = await sales_db.update_listing_url(listing_id, body.listing_url)
    if not updated:
        raise HTTPException(status_code=404, detail="Listing not found")
    return {"ok": True}


@router.delete("/{listing_id}")
async def delete_listing(listing_id: int):
    """Delete a listing."""
    deleted = await sales_db.delete_listing(listing_id)
    if not deleted:
        raise HTTPException(status_code=404, detail="Listing not found")
    return {"ok": True}


# ── Kleinanzeigen Scraper ───────────────────────────────────────────

@router.get("/scrape-test")
async def scrape_test():
    """Debug: test scrape without syncing to DB. Returns raw result."""
    try:
        result = await scrape_kleinanzeigen()
        return {
            "ok": True,
            "error": result.get("error"),
            "count": result.get("count", 0),
            "first_3": result.get("listings", [])[:3],
            "debug_snippet": result.get("debug_snippet"),
        }
    except Exception as e:
        import traceback
        return {
            "ok": False,
            "error": str(e),
            "traceback": traceback.format_exc(),
        }


@router.post("/scrape-kleinanzeigen")
async def scrape_and_sync():
    """
    Scrape Kleinanzeigen seller profile, sync listings to DB.
    - New listings (by URL) are added as 'active'
    - Existing listings that are no longer on KA are marked 'expired'
    - Existing listings still on KA are updated (price, title)
    """
    try:
        result = await scrape_kleinanzeigen()
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        logger.exception("Scraper crashed")
        raise HTTPException(status_code=500, detail=f"Scraper error: {str(e)}")

    if result.get("error"):
        raise HTTPException(status_code=400, detail=result["error"])

    scraped = result.get("listings", [])

    # Get existing KA listings from DB
    existing = await sales_db.list_listings(limit=5000, platform="kleinanzeigen")
    existing_by_url = {l["listing_url"]: l for l in existing if l.get("listing_url")}

    scraped_urls = set()
    added = 0
    updated = 0
    expired = 0

    for item in scraped:
        url = item.get("url", "")
        if not url:
            continue
        scraped_urls.add(url)

        if url in existing_by_url:
            updated += 1
        else:
            try:
                await sales_db.add_listing({
                    "sku": item.get("sku", ""),
                    "platform": "kleinanzeigen",
                    "title": item.get("title", ""),
                    "price": item.get("price"),
                    "currency": "EUR",
                    "listing_url": url,
                    "status": "active",
                    "listed_at": datetime.utcnow().isoformat(),
                })
                added += 1
            except Exception as e:
                logger.warning(f"Failed to add listing {url}: {e}")

    # Mark listings no longer on KA as expired
    for url, listing in existing_by_url.items():
        if url not in scraped_urls and listing.get("status") == "active":
            await sales_db.update_listing_status(listing["id"], "expired")
            expired += 1

    return {
        "scraped_at": result.get("scraped_at"),
        "found_on_ka": len(scraped),
        "added": added,
        "already_tracked": updated,
        "expired": expired,
    }


# ── Leboncoin Import (bookmarklet) ─────────────────────────────────

class LeboncoinListing(BaseModel):
    title: str = ""
    sku: str = ""
    price: Optional[float] = None

class LeboncoinImportRequest(BaseModel):
    listings: list[LeboncoinListing] = Field(..., description="Listings extracted by bookmarklet")


@router.post("/import-leboncoin")
async def import_leboncoin(body: LeboncoinImportRequest):
    """
    Import Leboncoin listings sent by the browser bookmarklet.
    Syncs by SKU: new ones added, missing ones expired.
    """
    scraped = body.listings

    # Get existing LBC listings from DB
    existing = await sales_db.list_listings(limit=5000, platform="leboncoin")
    existing_by_sku = {l["sku"]: l for l in existing if l.get("sku")}

    scraped_skus = set()
    added = 0
    updated = 0
    expired = 0

    for item in scraped:
        sku = item.sku.strip()
        if not sku:
            continue
        scraped_skus.add(sku)

        if sku in existing_by_sku:
            # Update price/title if changed
            ex = existing_by_sku[sku]
            if ex.get("status") == "expired":
                await sales_db.update_listing_status(ex["id"], "active")
            updated += 1
        else:
            try:
                await sales_db.add_listing({
                    "sku": sku,
                    "platform": "leboncoin",
                    "title": item.title,
                    "price": item.price,
                    "currency": "EUR",
                    "listing_url": "",
                    "status": "active",
                    "listed_at": datetime.utcnow().isoformat(),
                })
                added += 1
            except Exception as e:
                logger.warning(f"Failed to add LBC listing SKU {sku}: {e}")

    # Mark listings no longer on LBC as expired
    for sku, listing in existing_by_sku.items():
        if sku not in scraped_skus and listing.get("status") == "active":
            await sales_db.update_listing_status(listing["id"], "expired")
            expired += 1

    return {
        "imported_at": datetime.utcnow().isoformat(),
        "found_on_lbc": len(scraped),
        "added": added,
        "already_tracked": updated,
        "expired": expired,
    }
