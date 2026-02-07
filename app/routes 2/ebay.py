"""eBay integration routes — OAuth flow, publishing, policies, status."""

from fastapi import APIRouter, HTTPException, Query, Request
from fastapi.responses import RedirectResponse
from pydantic import BaseModel
from typing import Optional
from app.services.ebay import ebay_tokens, ebay_client

router = APIRouter()


# ── OAuth ─────────────────────────────────────────────────────────
@router.get("/status")
async def ebay_status():
    """Check eBay connection status."""
    import os
    return {
        "configured": ebay_tokens.is_configured,
        "authenticated": ebay_tokens.is_authenticated,
        "has_access_token": bool(ebay_tokens.access_token),
        "debug": {
            "client_id_set": bool(os.getenv("EBAY_CLIENT_ID")),
            "client_id_len": len(os.getenv("EBAY_CLIENT_ID", "")),
            "secret_set": bool(os.getenv("EBAY_CLIENT_SECRET")),
            "redirect_set": bool(os.getenv("EBAY_REDIRECT_URI")),
            "env": os.getenv("EBAY_ENV", "not set"),
        }
    }


@router.get("/auth/url")
async def ebay_auth_url():
    """Get the eBay OAuth consent URL."""
    if not ebay_tokens.is_configured:
        raise HTTPException(
            status_code=400,
            detail="eBay API keys not configured. Set EBAY_CLIENT_ID, EBAY_CLIENT_SECRET, EBAY_REDIRECT_URI."
        )
    return {"url": ebay_tokens.get_auth_url()}


@router.get("/auth/callback")
async def ebay_auth_callback(code: str = Query(...)):
    """eBay OAuth callback — exchange code for tokens."""
    try:
        result = await ebay_tokens.exchange_code(code)
        # Redirect back to dashboard with success
        return RedirectResponse(url="/dashboard?ebay=connected")
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"OAuth error: {str(e)}")


@router.post("/auth/token")
async def ebay_set_token(request: Request):
    """Manually set a refresh token (for initial setup)."""
    body = await request.json()
    refresh_token = body.get("refresh_token")
    if not refresh_token:
        raise HTTPException(status_code=400, detail="refresh_token required")
    ebay_tokens.refresh_token = refresh_token
    ebay_tokens._save_tokens()
    return {"status": "saved"}


# ── Policies ──────────────────────────────────────────────────────
@router.get("/policies")
async def ebay_policies():
    """Fetch all eBay business policies (fulfillment, payment, return)."""
    try:
        return await ebay_client.get_all_policies()
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/policies/create-defaults")
async def ebay_create_default_policies():
    """Create default fulfillment, payment, and return policies for eBay France."""
    results = {}

    # Fulfillment policy — flat rate €10, 3 day handling
    try:
        results["fulfillment"] = await ebay_client.create_fulfillment_policy({
            "name": "Expédition Standard FR",
            "marketplaceId": "EBAY_FR",
            "handlingTime": {"value": 3, "unit": "DAY"},
            "shippingOptions": [{
                "optionType": "DOMESTIC",
                "costType": "FLAT_RATE",
                "shippingServices": [{
                    "sortOrder": 1,
                    "shippingCarrierCode": "Colissimo",
                    "shippingServiceCode": "FR_ColossimoColissimo",
                    "shippingCost": {"value": "10.00", "currency": "EUR"},
                    "freeShipping": False,
                }]
            }]
        })
    except Exception as e:
        results["fulfillment"] = {"status": "error", "detail": str(e)}

    # Payment policy
    try:
        results["payment"] = await ebay_client.create_payment_policy({
            "name": "Paiement Standard",
            "marketplaceId": "EBAY_FR",
            "paymentMethods": [{"paymentMethodType": "PERSONAL_CHECK"}],
            "immediatePay": False,
        })
    except Exception as e:
        results["payment"] = {"status": "error", "detail": str(e)}

    # Return policy — 30 day returns
    try:
        results["return"] = await ebay_client.create_return_policy({
            "name": "Retours 30 jours",
            "marketplaceId": "EBAY_FR",
            "returnsAccepted": True,
            "returnPeriod": {"value": 30, "unit": "DAY"},
            "returnShippingCostPayer": "BUYER",
            "refundMethod": "MONEY_BACK",
        })
    except Exception as e:
        results["return"] = {"status": "error", "detail": str(e)}

    return results


# ── Inventory Locations ──────────────────────────────────────────
@router.get("/locations")
async def ebay_locations():
    """Get eBay inventory locations."""
    try:
        return await ebay_client.get_locations()
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


