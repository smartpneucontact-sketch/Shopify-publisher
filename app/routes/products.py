"""Products endpoints — proxies Shopify Admin API."""

from fastapi import APIRouter, HTTPException, Query
from app.services.shopify import shopify_client

router = APIRouter()


@router.get("/")
async def list_products(
    limit: int = Query(50, ge=1, le=250),
    collection_id: int | None = Query(None),
    product_type: str | None = Query(None),
):
    """Get all products with optional filters."""
    try:
        kwargs = {}
        if collection_id:
            kwargs["collection_id"] = collection_id
        if product_type:
            kwargs["product_type"] = product_type
        data = await shopify_client.get_products(limit=limit, **kwargs)
        return data
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/count")
async def product_count():
    """Get total product count."""
    try:
        return await shopify_client.get_product_count()
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/{product_id}")
async def get_product(product_id: int):
    """Get a single product by ID."""
    try:
        return await shopify_client.get_product(product_id)
    except Exception as e:
        raise HTTPException(status_code=404, detail=f"Product {product_id} not found")
