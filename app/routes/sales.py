"""
FastAPI router for recording and querying tire sales across multiple channels.
Part of the SmartPneu Shopify-publisher app.
"""

from datetime import datetime, timedelta
from typing import Optional, List
from enum import Enum

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel, Field

from app.services.sales_db import sales_db
from app.services.shopify import shopify_client


router = APIRouter(prefix="/sales", tags=["sales"])


class SalesChannel(str, Enum):
    """Supported sales channels."""
    SHOPIFY = "shopify"
    EBAY = "ebay"
    EBAY_KLEINANZEIGEN = "ebay_kleinanzeigen"
    LEBONCOIN = "leboncoin"
    CASH = "cash"


class SaleRequest(BaseModel):
    """Request model for recording a new sale."""
    sku: str = Field(..., description="Product SKU")
    channel: SalesChannel = Field(..., description="Sales channel")
    sale_price: float = Field(..., gt=0, description="Sale price in currency units")
    quantity: Optional[int] = Field(default=1, ge=1, description="Quantity sold")
    order_ref: Optional[str] = Field(default=None, description="Order reference/ID from channel")
    customer_name: Optional[str] = Field(default=None, description="Customer name")
    customer_email: Optional[str] = Field(default=None, description="Customer email")
    notes: Optional[str] = Field(default=None, description="Additional notes")
    sold_at: Optional[datetime] = Field(default=None, description="Sale timestamp (UTC)")
    product_title: Optional[str] = Field(default=None, description="Product title")
    brand: Optional[str] = Field(default=None, description="Brand name")
    model: Optional[str] = Field(default=None, description="Model name")


class SaleResponse(BaseModel):
    """Response model for a sale record."""
    sale_id: Optional[str] = None
    sku: str
    channel: str
    sale_price: float
    quantity: int
    order_ref: Optional[str] = None
    customer_name: Optional[str] = None
    customer_email: Optional[str] = None
    notes: Optional[str] = None
    sold_at: datetime
    product_title: Optional[str] = None
    brand: Optional[str] = None
    model: Optional[str] = None
    created_at: Optional[datetime] = None


class RevenueSummary(BaseModel):
    """Response model for revenue summary."""
    total_revenue: float
    sale_count: int
    by_channel: dict
    by_day: list
    top_skus: list


@router.post("/", response_model=SaleResponse, status_code=201)
async def record_sale(sale: SaleRequest):
    """
    Record a new tire sale across any supported channel.

    If sold_at is not provided, the current UTC time will be used.

    Args:
        sale: Sale data including SKU, channel, price, and optional metadata

    Returns:
        The recorded sale with assigned sale_id and timestamps

    Raises:
        HTTPException: If the sale cannot be recorded
    """
    try:
        sale_data = sale.dict()

        # Use current UTC time if sold_at not provided
        if sale_data.get("sold_at") is None:
            sale_data["sold_at"] = datetime.utcnow()

        # Convert enum to string
        sale_data["channel"] = sale_data["channel"].value

        result = sales_db.record_sale(sale_data)

        if not result:
            raise HTTPException(
                status_code=500,
                detail="Failed to record sale in database"
            )

        return SaleResponse(**result)

    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error recording sale: {str(e)}")


@router.post("/import/shopify", status_code=202)
async def import_shopify_orders():
    """
    Import recent Shopify orders as sales records.

    Fetches recent orders from Shopify and imports them as sales,
    avoiding duplicates by checking order_ref.

    Returns:
        Dictionary with import statistics (imported_count, skipped_count, errors)

    Raises:
        HTTPException: If Shopify connection or import fails
    """
    try:
        # Fetch recent orders from Shopify
        orders = shopify_client.get_recent_orders()

        if not orders:
            return {
                "imported_count": 0,
                "skipped_count": 0,
                "errors": [],
                "message": "No recent orders found in Shopify"
            }

        imported_count = 0
        skipped_count = 0
        errors = []

        for order in orders:
            try:
                # Check if order already exists by order_ref
                existing = sales_db.get_sales(
                    channel="shopify",
                    start_date=None,
                    end_date=None,
                    sku=None,
                    limit=1,
                    offset=0
                )

                order_ref = order.get("order_number") or order.get("id")

                # Check if this order_ref already exists
                duplicate = any(
                    sale.get("order_ref") == order_ref
                    for sale in existing
                )

                if duplicate:
                    skipped_count += 1
                    continue

                # Extract sale data from Shopify order
                total_price = float(order.get("total_price", 0))

                sale_data = {
                    "sku": order.get("sku", "UNKNOWN"),
                    "channel": "shopify",
                    "sale_price": total_price,
                    "quantity": order.get("quantity", 1),
                    "order_ref": order_ref,
                    "customer_name": order.get("customer_name"),
                    "customer_email": order.get("customer_email"),
                    "notes": f"Imported from Shopify - {order.get('fulfillment_status', 'pending')}",
                    "sold_at": order.get("created_at", datetime.utcnow()),
                    "product_title": order.get("title"),
                    "brand": order.get("vendor"),
                }

                result = sales_db.record_sale(sale_data)
                if result:
                    imported_count += 1

            except Exception as e:
                errors.append({
                    "order_id": order.get("id"),
                    "error": str(e)
                })

        return {
            "imported_count": imported_count,
            "skipped_count": skipped_count,
            "errors": errors,
            "message": f"Imported {imported_count} orders, skipped {skipped_count} duplicates"
        }

    except Exception as e:
        raise HTTPException(
            status_code=500,
            detail=f"Error importing Shopify orders: {str(e)}"
        )