# ── Listing Status ───────────────────────────────────────────────
@router.get("/offers")
async def ebay_offers_for_sku(sku: str = Query(...)):
    """Check if a SKU has existing eBay offers/listings."""
    try:
        return await ebay_client.get_offers(sku)
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/items")
async def ebay_items(limit: int = Query(100), offset: int = Query(0)):
    """List all eBay inventory items."""
    try:
        return await ebay_client.get_items(limit=limit, offset=offset)
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


# ── Publish ──────────────────────────────────────────────────────
class PublishRequest(BaseModel):
    sku: str
    title: str
    description: Optional[str] = ""
    price: float
    quantity: int = 1
    condition: str = "used"
    images: list[str] = []
    category_id: str
    aspects: dict = {}
    fulfillment_policy_id: str
    payment_policy_id: str
    return_policy_id: str
    location_key: str = "default"


@router.post("/publish")
async def ebay_publish(req: PublishRequest):
    """Publish a product to eBay France."""
    try:
        result = await ebay_client.publish_product(
            sku=req.sku,
            title=req.title,
            description=req.description,
            price=req.price,
            quantity=req.quantity,
            condition=req.condition,
            images=req.images,
            category_id=req.category_id,
            aspects=req.aspects,
            fulfillment_policy_id=req.fulfillment_policy_id,
            payment_policy_id=req.payment_policy_id,
            return_policy_id=req.return_policy_id,
            location_key=req.location_key,
        )
        return result
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


class BulkPublishRequest(BaseModel):
    skus: list[str]
    category_id: str
    fulfillment_policy_id: str
    payment_policy_id: str
    return_policy_id: str
    location_key: str = "default"
    price_adjustment: float = 0  # Add/subtract from Shopify price


@router.post("/publish/bulk")
async def ebay_publish_bulk(req: BulkPublishRequest, request: Request):
    """
    Bulk publish: takes SKU list, fetches product data from Shopify,
    and publishes each to eBay.
    """
    from app.services.shopify import shopify_client

    try:
        # Fetch all Shopify products
        products = await shopify_client.get_products_with_metafields()
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Shopify fetch error: {e}")

    # Build SKU → product+variant map
    sku_map = {}
    for p in products:
        for v in p.get("variants", []):
            if v.get("sku") and v["sku"] in req.skus:
                sku_map[v["sku"]] = {"product": p, "variant": v}

    results = []
    for sku in req.skus:
        entry = sku_map.get(sku)
        if not entry:
            results.append({"sku": sku, "status": "error", "errors": "SKU not found in Shopify"})
            continue

        p = entry["product"]
        v = entry["variant"]
        price = float(v.get("price", 0)) + req.price_adjustment
        images = [img["src"] for img in p.get("images", []) if img.get("src")]

        # Build tire aspects from metafields
        aspects = {}
        for mf in p.get("metafields", []):
            if mf["namespace"] == "custom":
                key = mf["key"]
                label_map = {
                    "tire_provider": "Marque",
                    "model": "Modèle",
                    "largeur": "Largeur du pneu",
                    "hauteur": "Rapport d'aspect",
                    "rayon": "Diamètre de la jante",
                    "tread_depth": "Profondeur de la bande de roulement",
                    "dot": "DOT",
                    "speed_index": "Indice de vitesse",
                    "load_index": "Indice de charge",
                }
                if key in label_map and mf.get("value"):
                    aspects[label_map[key]] = [str(mf["value"])]

        # Get quantity from metafield or variant
        qty_mf = next((mf["value"] for mf in p.get("metafields", [])
                       if mf.get("key") == "tire_count"), None)
        quantity = int(qty_mf) if qty_mf else int(v.get("inventory_quantity", 1))

        result = await ebay_client.publish_product(
            sku=sku,
            title=p.get("title", ""),
            description=p.get("body_html", ""),
            price=max(0.01, price),
            quantity=max(1, quantity),
            condition="used",
            images=images,
            category_id=req.category_id,
            aspects=aspects,
            fulfillment_policy_id=req.fulfillment_policy_id,
            payment_policy_id=req.payment_policy_id,
            return_policy_id=req.return_policy_id,
            location_key=req.location_key,
        )
        results.append(result)

    published = sum(1 for r in results if r.get("status") == "published")
    return {
        "total": len(req.skus),
        "published": published,
        "failed": len(req.skus) - published,
        "results": results,
    }


# ── Withdraw ─────────────────────────────────────────────────────
@router.post("/withdraw/{offer_id}")
async def ebay_withdraw(offer_id: str):
    """Withdraw (end) an eBay listing."""
    try:
        return await ebay_client.withdraw_offer(offer_id)
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
