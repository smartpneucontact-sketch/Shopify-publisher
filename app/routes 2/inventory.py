"""Inventory endpoints — proxies Shopify Admin API."""

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel
from app.services.shopify import shopify_client

router = APIRouter()


class InventoryAdjustment(BaseModel):
    inventory_item_id: int
    location_id: int
    adjustment: int  # positive = add stock, negative = remove stock


@router.get("/locations")
async def list_locations():
    """Get all store locations."""
    try:
        return await shopify_client.get_locations()
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/levels")
async def get_inventory_levels(
    location_ids: str = Query(..., description="Comma-separated location IDs"),
):
    """Get inventory levels for given locations."""
    try:
        return await shopify_client.get_inventory_levels(location_ids=location_ids)
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/adjust")
async def adjust_inventory(body: InventoryAdjustment):
    """Adjust inventory for an item at a location."""
    try:
        return await shopify_client.adjust_inventory(
            inventory_item_id=body.inventory_item_id,
            location_id=body.location_id,
            adjustment=body.adjustment,
        )
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))
