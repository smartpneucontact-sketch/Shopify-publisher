"""eBay integration routes — OAuth flow, publishing, policies, status."""

from fastapi import APIRouter, HTTPException, Query, Request
from fastapi.responses import RedirectResponse, JSONResponse
from pydantic import BaseModel
from typing import Optional
from app.services.ebay import ebay_tokens, ebay_client
import hashlib
import os

router = APIRouter()

# ── eBay Marketplace Account Deletion Notification ────────────────
EBAY_VERIFICATION_TOKEN = os.getenv("EBAY_VERIFICATION_TOKEN", "")
EBAY_NOTIFICATION_ENDPOINT = os.getenv("EBAY_NOTIFICATION_ENDPOINT", "")


@router.get("/notifications/account-deletion")
async def ebay_account_deletion_verify(challenge_code: str = Query(...)):
    """
    eBay verification challenge — responds with hash of
    challengeCode + verificationToken + endpoint URL.
    """
    token = EBAY_VERIFICATION_TOKEN
    endpoint = EBAY_NOTIFICATION_ENDPOINT
    # Hash = SHA256(challengeCode + verificationToken + endpoint)
    hash_input = challenge_code + token + endpoint
    response_hash = hashlib.sha256(hash_input.encode("utf-8")).hexdigest()
    return JSONResponse(
        content={"challengeResponse": response_hash},
        headers={"Content-Type": "application/json"},
    )


