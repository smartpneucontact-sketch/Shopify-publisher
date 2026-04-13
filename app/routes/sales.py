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
from app.services.ebay import ebay_client


router = APIRouter(tags=["sales"])


def _map_sale(row: dict) -> dict:
    """Map a DB row to SaleResponse-compatible dict (id→sale_id, ensure sold_at is str)."""
    d = dict(row)
    if "id" in d:
        d["sale_id"] = str(d.pop("id"))
    # Ensure sold_at is a string Pydantic can parse
    if "sold_at" in d and not isinstance(d["sold_at"], str):
        d["sold_at"] = str(d["sold_at"])
    return d


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

        # Use current UTC time if sold_at not provided, always as ISO string
        if sale_data.get("sold_at") is None:
            sale_data["sold_at"] = datetime.utcnow().isoformat()
        elif isinstance(sale_data["sold_at"], datetime):
            sale_data["sold_at"] = sale_data["sold_at"].isoformat()

        # Convert enum to string
        sale_data["channel"] = sale_data["channel"].value

        result = await sales_db.record_sale(sale_data)

        if not result:
            raise HTTPException(
                status_code=500,
                detail="Failed to record sale in database"
            )

        # Auto-sync to Shopify: set product to active + inventory 0
        sku_val = sale_data.get("sku", "")
        if sku_val and not sku_val.startswith("NOSSKU-"):
            try:
                all_products = await shopify_client.get_all_products_all_statuses()
                for p in all_products:
                    for v in p.get("variants", []):
                        if v.get("sku") == sku_val:
                            # If product is draft, activate it first
                            if p.get("status") != "active":
                                await shopify_client.set_product_status(p["id"], "active")
                            # Set inventory to 0
                            await shopify_client.set_inventory_to_zero(p["id"])
                            # Mark eBay as ENDED
                            try:
                                await shopify_client.set_product_metafield(
                                    p["id"], "ebay", "ebay_status", "ENDED"
                                )
                            except Exception:
                                pass
                            await sales_db.mark_sku_unlisted(sku_val)
                            break
            except Exception:
                pass  # Best effort — don't fail the sale recording

        return SaleResponse(**_map_sale(result))

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
                existing = await sales_db.get_sales(
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

                result = await sales_db.record_sale(sale_data)
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


@router.post("/import/ebay", status_code=202)
async def import_ebay_orders(
    days: int = Query(30, ge=1, le=365, description="Fetch orders from the last N days"),
):
    """
    Import recent eBay orders as sales records.

    Fetches completed orders from eBay via the Sell Fulfillment API
    and imports them as sales, avoiding duplicates by checking order_ref.

    Query Parameters:
        days: Number of days of order history to import (default 30, max 365)

    Returns:
        Dictionary with import statistics (imported_count, skipped_count, errors)
    """
    if not ebay_client.tokens.is_authenticated:
        raise HTTPException(
            status_code=401,
            detail="eBay not authenticated. Complete the OAuth flow at /api/ebay/auth first."
        )

    try:
        orders = await ebay_client.get_all_recent_orders(days=days)

        if not orders:
            return {
                "imported_count": 0,
                "skipped_count": 0,
                "errors": [],
                "message": f"No eBay orders found in the last {days} days"
            }

        imported_count = 0
        skipped_count = 0
        errors = []

        # Pre-fetch existing eBay sales to check duplicates efficiently
        existing_sales = await sales_db.get_sales(
            channel="ebay",
            start_date=None,
            end_date=None,
            sku=None,
            limit=10000,
            offset=0,
        )
        existing_refs = {s.get("order_ref") for s in existing_sales if s.get("order_ref")}

        for order in orders:
            try:
                order_id = order.get("orderId", "")

                # Skip if already imported
                if order_id in existing_refs:
                    skipped_count += 1
                    continue

                # Extract buyer info
                buyer = order.get("buyer", {})
                buyer_name = buyer.get("username", "")

                # Extract total price
                price_summary = order.get("pricingSummary", {})
                total = price_summary.get("total", {})
                total_price = float(total.get("value", 0))
                currency = total.get("currency", "EUR")

                # Extract line items for SKU and product info
                line_items = order.get("lineItems", [])
                creation_date = order.get("creationDate")

                if not line_items:
                    # Record order-level sale if no line items
                    sale_data = {
                        "sku": "EBAY-UNKNOWN",
                        "channel": "ebay",
                        "sale_price": total_price,
                        "quantity": 1,
                        "order_ref": order_id,
                        "customer_name": buyer_name,
                        "notes": f"eBay order {order_id} (no line items)",
                        "sold_at": creation_date or datetime.utcnow().isoformat(),
                    }
                    result = await sales_db.record_sale(sale_data)
                    if result:
                        imported_count += 1
                    continue

                # Import each line item as a separate sale record
                for item in line_items:
                    sku = item.get("sku") or item.get("legacyItemId", "EBAY-NOSKU")
                    title = item.get("title", "")
                    qty = item.get("quantity", 1)

                    item_price_info = item.get("total", item.get("lineItemCost", {}))
                    item_price = float(item_price_info.get("value", 0)) if item_price_info else total_price

                    # Build a unique ref per line item to avoid duplicates
                    line_ref = f"{order_id}:{item.get('lineItemId', sku)}"

                    if line_ref in existing_refs:
                        skipped_count += 1
                        continue

                    sale_data = {
                        "sku": sku,
                        "channel": "ebay",
                        "sale_price": item_price,
                        "quantity": qty,
                        "order_ref": line_ref,
                        "customer_name": buyer_name,
                        "notes": f"eBay order {order_id}",
                        "sold_at": creation_date or datetime.utcnow().isoformat(),
                        "product_title": title,
                    }

                    result = await sales_db.record_sale(sale_data)
                    if result:
                        imported_count += 1
                        existing_refs.add(line_ref)

            except Exception as e:
                errors.append({
                    "order_id": order.get("orderId"),
                    "error": str(e)
                })

        return {
            "imported_count": imported_count,
            "skipped_count": skipped_count,
            "errors": errors,
            "message": f"Imported {imported_count} eBay sales, skipped {skipped_count} duplicates"
        }

    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(
            status_code=500,
            detail=f"Error importing eBay orders: {str(e)}"
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

        sales = await sales_db.get_sales(
            channel=channel_value,
            start_date=start_date,
            end_date=end_date,
            sku=sku,
            limit=limit,
            offset=offset
        )

        return [SaleResponse(**_map_sale(s)) for s in sales]

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
        summary = await sales_db.get_revenue_summary(start_date, end_date)

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
        daily_data = await sales_db.get_daily_revenue(days)

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
        breakdown = await sales_db.get_channel_breakdown(start_date, end_date)

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

        result = await sales_db.update_sale(sale_id, sale_data)

        if not result:
            raise HTTPException(
                status_code=404,
                detail=f"Sale with ID {sale_id} not found"
            )

        return SaleResponse(**_map_sale(result))

    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error updating sale: {str(e)}")


@router.delete("/{sale_id}")
async def delete_sale(sale_id: str):
    """
    Delete a sale record. If the deleted SKU has no remaining sales
    and was never sold on Shopify, set the Shopify product to draft.
    """
    try:
        # Cast to int — DB uses integer IDs
        try:
            sid = int(sale_id)
        except (ValueError, TypeError):
            raise HTTPException(status_code=400, detail="Invalid sale ID")

        # Fetch the sale first so we know the SKU
        import aiosqlite
        from app.config import settings
        sku_to_check = None
        async with aiosqlite.connect(settings.SALES_DB_PATH) as db:
            db.row_factory = aiosqlite.Row
            cur = await db.execute("SELECT sku, channel FROM sales WHERE id = ?", (sid,))
            row = await cur.fetchone()
            if row:
                sku_to_check = dict(row).get("sku")

        success = await sales_db.delete_sale(sid)
        if not success:
            raise HTTPException(status_code=404, detail=f"Sale with ID {sale_id} not found")

        # Check if this SKU still has remaining sales
        if sku_to_check and not sku_to_check.startswith("NOSSKU-"):
            remaining = await sales_db.get_sales(
                channel=None, start_date=None, end_date=None,
                sku=sku_to_check, limit=1000, offset=0,
            )
            if not remaining:
                # No more sales for this SKU — unmark as unlisted
                await sales_db.unmark_sku_unlisted(sku_to_check)
                # Set Shopify product to draft (only if not sold via Shopify)
                shopify_sold = any(s.get("channel") == "shopify" for s in remaining)
                if not shopify_sold:
                    try:
                        all_products = await shopify_client.get_all_products()
                        for p in all_products:
                            for v in p.get("variants", []):
                                if v.get("sku") == sku_to_check:
                                    await shopify_client.set_product_status(p["id"], "draft")
                                    break
                    except Exception:
                        pass  # Best effort — don't fail the delete

        return {"status": "deleted", "sale_id": sale_id}

    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error deleting sale: {str(e)}")


class SyncShopifyRequest(BaseModel):
    sku: Optional[str] = Field(default=None, description="Single SKU to sync (optional)")


@router.post("/sync-shopify")
async def sync_sale_to_shopify(body: SyncShopifyRequest):
    """
    Set Shopify inventory to 0 for a specific SKU after an in-person or
    marketplace sale. Also updates the eBay metafield to ENDED if present.
    """
    if not body.sku:
        raise HTTPException(status_code=400, detail="SKU is required")
    all_products = await shopify_client.get_all_products()
    for p in all_products:
        for v in p.get("variants", []):
            if v.get("sku") == body.sku:
                result = await shopify_client.set_inventory_to_zero(p["id"])
                try:
                    await shopify_client.set_product_metafield(
                        p["id"], "ebay", "ebay_status", "ENDED"
                    )
                except Exception:
                    pass
                await sales_db.mark_sku_unlisted(body.sku)
                return {"status": "synced", "sku": body.sku, "result": result}

    raise HTTPException(status_code=404, detail=f"SKU {body.sku} not found in Shopify")


@router.post("/sync-shopify-all")
async def sync_all_sales_to_shopify():
    """
    Set Shopify inventory to 0 for ALL unique SKUs in the sales list.
    Skips NOSSKU- entries. Also marks eBay status as ENDED.
    """
    all_sales = await sales_db.get_sales(
        channel=None, start_date=None, end_date=None,
        sku=None, limit=10000, offset=0,
    )
    # Collect unique real SKUs
    skus = set()
    for s in all_sales:
        sku = s.get("sku", "")
        if sku and not sku.startswith("NOSSKU-"):
            skus.add(sku)

    if not skus:
        return {"status": "nothing_to_sync", "synced": 0}

    all_products = await shopify_client.get_all_products()
    # Build SKU → product map
    sku_product = {}
    for p in all_products:
        for v in p.get("variants", []):
            if v.get("sku") in skus:
                sku_product[v["sku"]] = p

    synced = []
    errors = []
    for sku in skus:
        product = sku_product.get(sku)
        if not product:
            continue
        try:
            await shopify_client.set_inventory_to_zero(product["id"])
            try:
                await shopify_client.set_product_metafield(
                    product["id"], "ebay", "ebay_status", "ENDED"
                )
            except Exception:
                pass
            await sales_db.mark_sku_unlisted(sku)
            synced.append(sku)
        except Exception as e:
            errors.append({"sku": sku, "error": str(e)})

    return {
        "status": "done",
        "synced": len(synced),
        "synced_skus": synced,
        "not_found": list(skus - set(sku_product.keys())),
        "errors": errors,
    }


@router.get("/shopify-statuses")
async def get_shopify_statuses():
    """
    Return Shopify product status for every SKU in the sales list.
    Returns a dict mapping SKU → {status, inventory}.
    """
    all_sales = await sales_db.get_sales(
        channel=None, start_date=None, end_date=None,
        sku=None, limit=10000, offset=0,
    )
    skus = set()
    for s in all_sales:
        sku = s.get("sku", "")
        if sku and not sku.startswith("NOSSKU-"):
            skus.add(sku)

    if not skus:
        return {"statuses": {}}

    # Get explicitly unlisted SKUs from our DB
    unlisted_skus_set = set(await sales_db.get_unlisted_skus())

    def _classify(p_status, inv, sku_val):
        """Derive effective status for a SKU in the sales list."""
        if sku_val in unlisted_skus_set:
            return "unlisted"
        return p_status

    # Try REST API first (separate calls per status)
    try:
        products = await shopify_client.get_all_products_all_statuses()
    except Exception:
        products = []

    result = {}
    for p in products:
        p_status = p.get("status", "unknown")
        for v in p.get("variants", []):
            sku = v.get("sku", "")
            if sku in skus:
                inv = v.get("inventory_quantity", v.get("inventoryQuantity", None))
                result[sku] = {"status": _classify(p_status, inv, sku), "inventory": inv}

    # For any SKUs still missing, try GraphQL as fallback
    missing = skus - set(result.keys()) - {s for s in skus if s.startswith("NOSSKU-")}
    if missing:
        try:
            gql_products = await shopify_client.get_products_with_metafields()
            for p in gql_products:
                p_status = p.get("status", "unknown")
                for v in p.get("variants", []):
                    sku = v.get("sku", "")
                    if sku in missing:
                        inv = v.get("inventory_quantity", v.get("inventoryQuantity", None))
                        result[sku] = {"status": _classify(p_status, inv, sku), "inventory": inv}
        except Exception:
            pass

    # Mark SKUs still not found
    for sku in skus:
        if sku not in result:
            result[sku] = {"status": "not_found", "inventory": None}

    return {"statuses": result}



@router.get("/shopify-sold")
async def get_shopify_sold():
    """
    Return Shopify products that are ACTIVE with stock <= 0
    and whose SKU is NOT in the recorded sales list.
    These are products sold directly via Shopify.
    """
    # Get all SKUs from sales DB
    all_sales = await sales_db.get_sales(
        channel=None, start_date=None, end_date=None,
        sku=None, limit=10000, offset=0,
    )
    recorded_skus = set()
    for s in all_sales:
        sku = s.get("sku", "")
        if sku and not sku.startswith("NOSSKU-"):
            recorded_skus.add(sku)

    # Fetch all Shopify products
    try:
        products = await shopify_client.get_all_products_all_statuses()
    except Exception:
        products = []

    shopify_sold = []
    for p in products:
        if p.get("status") != "active":
            continue
        for v in p.get("variants", []):
            sku = v.get("sku", "")
            inv = v.get("inventory_quantity", None)
            if sku and inv is not None and inv <= 0 and sku not in recorded_skus:
                shopify_sold.append({
                    "sku": sku,
                    "title": p.get("title", ""),
                    "product_id": p.get("id"),
                    "inventory": inv,
                })

    return {"products": shopify_sold, "count": len(shopify_sold)}


@router.get("/check-sku/{sku}")
async def check_sku_exists(sku: str):
    """Check if a SKU already has a sale recorded."""
    existing = await sales_db.get_sales(
        channel=None, start_date=None, end_date=None,
        sku=sku, limit=1, offset=0,
    )
    return {"exists": len(existing) > 0, "sku": sku}
