"""
eBay Sell Inventory API client — OAuth2 + listing management.
Target: eBay France (EBAY_FR), Fixed Price listings.

Flow:
  1. createOrReplaceInventoryItem (SKU → product data)
  2. createOffer (SKU → marketplace + price + policies)
  3. publishOffer (offer → live listing)
"""

import httpx
import base64
import json
import os
import time
from typing import Optional
from pathlib import Path

# ── Config ────────────────────────────────────────────────────────
EBAY_ENV = os.getenv("EBAY_ENV", "production")  # "sandbox" or "production"

if EBAY_ENV == "sandbox":
    EBAY_API_BASE = "https://api.sandbox.ebay.com"
    EBAY_AUTH_URL = "https://auth.sandbox.ebay.com/oauth2/authorize"
    EBAY_TOKEN_URL = "https://api.sandbox.ebay.com/identity/v1/oauth2/token"
else:
    EBAY_API_BASE = "https://api.ebay.com"
    EBAY_AUTH_URL = "https://auth.ebay.com/oauth2/authorize"
    EBAY_TOKEN_URL = "https://api.ebay.com/identity/v1/oauth2/token"

EBAY_CLIENT_ID = os.getenv("EBAY_CLIENT_ID", "")
EBAY_CLIENT_SECRET = os.getenv("EBAY_CLIENT_SECRET", "")
EBAY_REDIRECT_URI = os.getenv("EBAY_REDIRECT_URI", "")  # RuName
EBAY_MARKETPLACE = "EBAY_FR"
EBAY_CONTENT_LANGUAGE = "fr-FR"
EBAY_CURRENCY = "EUR"

SCOPES = [
    "https://api.ebay.com/oauth/api_scope",
    "https://api.ebay.com/oauth/api_scope/sell.inventory",
    "https://api.ebay.com/oauth/api_scope/sell.account",
    "https://api.ebay.com/oauth/api_scope/sell.fulfillment",
]

# Token persistence file
TOKEN_FILE = Path("/tmp/ebay_tokens.json")


class EbayTokenManager:
    """Handles OAuth2 authorization code flow + token refresh."""

    def __init__(self):
        self.access_token: Optional[str] = None
        self.refresh_token: Optional[str] = None
        self.expires_at: float = 0
        self._load_tokens()

    def _load_tokens(self):
        """Load tokens from file if available."""
        if TOKEN_FILE.exists():
            try:
                data = json.loads(TOKEN_FILE.read_text())
                self.access_token = data.get("access_token")
                self.refresh_token = data.get("refresh_token")
                self.expires_at = data.get("expires_at", 0)
            except Exception:
                pass

    def _save_tokens(self):
        """Persist tokens to file."""
        TOKEN_FILE.write_text(json.dumps({
            "access_token": self.access_token,
            "refresh_token": self.refresh_token,
            "expires_at": self.expires_at,
        }))

    @property
    def is_configured(self) -> bool:
        return bool(EBAY_CLIENT_ID and EBAY_CLIENT_SECRET and EBAY_REDIRECT_URI)

    @property
    def is_authenticated(self) -> bool:
        return bool(self.refresh_token)

    @property
    def _basic_auth(self) -> str:
        creds = f"{EBAY_CLIENT_ID}:{EBAY_CLIENT_SECRET}"
        return base64.b64encode(creds.encode()).decode()

    def get_auth_url(self) -> str:
        """Generate the eBay consent URL for the user to authorize."""
        from urllib.parse import quote
        scope = quote(" ".join(SCOPES))
        return (
            f"{EBAY_AUTH_URL}"
            f"?client_id={EBAY_CLIENT_ID}"
            f"&response_type=code"
            f"&redirect_uri={EBAY_REDIRECT_URI}"
            f"&scope={scope}"
        )

    async def exchange_code(self, auth_code: str) -> dict:
        """Exchange authorization code for access + refresh tokens."""
        async with httpx.AsyncClient(timeout=30) as client:
            resp = await client.post(
                EBAY_TOKEN_URL,
                headers={
                    "Content-Type": "application/x-www-form-urlencoded",
                    "Authorization": f"Basic {self._basic_auth}",
                },
                data={
                    "grant_type": "authorization_code",
                    "code": auth_code,
                    "redirect_uri": EBAY_REDIRECT_URI,
                },
            )
            resp.raise_for_status()
            data = resp.json()

        self.access_token = data["access_token"]
        self.refresh_token = data.get("refresh_token", self.refresh_token)
        self.expires_at = time.time() + data.get("expires_in", 7200) - 60
        self._save_tokens()
        return {"status": "authenticated", "expires_in": data.get("expires_in")}

    async def get_access_token(self) -> str:
        """Return a valid access token, refreshing if needed."""
        if self.access_token and time.time() < self.expires_at:
            return self.access_token

        if not self.refresh_token:
            raise Exception("Not authenticated with eBay. Please complete OAuth flow first.")

        async with httpx.AsyncClient(timeout=30) as client:
            resp = await client.post(
                EBAY_TOKEN_URL,
                headers={
                    "Content-Type": "application/x-www-form-urlencoded",
                    "Authorization": f"Basic {self._basic_auth}",
                },
                data={
                    "grant_type": "refresh_token",
                    "refresh_token": self.refresh_token,
                    "scope": " ".join(SCOPES),
                },
            )
            resp.raise_for_status()
            data = resp.json()

        self.access_token = data["access_token"]
        self.expires_at = time.time() + data.get("expires_in", 7200) - 60
        self._save_tokens()
        return self.access_token


