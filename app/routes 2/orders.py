"""Orders endpoints — proxies Shopify Admin API."""

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel
from typing import Optional
from app.services.shopify import shopify_client

router = APIRouter()


class CreateOrderRequest(BaseModel):
    """Simplified order creation payload."""
    line_items: list[dict]
    customer: Optional[dict] = None
    email: Optional[str] = None
    shipping_address: Optional[dict] = None
    financial_status: str = "pending"
    note: Optional[str] = None


@router.get("/")
async def list_orders(
    limit: int = Query(50, ge=1, le=250),
    status: str = Query("any", regex="^(open|closed|cancelled|any)$"),
    created_at_min: str | None = Query(None, description="ISO 8601 date"),
    created_at_max: str | None = Query(None, description="ISO 8601 date"),
):
    """List orders with filters."""
    try:
        kwargs = {}
        if created_at_min:
            kwargs["created_at_min"] = created_at_min
        if created_at_max:
            kwargs["created_at_max"] = created_at_max
        return await shopify_client.get_orders(limit=limit, status=status, **kwargs)
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/count")
async def order_count(status: str = Query("any")):
    """Get order count."""
    try:
        return await shopify_client.get_order_count(status=status)
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/{order_id}")
async def get_order(order_id: int):
    """Get a single order."""
    try:
        return await shopify_client.get_order(order_id)
    except Exception as e:
        raise HTTPException(status_code=404, detail=f"Order {order_id} not found")


@router.post("/")
async def create_order(order: CreateOrderRequest):
    """Create a new draft order."""
    try:
        return await shopify_client.create_order(order.model_dump(exclude_none=True))
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.post("/{order_id}/close")
async def close_order(order_id: int):
    """Close an order."""
    try:
        return await shopify_client.close_order(order_id)
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))
