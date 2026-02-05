"""Customers endpoints — proxies Shopify Admin API."""

from fastapi import APIRouter, HTTPException, Query
from app.services.shopify import shopify_client

router = APIRouter()


@router.get("/")
async def list_customers(limit: int = Query(50, ge=1, le=250)):
    """List all customers."""
    try:
        return await shopify_client.get_customers(limit=limit)
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/count")
async def customer_count():
    """Get customer count."""
    try:
        return await shopify_client.get_customer_count()
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/search")
async def search_customers(q: str = Query(..., min_length=1)):
    """Search customers by name, email, etc."""
    try:
        return await shopify_client.search_customers(query=q)
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/{customer_id}")
async def get_customer(customer_id: int):
    """Get a single customer."""
    try:
        return await shopify_client.get_customer(customer_id)
    except Exception as e:
        raise HTTPException(status_code=404, detail=f"Customer {customer_id} not found")
