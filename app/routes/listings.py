"""
Listings API routes — track Kleinanzeigen / Leboncoin publications.
"""

from typing import Optional

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from app.services.sales_db import sales_db

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
