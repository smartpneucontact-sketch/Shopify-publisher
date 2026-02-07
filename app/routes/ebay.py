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


# ── Seller Programs (Opt-In) ─────────────────────────────────
@router.get("/programs")
async def ebay_programs():
    """Check which eBay seller programs the account is opted into."""
    try:
        return await ebay_client.get_opted_in_programs()
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.api_route("/programs/opt-in", methods=["GET", "POST"])
async def ebay_opt_in_policies():
    """Opt in to eBay Selling Policy Management (required before creating business policies)."""
    try:
        return await ebay_client.opt_in_to_program("SELLING_POLICY_MANAGEMENT")
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


# ── Policies ──────────────────────────────────────────────────────
@router.get("/policies")
async def ebay_policies():
    """Fetch all eBay business policies (fulfillment, payment, return)."""
    try:
        return await ebay_client.get_all_policies()
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.api_route("/policies/create-defaults", methods=["GET", "POST"])
async def ebay_create_default_policies():
    """Create default fulfillment, payment, and return policies for eBay France."""
    results = {}

    # Fulfillment policy — flat rate €10, 3 day handling
    try:
        results["fulfillment"] = await ebay_client.create_fulfillment_policy({
            "name": "Expédition Standard FR",
            "marketplaceId": "EBAY_FR",
            "categoryTypes": [{"name": "ALL_EXCLUDING_MOTORS_VEHICLES"}],
            "handlingTime": {"value": 3, "unit": "DAY"},
            "shippingOptions": [{
                "optionType": "DOMESTIC",
                "costType": "FLAT_RATE",
                "shippingServices": [{
                    "sortOrder": 1,
                    "shippingCarrierCode": "Colissimo",
                    "shippingServiceCode": "FR_ColiposteColissimo",
                    "shippingCost": {"value": "10.00", "currency": "EUR"},
                    "additionalShippingCost": {"value": "5.00", "currency": "EUR"},
                    "freeShipping": False,
                }]
            }]
        })
    except Exception as e:
        results["fulfillment"] = {"status": "error", "detail": str(e)}

    # Payment policy — eBay managed payments (standard for EBAY_FR)
    try:
        results["payment"] = await ebay_client.create_payment_policy({
            "name": "Paiement Standard",
            "marketplaceId": "EBAY_FR",
            "categoryTypes": [{"name": "ALL_EXCLUDING_MOTORS_VEHICLES"}],
            "immediatePay": False,
        })
    except Exception as e:
        results["payment"] = {"status": "error", "detail": str(e)}

    # Return policy — 30 day returns
    try:
        results["return"] = await ebay_client.create_return_policy({
            "name": "Retours 30 jours",
            "marketplaceId": "EBAY_FR",
            "categoryTypes": [{"name": "ALL_EXCLUDING_MOTORS_VEHICLES"}],
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


@router.api_route("/locations/create-default", methods=["GET", "POST"])
async def ebay_create_default_location():
    """Create the default warehouse inventory location for eBay France."""
    try:
        return await ebay_client.create_location("default", {
            "location": {
                "address": {
                    "addressLine1": "1 rue Peyerimhoff",
                    "city": "Freyming-Merlebach",
                    "stateOrProvince": "Grand Est",
                    "postalCode": "57800",
                    "country": "FR",
                }
            },
            "locationTypes": ["WAREHOUSE"],
            "name": "SmartPneu Entrepôt",
            "merchantLocationStatus": "ENABLED",
        })
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.api_route("/policies/create-gls", methods=["GET", "POST"])
async def ebay_create_gls_policy():
    """Create a GLS fulfillment policy for eBay France."""
    try:
        return await ebay_client.create_fulfillment_policy({
            "name": "Expédition GLS FR",
            "marketplaceId": "EBAY_FR",
            "categoryTypes": [{"name": "ALL_EXCLUDING_MOTORS_VEHICLES"}],
            "handlingTime": {"value": 3, "unit": "DAY"},
            "shippingOptions": [{
                "optionType": "DOMESTIC",
                "costType": "FLAT_RATE",
                "shippingServices": [{
                    "sortOrder": 1,
                    "shippingCarrierCode": "GLS",
                    "shippingServiceCode": "FR_AuteModeDenvoiDeColis",
                    "shippingCost": {"value": "10.00", "currency": "EUR"},
                    "additionalShippingCost": {"value": "5.00", "currency": "EUR"},
                    "freeShipping": False,
                }]
            }]
        })
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


# ── Category Aspects ──────────────────────────────────────────────
@router.get("/category-aspects/{category_id}")
async def ebay_category_aspects(category_id: str):
    """Get required and recommended aspects for an eBay category."""
    try:
        result = await ebay_client.get_category_aspects(category_id)
        # Extract just the required ones for easy reading
        required = []
        if "aspects" in result:
            for aspect in result["aspects"]:
                constraint = aspect.get("aspectConstraint", {})
                if constraint.get("aspectRequired"):
                    required.append({
                        "name": aspect.get("localizedAspectName"),
                        "mode": constraint.get("aspectMode"),
                        "values": [v.get("localizedValue") for v in aspect.get("aspectValues", [])[:20]],
                    })
        return {"category_id": category_id, "required_aspects": required, "full_response": result}
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
                    "model": "Modèle",
                    "largeur": "Largeur de pneu",
                    "hauteur": "Rapport d'aspect",
                    "rayon": "Diamètre",
                    "tread_depth": "Profondeur des sculptures",
                    "dot": "Code de date DOT",
                    "speed_index": "Indice de vitesse",
                    "load_index": "Indice de charge",
                    "tire_count": "Quantité",
                    "season": "Type de pneu",
                }
                if key in label_map and mf.get("value"):
                    val = str(mf["value"])
                    # Add "mm" suffix for tread depth if not already present
                    if key == "tread_depth" and "mm" not in val:
                        val = f"{val} mm"
                    aspects[label_map[key]] = [val]

        # Brand comes from Shopify vendor field
        if p.get("vendor"):
            aspects["Marque"] = [p["vendor"]]

        # Default Quantité to 1 if not set
        if "Quantité" not in aspects:
            aspects["Quantité"] = ["1"]

        # Default Type de pneu to Été if not set
        if "Type de pneu" not in aspects:
            aspects["Type de pneu"] = ["Été"]

        # MPN — use SKU or "Non applicable"
        if "Numéro de pièce fabricant" not in aspects:
            aspects["Numéro de pièce fabricant"] = ["Non applicable"]

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


# ── Debug publish (step-by-step) ─────────────────────────────────
@router.get("/publish/debug/{sku}")
async def ebay_publish_debug(sku: str, category_id: str = "179680"):
    """
    Debug: shows what data would be sent to eBay for a given SKU,
    and tests Step 1 (create inventory item) only.
    """
    from app.services.shopify import shopify_client

    products = await shopify_client.get_products_with_metafields()
    entry = None
    for p in products:
        for v in p.get("variants", []):
            if v.get("sku") == sku:
                entry = {"product": p, "variant": v}
                break
        if entry:
            break

    if not entry:
        return {"error": f"SKU {sku} not found in Shopify"}

    p = entry["product"]
    v = entry["variant"]
    images = [img["src"] for img in p.get("images", []) if img.get("src")]

    aspects = {}
    for mf in p.get("metafields", []):
        if mf["namespace"] == "custom":
            key = mf["key"]
            label_map = {
                "model": "Modèle",
                "largeur": "Largeur de pneu",
                "hauteur": "Rapport d'aspect",
                "rayon": "Diamètre",
                "tread_depth": "Profondeur des sculptures",
                "dot": "Code de date DOT",
                "speed_index": "Indice de vitesse",
                "load_index": "Indice de charge",
                "tire_count": "Quantité",
                "season": "Type de pneu",
            }
            if key in label_map and mf.get("value"):
                val = str(mf["value"])
                if key == "tread_depth" and "mm" not in val:
                    val = f"{val} mm"
                aspects[label_map[key]] = [val]

    # Brand comes from Shopify vendor field
    if p.get("vendor"):
        aspects["Marque"] = [p["vendor"]]

    # Default Quantité to 1 if not set
    if "Quantité" not in aspects:
        aspects["Quantité"] = ["1"]

    # Default Type de pneu to Été if not set
    if "Type de pneu" not in aspects:
        aspects["Type de pneu"] = ["Été"]

    # MPN — default to "Non applicable"
    if "Numéro de pièce fabricant" not in aspects:
        aspects["Numéro de pièce fabricant"] = ["Non applicable"]

    qty_mf = next((mf["value"] for mf in p.get("metafields", [])
                    if mf.get("key") == "tire_count"), None)
    quantity = int(qty_mf) if qty_mf else int(v.get("inventory_quantity", 1))

    item_data = {
        "availability": {
            "shipToLocationAvailability": {"quantity": max(1, quantity)}
        },
        "condition": "USED_EXCELLENT",
        "product": {
            "title": p.get("title", "")[:80],
            "description": p.get("body_html", "") or p.get("title", ""),
            "imageUrls": images[:12],
            "aspects": aspects,
        },
    }

    # Test step 1: create inventory item
    step1_result = await ebay_client.create_or_replace_item(sku, item_data)

    return {
        "sku": sku,
        "shopify_title": p.get("title"),
        "shopify_price": v.get("price"),
        "quantity": max(1, quantity),
        "images_count": len(images),
        "aspects": aspects,
        "item_data_sent": item_data,
        "step1_create_item": step1_result,
    }


@router.get("/publish/debug-offer/{sku}")
async def ebay_publish_debug_offer(
    sku: str,
    category_id: str = "179680",
    fulfillment_policy_id: str = "6215604000",
    payment_policy_id: str = "6215599000",
    return_policy_id: str = "6215600000",
    location_key: str = "default",
):
    """
    Debug Step 2: create offer for an existing inventory item.
    Run /publish/debug/{sku} first to create the inventory item.
    """
    from app.services.shopify import shopify_client

    products = await shopify_client.get_products_with_metafields()
    entry = None
    for p in products:
        for v in p.get("variants", []):
            if v.get("sku") == sku:
                entry = {"product": p, "variant": v}
                break
        if entry:
            break

    if not entry:
        return {"error": f"SKU {sku} not found in Shopify"}

    p = entry["product"]
    v = entry["variant"]
    price = float(v.get("price", 0))

    qty_mf = next((mf["value"] for mf in p.get("metafields", [])
                    if mf.get("key") == "tire_count"), None)
    quantity = int(qty_mf) if qty_mf else int(v.get("inventory_quantity", 1))

    offer_data = {
        "sku": sku,
        "marketplaceId": "EBAY_FR",
        "format": "FIXED_PRICE",
        "availableQuantity": max(1, quantity),
        "categoryId": category_id,
        "listingDescription": p.get("body_html", "") or p.get("title", ""),
        "listingPolicies": {
            "fulfillmentPolicyId": fulfillment_policy_id,
            "paymentPolicyId": payment_policy_id,
            "returnPolicyId": return_policy_id,
        },
        "pricingSummary": {
            "price": {
                "value": str(max(0.01, price)),
                "currency": "EUR",
            }
        },
        "merchantLocationKey": location_key,
    }

    step2_result = await ebay_client.create_offer(offer_data)

    return {
        "sku": sku,
        "offer_data_sent": offer_data,
        "step2_create_offer": step2_result,
    }


# ── Withdraw ─────────────────────────────────────────────────────
@router.post("/offer/{offer_id}/publish")
async def ebay_publish_offer(offer_id: str):
    """Publish an existing offer by ID."""
    try:
        return await ebay_client.publish_offer(offer_id)
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/withdraw/{offer_id}")
async def ebay_withdraw(offer_id: str):
    """Withdraw (end) an eBay listing."""
    try:
        return await ebay_client.withdraw_offer(offer_id)
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