class EbayInventoryClient:
    """eBay Sell Inventory API — create items, offers, and publish listings."""

    def __init__(self, token_manager: EbayTokenManager):
        self.tokens = token_manager
        self.base = f"{EBAY_API_BASE}/sell/inventory/v1"
        self.account_base = f"{EBAY_API_BASE}/sell/account/v1"
        self.taxonomy_base = f"{EBAY_API_BASE}/commerce/taxonomy/v1"

    async def _headers(self) -> dict:
        token = await self.tokens.get_access_token()
        return {
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
            "Content-Language": EBAY_CONTENT_LANGUAGE,
            "X-EBAY-C-MARKETPLACE-ID": EBAY_MARKETPLACE,
        }

    async def _request(self, method: str, url: str, json_body=None) -> dict:
        headers = await self._headers()
        async with httpx.AsyncClient(timeout=60) as client:
            resp = await client.request(
                method=method,
                url=url,
                headers=headers,
                json=json_body,
            )
            if resp.status_code == 204 or (resp.status_code == 200 and not resp.text.strip()):
                return {"status": "success", "code": resp.status_code}
            if resp.status_code >= 400:
                try:
                    err = resp.json()
                except Exception:
                    err = {"message": resp.text}
                return {"status": "error", "code": resp.status_code, "errors": err}
            return resp.json()

    # ── Inventory Items ───────────────────────────────────────────
    async def create_or_replace_item(self, sku: str, item_data: dict) -> dict:
        """PUT /inventory_item/{sku} — create or update inventory item."""
        headers = await self._headers()
        async with httpx.AsyncClient(timeout=60) as client:
            resp = await client.put(
                f"{self.base}/inventory_item/{sku}",
                headers=headers,
                json=item_data,
            )
            if resp.status_code in (200, 201, 204):
                return {"status": "success", "sku": sku}
            try:
                err = resp.json()
            except Exception:
                err = {"message": resp.text}
            return {"status": "error", "code": resp.status_code, "errors": err}

    async def get_item(self, sku: str) -> dict:
        return await self._request("GET", f"{self.base}/inventory_item/{sku}")

    async def get_items(self, limit: int = 100, offset: int = 0) -> dict:
        return await self._request("GET", f"{self.base}/inventory_item?limit={limit}&offset={offset}")

    # ── Offers ────────────────────────────────────────────────────
    async def create_offer(self, offer_data: dict) -> dict:
        return await self._request("POST", f"{self.base}/offer", json_body=offer_data)

    async def get_offers(self, sku: str) -> dict:
        return await self._request("GET", f"{self.base}/offer?sku={sku}")

    async def update_offer(self, offer_id: str, offer_data: dict) -> dict:
        return await self._request("PUT", f"{self.base}/offer/{offer_id}", json_body=offer_data)

    async def publish_offer(self, offer_id: str) -> dict:
        return await self._request("POST", f"{self.base}/offer/{offer_id}/publish")

    async def withdraw_offer(self, offer_id: str) -> dict:
        return await self._request("POST", f"{self.base}/offer/{offer_id}/withdraw")

    async def delete_offer(self, offer_id: str) -> dict:
        return await self._request("DELETE", f"{self.base}/offer/{offer_id}")

    # ── Account / Business Policies ───────────────────────────────
    async def get_fulfillment_policies(self) -> dict:
        return await self._request("GET", f"{self.account_base}/fulfillment_policy?marketplace_id={EBAY_MARKETPLACE}")

    async def get_payment_policies(self) -> dict:
        return await self._request("GET", f"{self.account_base}/payment_policy?marketplace_id={EBAY_MARKETPLACE}")

    async def get_return_policies(self) -> dict:
        return await self._request("GET", f"{self.account_base}/return_policy?marketplace_id={EBAY_MARKETPLACE}")

    async def get_all_policies(self) -> dict:
        """Fetch all 3 business policy types."""
        import asyncio
        ff, pp, rr = await asyncio.gather(
            self.get_fulfillment_policies(),
            self.get_payment_policies(),
            self.get_return_policies(),
        )
        return {
            "fulfillment": ff.get("fulfillmentPolicies", []),
            "payment": pp.get("paymentPolicies", []),
            "return": rr.get("returnPolicies", []),
        }

    async def create_fulfillment_policy(self, data: dict) -> dict:
        return await self._request("POST", f"{self.account_base}/fulfillment_policy", json_body=data)

    async def create_payment_policy(self, data: dict) -> dict:
        return await self._request("POST", f"{self.account_base}/payment_policy", json_body=data)

    async def create_return_policy(self, data: dict) -> dict:
        return await self._request("POST", f"{self.account_base}/return_policy", json_body=data)

    # ── Seller Programs (Opt-In) ─────────────────────────────────
    async def get_opted_in_programs(self) -> dict:
        return await self._request("GET", f"{self.account_base}/program/get_opted_in_programs")

    async def opt_in_to_program(self, program_type: str = "SELLING_POLICY_MANAGEMENT") -> dict:
        return await self._request("POST", f"{self.account_base}/program/opt_in", json_body={"programType": program_type})

    # ── Inventory Location ────────────────────────────────────────
    async def get_locations(self) -> dict:
        return await self._request("GET", f"{self.base}/location")

    async def create_location(self, location_key: str, location_data: dict) -> dict:
        headers = await self._headers()
        async with httpx.AsyncClient(timeout=60) as client:
            resp = await client.post(
                f"{self.base}/location/{location_key}",
                headers=headers,
                json=location_data,
            )
            if resp.status_code in (200, 201, 204):
                return {"status": "success", "key": location_key}
            try:
                err = resp.json()
            except Exception:
                err = {"message": resp.text}
            return {"status": "error", "code": resp.status_code, "errors": err}

    # ── Taxonomy / Category Aspects ──────────────────────────────
    async def get_category_aspects(self, category_id: str) -> dict:
        """Get required and recommended aspects for a category."""
        # First get the default category tree ID for EBAY_FR
        return await self._request(
            "GET",
            f"{self.taxonomy_base}/category_tree/71/get_item_aspects_for_category?category_id={category_id}"
        )

    # ── High-Level: Publish a Shopify product to eBay ─────────────
    async def publish_product(
        self,
        sku: str,
        title: str,
        description: str,
        price: float,
        quantity: int,
        condition: str,
        images: list[str],
        category_id: str,
        aspects: dict,
        fulfillment_policy_id: str,
        payment_policy_id: str,
        return_policy_id: str,
        location_key: str = "default",
    ) -> dict:
        """
        Full publish flow:
          1. Create/replace inventory item
          2. Create offer
          3. Publish offer
        """
        # Map condition
        condition_map = {
            "new": "NEW",
            "used": "USED_EXCELLENT",
            "a": "USED_EXCELLENT",
            "b": "USED_VERY_GOOD",
            "c": "USED_GOOD",
            "d": "USED_ACCEPTABLE",
        }
        ebay_condition = condition_map.get(condition.lower().strip(), "USED_GOOD") if condition else "USED_GOOD"

        # Step 1: Create inventory item
        item_data = {
            "availability": {
                "shipToLocationAvailability": {
                    "quantity": quantity,
                }
            },
            "condition": ebay_condition,
            "product": {
                "title": title[:80],  # eBay limit
                "description": description or title,
                "imageUrls": images[:12],  # eBay max 12
                "aspects": aspects,
            },
        }

        result = await self.create_or_replace_item(sku, item_data)
        if result.get("status") == "error":
            return {"step": "create_item", **result}

        # Step 2: Create offer
        offer_data = {
            "sku": sku,
            "marketplaceId": EBAY_MARKETPLACE,
            "format": "FIXED_PRICE",
            "availableQuantity": quantity,
            "categoryId": category_id,
            "listingDescription": description or title,
            "listingPolicies": {
                "fulfillmentPolicyId": fulfillment_policy_id,
                "paymentPolicyId": payment_policy_id,
                "returnPolicyId": return_policy_id,
            },
            "pricingSummary": {
                "price": {
                    "value": str(price),
                    "currency": EBAY_CURRENCY,
                }
            },
            "merchantLocationKey": location_key,
        }

        offer_result = await self.create_offer(offer_data)
        if offer_result.get("status") == "error":
            # Handle "offer already exists" — extract offerId and publish it
            errors = offer_result.get("errors", {})
            err_list = errors.get("errors", []) if isinstance(errors, dict) else []
            existing_offer_id = None
            for err in err_list:
                if err.get("errorId") == 25002:  # Offer already exists
                    for param in err.get("parameters", []):
                        if param.get("name") == "offerId":
                            existing_offer_id = param["value"]
                            break
            if existing_offer_id:
                # Update the existing offer with current data, then publish
                update_result = await self.update_offer(existing_offer_id, offer_data)
                if update_result.get("status") == "error":
                    return {"step": "update_existing_offer", "offer_id": existing_offer_id, **update_result}
                # Now publish
                pub_result = await self.publish_offer(existing_offer_id)
                if pub_result.get("status") == "error":
                    return {"step": "publish_existing_offer", "offer_id": existing_offer_id, **pub_result}
                listing_id = pub_result.get("listingId")
                return {
                    "status": "published",
                    "sku": sku,
                    "offer_id": existing_offer_id,
                    "listing_id": listing_id,
                    "ebay_url": f"https://www.ebay.fr/itm/{listing_id}" if listing_id else None,
                    "note": "updated and published existing offer",
                }
            return {"step": "create_offer", **offer_result}

        offer_id = offer_result.get("offerId")
        if not offer_id:
            return {"step": "create_offer", "status": "error", "errors": "No offerId returned"}

        # Step 3: Publish
        pub_result = await self.publish_offer(offer_id)
        if pub_result.get("status") == "error":
            return {"step": "publish_offer", "offer_id": offer_id, **pub_result}

        listing_id = pub_result.get("listingId")
        return {
            "status": "published",
            "sku": sku,
            "offer_id": offer_id,
            "listing_id": listing_id,
            "ebay_url": f"https://www.ebay.fr/itm/{listing_id}" if listing_id else None,
        }


# ── Singletons ────────────────────────────────────────────────────
ebay_tokens = EbayTokenManager()
ebay_client = EbayInventoryClient(ebay_tokens)