@router.post("/notifications/account-deletion")
async def ebay_account_deletion_notification(request: Request):
    """
    Handle eBay marketplace account deletion notifications.
    Log the event and return 200 OK.
    """
    body = await request.json()
    print(f"🔔 eBay Account Deletion Notification: {body}")
    # In production, you would delete user data here
    return {"status": "ok"}


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
    """Create a Colissimo fulfillment policy for eBay France."""
    try:
        return await ebay_client.create_fulfillment_policy({
            "name": "Expédition Colissimo FR",
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
        raise HTTPException(status_code=500, detail=str(e))


@router.api_route("/policies/add-eu-shipping/{policy_id}", methods=["GET", "POST"])
async def ebay_add_eu_shipping(policy_id: str):
    """
    Update an existing fulfillment policy with 3-tier shipping:
    - France: FREE
    - DE/NL/BE/LU: €15
    - Rest of EU: €20
    """
    try:
        policy_data = {
            "name": "Livraison FR Gratuit + EU",
            "marketplaceId": "EBAY_FR",
            "categoryTypes": [{"name": "ALL_EXCLUDING_MOTORS_VEHICLES"}],
            "handlingTime": {"value": 3, "unit": "DAY"},
            "shippingOptions": [
                {
                    "optionType": "DOMESTIC",
                    "costType": "FLAT_RATE",
                    "shippingServices": [{
                        "sortOrder": 1,
                        "shippingCarrierCode": "Colissimo",
                        "shippingServiceCode": "FR_ColiposteColissimo",
                        "shippingCost": {"value": "0.00", "currency": "EUR"},
                        "additionalShippingCost": {"value": "0.00", "currency": "EUR"},
                        "freeShipping": True,
                    }]
                },
                {
                    "optionType": "INTERNATIONAL",
                    "costType": "FLAT_RATE",
                    "shippingServices": [
                        {
                            "sortOrder": 1,
                            "shippingServiceCode": "FR_StandardInternational",
                            "shippingCost": {"value": "15.00", "currency": "EUR"},
                            "additionalShippingCost": {"value": "10.00", "currency": "EUR"},
                            "freeShipping": False,
                            "shipToLocations": {
                                "regionIncluded": [
                                    {"regionName": "Europe", "regionType": "WORLD_REGION"}
                                ]
                            }
                        },
                    ]
                }
            ]
        }
        result = await ebay_client.update_fulfillment_policy(policy_id, policy_data)
        return {
            "status": "success",
            "policy_id": policy_id,
            "tiers": {
                "france": "GRATUIT",
                "europe": "€15",
            },
            "result": result,
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.api_route("/policies/create-gls-eu", methods=["GET", "POST"])
async def ebay_create_gls_eu_policy():
    """
    Create a new fulfillment policy with 3-tier shipping:
    - France: FREE (Colissimo)
    - DE/NL/BE/LU: €15
    - Rest of EU: €20
    """
    try:
        result = await ebay_client.create_fulfillment_policy({
            "name": "Livraison FR Gratuit + EU",
            "marketplaceId": "EBAY_FR",
            "categoryTypes": [{"name": "ALL_EXCLUDING_MOTORS_VEHICLES"}],
            "handlingTime": {"value": 3, "unit": "DAY"},
            "shippingOptions": [
                {
                    "optionType": "DOMESTIC",
                    "costType": "FLAT_RATE",
                    "shippingServices": [{
                        "sortOrder": 1,
                        "shippingCarrierCode": "Colissimo",
                        "shippingServiceCode": "FR_ColiposteColissimo",
                        "shippingCost": {"value": "0.00", "currency": "EUR"},
                        "additionalShippingCost": {"value": "0.00", "currency": "EUR"},
                        "freeShipping": True,
                    }]
                },
                {
                    "optionType": "INTERNATIONAL",
                    "costType": "FLAT_RATE",
                    "shippingServices": [
                        {
                            "sortOrder": 1,
                            "shippingServiceCode": "FR_StandardInternational",
                            "shippingCost": {"value": "15.00", "currency": "EUR"},
                            "additionalShippingCost": {"value": "10.00", "currency": "EUR"},
                            "freeShipping": False,
                            "shipToLocations": {
                                "regionIncluded": [
                                    {"regionName": "Europe", "regionType": "WORLD_REGION"}
                                ]
                            }
                        },
                    ]
                }
            ]
        })
        return {
            "status": "success",
            "tiers": {
                "france": "GRATUIT",
                "europe": "€15",
            },
            "result": result,
        }
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


@router.get("/shipping-services")
async def ebay_shipping_services(search: str = Query(default="", description="Filter by name, e.g. 'GLS', 'Colissimo'")):
    """Get all available shipping services for eBay France. Optionally filter by name."""
    try:
        result = await ebay_client.get_shipping_services("EBAY_FR")
        if result.get("status") == "error":
            return result
        # Try multiple possible response keys
        services = (result.get("shippingServices", [])
                    or result.get("shippingService", [])
                    or result.get("ShippingService", []))
        if search and services:
            search_lower = search.lower()
            services = [s for s in services if search_lower in str(s).lower()]
        return {
            "total": len(services),
            "search": search or "(all)",
            "services": services[:50],
            "raw_keys": list(result.keys()) if isinstance(result, dict) else str(type(result)),
        }
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

        # Collect raw metafield values for title building
        meta_raw = {}
        for mf in p.get("metafields", []):
            if mf["namespace"] == "custom" and mf.get("value"):
                meta_raw[mf["key"]] = str(mf["value"])

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
                    "season": "Type de pneu",
                }
                if key in label_map and mf.get("value"):
                    val = str(mf["value"])
                    # Add "mm" suffix for tread depth if not already present
                    if key == "tread_depth" and "mm" not in val:
                        val = f"{val} mm"
                    aspects[label_map[key]] = [val]

        # Brand comes from Shopify vendor field
        brand = p.get("vendor", "")
        if brand:
            aspects["Marque"] = [brand]

        # Build eBay title: Brand Model Width/Height RRim XL LoadSpeed
        # Example: Michelin Primacy 3 205/45 R17 XL 88W
        model = meta_raw.get("model", "")
        largeur = meta_raw.get("largeur", "")
        hauteur = meta_raw.get("hauteur", "")
        rayon = meta_raw.get("rayon", "")
        load_idx = meta_raw.get("load_index", "")
        speed_idx = meta_raw.get("speed_index", "")

        # Detect XL/Renforcé from original Shopify title, tags, or collections
        original_title = (p.get("title", "") + " " + p.get("tags", "")).upper()
        collections = [c.lower() for c in p.get("collections", [])]
        is_xl = "XL" in original_title or "RENFORCÉ" in original_title or "RENFORCE" in original_title or "EXTRA LOAD" in original_title or any("renforc" in c or "extra load" in c or c == "xl" for c in collections)

        size_part = ""
        if largeur and hauteur:
            size_part = f"{largeur}/{hauteur}"
        elif largeur:
            size_part = largeur

        rim_part = f"R{rayon}" if rayon else ""
        xl_part = "XL" if is_xl else ""
        index_part = f"{load_idx}{speed_idx}" if load_idx or speed_idx else ""

        title_parts = [brand, model, size_part, rim_part, xl_part, index_part]
        ebay_title = " ".join(part for part in title_parts if part).strip()

        # Fallback to Shopify title if we couldn't build a good one
        if len(ebay_title) < 10:
            ebay_title = p.get("title", "Pneu occasion")

        # eBay title limit is 80 chars
        ebay_title = ebay_title[:80]

        # Build description
        ebay_description = p.get("body_html", "") or p.get("title", "")

        # Quantité = tire_count / 2 (sold in pairs)
        qty_mf = next((mf["value"] for mf in p.get("metafields", [])
                       if mf.get("key") == "tire_count"), None)
        tire_count = int(qty_mf) if qty_mf else 2
        quantite = max(1, tire_count // 2)
        aspects["Quantité"] = [str(quantite)]

        # Default Type de pneu to Été if not set
        if "Type de pneu" not in aspects:
            aspects["Type de pneu"] = ["Été"]

        # MPN — use SKU or "Non applicable"
        if "Numéro de pièce fabricant" not in aspects:
            aspects["Numéro de pièce fabricant"] = ["Non applicable"]

        # Runflat — check if product is in runflat collection
        is_runflat = any("runflat" in c or "run flat" in c or "run-flat" in c for c in collections)
        aspects["Runflat"] = ["Oui"] if is_runflat else ["Non"]

        # Inventory quantity = tire_count / 2
        quantity = quantite

        result = await ebay_client.publish_product(
            sku=sku,
            title=ebay_title,
            description=ebay_description,
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

        # Save eBay listing data to Shopify metafields on success
        if result.get("status") == "published" and result.get("listing_id"):
            try:
                from datetime import datetime
                product_id = p["id"]
                await shopify_client.set_product_metafields_bulk(product_id, [
                    {"key": "listing_id", "value": str(result["listing_id"])},
                    {"key": "offer_id", "value": str(result["offer_id"])},
                    {"key": "ebay_url", "value": result.get("ebay_url", "")},
                    {"key": "published_at", "value": datetime.utcnow().isoformat()},
                    {"key": "ebay_status", "value": "ACTIVE"},
                ])
            except Exception as e:
                result["metafield_warning"] = f"Published but failed to save metafields: {e}"

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

    # Collect raw metafield values
    meta_raw = {}
    for mf in p.get("metafields", []):
        if mf["namespace"] == "custom" and mf.get("value"):
            meta_raw[mf["key"]] = str(mf["value"])

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
                "season": "Type de pneu",
            }
            if key in label_map and mf.get("value"):
                val = str(mf["value"])
                if key == "tread_depth" and "mm" not in val:
                    val = f"{val} mm"
                aspects[label_map[key]] = [val]

    # Brand comes from Shopify vendor field
    brand = p.get("vendor", "")
    if brand:
        aspects["Marque"] = [brand]

    # Build eBay title
    model = meta_raw.get("model", "")
    largeur = meta_raw.get("largeur", "")
    hauteur = meta_raw.get("hauteur", "")
    rayon = meta_raw.get("rayon", "")
    load_idx = meta_raw.get("load_index", "")
    speed_idx = meta_raw.get("speed_index", "")
    original_title = (p.get("title", "") + " " + p.get("tags", "")).upper()
    collections = [c.lower() for c in p.get("collections", [])]
    is_xl = "XL" in original_title or "RENFORCÉ" in original_title or "RENFORCE" in original_title or "EXTRA LOAD" in original_title or any("renforc" in c or "extra load" in c or c == "xl" for c in collections)

    size_part = f"{largeur}/{hauteur}" if largeur and hauteur else largeur
    rim_part = f"R{rayon}" if rayon else ""
    xl_part = "XL" if is_xl else ""
    index_part = f"{load_idx}{speed_idx}" if load_idx or speed_idx else ""
    title_parts = [brand, model, size_part, rim_part, xl_part, index_part]
    ebay_title = " ".join(part for part in title_parts if part).strip()
    if len(ebay_title) < 10:
        ebay_title = p.get("title", "Pneu occasion")
    ebay_title = ebay_title[:80]

    # Description
    ebay_description = p.get("body_html", "") or p.get("title", "")

    # Quantité = tire_count / 2 (sold in pairs)
    qty_mf = next((mf["value"] for mf in p.get("metafields", [])
                    if mf.get("key") == "tire_count"), None)
    tire_count = int(qty_mf) if qty_mf else 2
    quantite = max(1, tire_count // 2)
    aspects["Quantité"] = [str(quantite)]

    # Default Type de pneu to Été if not set
    if "Type de pneu" not in aspects:
        aspects["Type de pneu"] = ["Été"]

    # MPN — default to "Non applicable"
    if "Numéro de pièce fabricant" not in aspects:
        aspects["Numéro de pièce fabricant"] = ["Non applicable"]

    # Runflat — check if product is in runflat collection
    is_runflat = any("runflat" in c or "run flat" in c or "run-flat" in c for c in collections)
    aspects["Runflat"] = ["Oui"] if is_runflat else ["Non"]

    quantity = quantite

    item_data = {
        "availability": {
            "shipToLocationAvailability": {"quantity": max(1, quantity)}
        },
        "condition": "USED_EXCELLENT",
        "product": {
            "title": ebay_title,
            "description": ebay_description,
            "imageUrls": images[:12],
            "aspects": aspects,
        },
    }

    # Test step 1: create inventory item
    step1_result = await ebay_client.create_or_replace_item(sku, item_data)

    return {
        "sku": sku,
        "shopify_title": p.get("title"),
        "ebay_title": ebay_title,
        "is_xl": is_xl,
        "is_runflat": is_runflat,
        "collections": p.get("collections", []),
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
    tire_count = int(qty_mf) if qty_mf else 2
    quantity = max(1, tire_count // 2)

    ebay_description = p.get("body_html", "") or p.get("title", "")

    offer_data = {
        "sku": sku,
        "marketplaceId": "EBAY_FR",
        "format": "FIXED_PRICE",
        "availableQuantity": max(1, quantity),
        "categoryId": category_id,
        "listingDescription": ebay_description,
        "listingPolicies": {
            "fulfillmentPolicyId": fulfillment_policy_id,
            "paymentPolicyId": payment_policy_id,
            "returnPolicyId": return_policy_id,
            "bestOfferTerms": {
                "bestOfferEnabled": True,
            },
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


# ── Listing Status & Analytics ──────────────────────────────────
@router.get("/listings")
async def ebay_listings():
    """Get eBay listing status for all products that have eBay metafields."""
    from app.services.shopify import shopify_client

    products = await shopify_client.get_products_with_metafields()
    listings = []

    for p in products:
        ebay_mf = {}
        sku = None
        for mf in p.get("metafields", []):
            if mf["namespace"] == "ebay":
                ebay_mf[mf["key"]] = mf["value"]
            if mf["namespace"] == "custom" and mf["key"] == "tire_count":
                ebay_mf["tire_count"] = mf["value"]
        for v in p.get("variants", []):
            if v.get("sku"):
                sku = v["sku"]
                break

        if ebay_mf.get("listing_id"):
            listings.append({
                "product_id": p["id"],
                "sku": sku,
                "title": p.get("title", ""),
                "listing_id": ebay_mf.get("listing_id"),
                "offer_id": ebay_mf.get("offer_id"),
                "ebay_url": ebay_mf.get("ebay_url"),
                "ebay_status": ebay_mf.get("ebay_status", "UNKNOWN"),
                "published_at": ebay_mf.get("published_at"),
            })

    return {"total": len(listings), "listings": listings}


@router.get("/listings/analytics")
async def ebay_listings_analytics():
    """Fetch analytics (views, impressions) for all published listings."""
    from app.services.shopify import shopify_client

    products = await shopify_client.get_products_with_metafields()
    listing_ids = []
    listing_map = {}

    for p in products:
        for mf in p.get("metafields", []):
            if mf["namespace"] == "ebay" and mf["key"] == "listing_id" and mf["value"]:
                lid = mf["value"]
                listing_ids.append(lid)
                sku = next((v["sku"] for v in p.get("variants", []) if v.get("sku")), None)
                listing_map[lid] = {"product_id": p["id"], "sku": sku, "title": p.get("title", "")}

    if not listing_ids:
        return {"total": 0, "listings": []}

    # Try to get analytics from eBay
    analytics = await ebay_client.get_listing_analytics(listing_ids)

    # Also get offer details for each listing to confirm status
    offer_details = {}
    for p in products:
        offer_id = None
        for mf in p.get("metafields", []):
            if mf["namespace"] == "ebay" and mf["key"] == "offer_id":
                offer_id = mf["value"]
        if offer_id:
            try:
                detail = await ebay_client.get_offer_details(offer_id)
                if detail.get("status") != "error":
                    lid = detail.get("listing", {}).get("listingId") or detail.get("listingId")
                    offer_details[offer_id] = {
                        "offer_status": detail.get("status"),
                        "listing_status": detail.get("listing", {}).get("listingStatus"),
                        "listing_id": lid,
                    }
            except Exception:
                pass

    result = []
    for lid, info in listing_map.items():
        entry = {**info, "listing_id": lid}
        # Merge analytics if available
        if isinstance(analytics, dict) and analytics.get("dimensionMetrics"):
            for dm in analytics["dimensionMetrics"]:
                if dm.get("dimensionValues", [{}])[0].get("value") == lid:
                    metrics = {}
                    for m in dm.get("metrics", []):
                        metrics[m.get("name", "").lower()] = m.get("value")
                    entry["analytics"] = metrics
        # Merge offer details
        for p in products:
            for mf in p.get("metafields", []):
                if mf["namespace"] == "ebay" and mf["key"] == "offer_id" and mf["value"] in offer_details:
                    if str(p["id"]) == str(info["product_id"]):
                        entry["offer_details"] = offer_details[mf["value"]]
        result.append(entry)

    return {"total": len(result), "listings": result, "raw_analytics": analytics}


@router.post("/listings/refresh-status")
async def ebay_refresh_listing_status():
    """Refresh eBay status for all published listings by checking offer status."""
    from app.services.shopify import shopify_client

    products = await shopify_client.get_products_with_metafields()
    updated = []

    for p in products:
        offer_id = None
        has_listing = False
        for mf in p.get("metafields", []):
            if mf["namespace"] == "ebay" and mf["key"] == "offer_id":
                offer_id = mf["value"]
            if mf["namespace"] == "ebay" and mf["key"] == "listing_id":
                has_listing = True

        if not offer_id or not has_listing:
            continue

        try:
            detail = await ebay_client.get_offer_details(offer_id)
            if detail.get("status") != "error":
                offer_status = detail.get("status", "UNKNOWN")
                listing_status = detail.get("listing", {}).get("listingStatus", "")
                # Map to simple status: ACTIVE, ENDED, ERROR
                simple_status = "ACTIVE" if offer_status == "PUBLISHED" else offer_status
                if listing_status in ("ENDED", "COMPLETED"):
                    simple_status = "ENDED"

                await shopify_client.set_product_metafield(
                    p["id"], "ebay", "ebay_status", simple_status
                )
                sku = next((v["sku"] for v in p.get("variants", []) if v.get("sku")), None)
                updated.append({
                    "product_id": p["id"],
                    "sku": sku,
                    "offer_status": offer_status,
                    "listing_status": listing_status,
                    "ebay_status": simple_status,
                })
        except Exception as e:
            updated.append({"product_id": p["id"], "error": str(e)})

    return {"updated": len(updated), "results": updated}


@router.post("/sync")
async def ebay_sync_inventory():
    """
    Full sync: refresh eBay listing status → detect ENDED/SOLD items →
    set Shopify inventory to 0 → import eBay orders as sales.

    This is the "master sync" button that keeps everything in sync:
    - eBay listings that ended (sold) → Shopify stock goes to 0
    - eBay orders → recorded in sales database
    - Metafields updated with current status
    """
    from app.services.shopify import shopify_client
    from app.services.sales_db import sales_db

    products = await shopify_client.get_products_with_metafields()
    synced = []
    ended_products = []
    errors = []

    # Step 1: Check every product that has eBay metafields
    for p in products:
        offer_id = None
        listing_id = None
        old_status = None
        for mf in p.get("metafields", []):
            if mf["namespace"] == "ebay" and mf["key"] == "offer_id":
                offer_id = mf["value"]
            if mf["namespace"] == "ebay" and mf["key"] == "listing_id":
                listing_id = mf["value"]
            if mf["namespace"] == "ebay" and mf["key"] == "ebay_status":
                old_status = mf["value"]

        if not offer_id or not listing_id:
            continue

        sku = next((v["sku"] for v in p.get("variants", []) if v.get("sku")), None)

        try:
            detail = await ebay_client.get_offer_details(offer_id)
            if detail.get("status") == "error":
                # Offer no longer exists on eBay → treat as ENDED
                new_status = "ENDED"
            else:
                offer_status = detail.get("status", "UNKNOWN")
                listing_status = detail.get("listing", {}).get("listingStatus", "")
                new_status = "ACTIVE" if offer_status == "PUBLISHED" else offer_status
                if listing_status in ("ENDED", "COMPLETED", "SOLD"):
                    new_status = "ENDED"

            # Update metafield
            await shopify_client.set_product_metafield(
                p["id"], "ebay", "ebay_status", new_status
            )

            entry = {
                "product_id": p["id"],
                "sku": sku,
                "old_status": old_status,
                "new_status": new_status,
            }

            # Step 2: If listing ended and was previously active, zero Shopify inventory
            if new_status == "ENDED" and old_status != "ENDED":
                try:
                    zero_result = await shopify_client.set_inventory_to_zero(p["id"])
                    entry["shopify_inventory"] = "zeroed"
                    entry["zero_details"] = zero_result
                    ended_products.append(entry)
                except Exception as e:
                    entry["shopify_inventory"] = f"error: {str(e)}"
                    errors.append({"product_id": p["id"], "sku": sku, "error": str(e)})

            synced.append(entry)

        except Exception as e:
            errors.append({"product_id": p["id"], "sku": sku, "error": str(e)})

    # Step 3: Import eBay orders as sales
    import_result = {"imported_count": 0, "skipped_count": 0}
    try:
        if ebay_client.tokens.is_authenticated:
            orders = await ebay_client.get_all_recent_orders(days=90)
            existing_sales = sales_db.get_sales(
                channel="ebay", start_date=None, end_date=None,
                sku=None, limit=10000, offset=0,
            )
            existing_refs = {s.get("order_ref") for s in existing_sales if s.get("order_ref")}

            for order in orders:
                order_id = order.get("orderId", "")
                buyer = order.get("buyer", {})
                buyer_name = buyer.get("username", "")
                creation_date = order.get("creationDate")

                for item in order.get("lineItems", []):
                    item_sku = item.get("sku") or item.get("legacyItemId", "EBAY-NOSKU")
                    line_ref = f"{order_id}:{item.get('lineItemId', item_sku)}"

                    if line_ref in existing_refs:
                        import_result["skipped_count"] += 1
                        continue

                    item_price = float(item.get("total", item.get("lineItemCost", {})).get("value", 0))
                    sale_data = {
                        "sku": item_sku,
                        "channel": "ebay",
                        "sale_price": item_price,
                        "quantity": item.get("quantity", 1),
                        "order_ref": line_ref,
                        "customer_name": buyer_name,
                        "notes": f"eBay order {order_id} (auto-sync)",
                        "sold_at": creation_date,
                        "product_title": item.get("title", ""),
                    }
                    result = sales_db.record_sale(sale_data)
                    if result:
                        import_result["imported_count"] += 1
                        existing_refs.add(line_ref)
    except Exception as e:
        import_result["error"] = str(e)

    return {
        "total_checked": len(synced),
        "ended_and_zeroed": len(ended_products),
        "ended_products": ended_products,
        "sales_import": import_result,
        "errors": errors,
        "synced": synced,
    }