@router.get("/", response_model=List[SaleResponse])
async def list_sales(
    channel: Optional[SalesChannel] = Query(None, description="Filter by sales channel"),
    start_date: Optional[datetime] = Query(None, description="Filter by start date (ISO format)"),
    end_date: Optional[datetime] = Query(None, description="Filter by end date (ISO format)"),
    sku: Optional[str] = Query(None, description="Filter by SKU"),
    limit: int = Query(100, ge=1, le=1000, description="Maximum results to return"),
    offset: int = Query(0, ge=0, description="Result offset for pagination"),
):
    """
    List sales with optional filters.

    Query Parameters:
        channel: Filter by sales channel (shopify, ebay, ebay_kleinanzeigen, leboncoin, cash)
        start_date: Filter sales from this date onwards
        end_date: Filter sales up to this date
        sku: Filter by specific product SKU
        limit: Maximum number of results (default 100, max 1000)
        offset: Pagination offset (default 0)

    Returns:
        List of matching sales records

    Raises:
        HTTPException: If query fails
    """
    try:
        channel_value = channel.value if channel else None

        sales = sales_db.get_sales(
            channel=channel_value,
            start_date=start_date,
            end_date=end_date,
            sku=sku,
            limit=limit,
            offset=offset
        )

        return [SaleResponse(**sale) for sale in sales]

    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error fetching sales: {str(e)}")


@router.get("/summary", response_model=RevenueSummary)
async def get_summary(
    start_date: Optional[datetime] = Query(None, description="Filter by start date (ISO format)"),
    end_date: Optional[datetime] = Query(None, description="Filter by end date (ISO format)"),
):
    """
    Get revenue summary with aggregated statistics.

    Query Parameters:
        start_date: Summary start date
        end_date: Summary end date

    Returns:
        Revenue summary including total, by channel, by day, and top SKUs

    Raises:
        HTTPException: If query fails
    """
    try:
        summary = sales_db.get_revenue_summary(start_date, end_date)

        if not summary:
            return RevenueSummary(
                total_revenue=0.0,
                sale_count=0,
                by_channel={},
                by_day=[],
                top_skus=[]
            )

        return RevenueSummary(**summary)

    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error fetching summary: {str(e)}")


@router.get("/daily")
async def get_daily_revenue(
    days: int = Query(30, ge=1, le=365, description="Number of days to include (max 365)"),
):
    """
    Get daily revenue chart data for the last N days.

    Query Parameters:
        days: Number of days to include (default 30, max 365)

    Returns:
        List of daily revenue records with date and amount

    Raises:
        HTTPException: If query fails
    """
    try:
        daily_data = sales_db.get_daily_revenue(days)

        if not daily_data:
            return {
                "data": [],
                "days": days,
                "message": "No sales data available for the requested period"
            }

        return {
            "data": daily_data,
            "days": days
        }

    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error fetching daily revenue: {str(e)}")


@router.get("/channels")
async def get_channel_breakdown(
    start_date: Optional[datetime] = Query(None, description="Filter by start date (ISO format)"),
    end_date: Optional[datetime] = Query(None, description="Filter by end date (ISO format)"),
):
    """
    Get sales breakdown by channel.

    Query Parameters:
        start_date: Breakdown start date
        end_date: Breakdown end date

    Returns:
        List of channel statistics (channel, total_revenue, sale_count, avg_price)

    Raises:
        HTTPException: If query fails
    """
    try:
        breakdown = sales_db.get_channel_breakdown(start_date, end_date)

        if not breakdown:
            return {
                "channels": [],
                "message": "No sales data available for the requested period"
            }

        return {
            "channels": breakdown
        }

    except Exception as e:
        raise HTTPException(
            status_code=500,
            detail=f"Error fetching channel breakdown: {str(e)}"
        )


@router.put("/{sale_id}", response_model=SaleResponse)
async def update_sale(
    sale_id: str,
    sale: SaleRequest,
):
    """
    Update an existing sale record.

    Path Parameters:
        sale_id: ID of the sale to update

    Body:
        Updated sale data (same fields as POST)

    Returns:
        Updated sale record

    Raises:
        HTTPException: If sale not found or update fails
    """
    try:
        sale_data = sale.dict()

        # Convert enum to string
        sale_data["channel"] = sale_data["channel"].value

        result = sales_db.update_sale(sale_id, sale_data)

        if not result:
            raise HTTPException(
                status_code=404,
                detail=f"Sale with ID {sale_id} not found"
            )

        return SaleResponse(**result)

    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error updating sale: {str(e)}")


@router.delete("/{sale_id}", status_code=204)
async def delete_sale(sale_id: str):
    """
    Delete a sale record.

    Path Parameters:
        sale_id: ID of the sale to delete

    Returns:
        No content on success

    Raises:
        HTTPException: If sale not found or deletion fails
    """
    try:
        success = sales_db.delete_sale(sale_id)

        if not success:
            raise HTTPException(
                status_code=404,
                detail=f"Sale with ID {sale_id} not found"
            )

        return None

    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error deleting sale: {str(e)}")
