"""
Shopify Admin API client.
Centralizes all HTTP requests to the Shopify REST Admin API.
"""

import httpx
from typing import Any, Optional
from app.config import settings


class ShopifyAdminClient:
    """Async HTTP client for the Shopify Admin REST API."""

    def __init__(self):
        self.base_url = settings.shopify_base_url
        self.headers = settings.shopify_headers

    async def _request(
        self,
        method: str,
        endpoint: str,
        params: Optional[dict] = None,
        json_body: Optional[dict] = None,
    ) -> dict[str, Any]:
        url = f"{self.base_url}/{endpoint}"
        async with httpx.AsyncClient(timeout=120) as client:
            response = await client.request(
                method=method,
                url=url,
                headers=self.headers,
                params=params,
                json=json_body,
            )
            response.raise_for_status()
            return response.json()

    # ── Products ─────────────────────────────────────────────────────
    async def get_products(self, limit: int = 50, **kwargs) -> dict:
        params = {"limit": limit, **kwargs}
        return await self._request("GET", "products.json", params=params)

    async def get_product(self, product_id: int) -> dict:
        return await self._request("GET", f"products/{product_id}.json")

    async def get_product_count(self) -> dict:
        return await self._request("GET", "products/count.json")

    # ── Orders ───────────────────────────────────────────────────────
    async def get_orders(self, limit: int = 50, status: str = "any", **kwargs) -> dict:
        params = {"limit": limit, "status": status, **kwargs}
        return await self._request("GET", "orders.json", params=params)

    async def get_order(self, order_id: int) -> dict:
        return await self._request("GET", f"orders/{order_id}.json")

    async def get_order_count(self, status: str = "any") -> dict:
        return await self._request(
            "GET", "orders/count.json", params={"status": status}
        )

    async def create_order(self, order_data: dict) -> dict:
        return await self._request(
            "POST", "orders.json", json_body={"order": order_data}
        )

    async def close_order(self, order_id: int) -> dict:
        return await self._request("POST", f"orders/{order_id}/close.json")

    # ── Customers ────────────────────────────────────────────────────
    async def get_customers(self, limit: int = 50, **kwargs) -> dict:
        params = {"limit": limit, **kwargs}
        return await self._request("GET", "customers.json", params=params)

    async def get_customer(self, customer_id: int) -> dict:
        return await self._request("GET", f"customers/{customer_id}.json")

    async def search_customers(self, query: str) -> dict:
        return await self._request(
            "GET", "customers/search.json", params={"query": query}
        )

    async def get_customer_count(self) -> dict:
        return await self._request("GET", "customers/count.json")

    # ── Inventory ────────────────────────────────────────────────────
    async def get_inventory_levels(self, location_ids: str, **kwargs) -> dict:
        params = {"location_ids": location_ids, **kwargs}
        return await self._request("GET", "inventory_levels.json", params=params)

    async def adjust_inventory(
        self, inventory_item_id: int, location_id: int, adjustment: int
    ) -> dict:
        body = {
            "location_id": location_id,
            "inventory_item_id": inventory_item_id,
            "available_adjustment": adjustment,
        }
        return await self._request(
            "POST", "inventory_levels/adjust.json", json_body=body
        )

    async def get_locations(self) -> dict:
        return await self._request("GET", "locations.json")

    # ── Metafields ───────────────────────────────────────────────────
    async def get_product_metafields(self, product_id: int) -> dict:
        return await self._request("GET", f"products/{product_id}/metafields.json")

    async def get_all_products(self) -> list:
        """Fetch ALL products using since_id pagination (250 per page)."""
        all_products = []
        since_id = 0
        async with httpx.AsyncClient(timeout=120, headers=self.headers) as client:
            while True:
                resp = await client.get(
                    f"{self.base_url}/products.json",
                    params={"limit": 250, "since_id": since_id}
                )
                resp.raise_for_status()
                batch = resp.json().get("products", [])
                if not batch:
                    break
                all_products.extend(batch)
                since_id = batch[-1]["id"]
                if len(batch) < 250:
                    break
        return all_products

    async def get_products_with_metafields(self) -> list:
        """Fetch ALL products then batch-fetch all their metafields."""
        import asyncio
        products = await self.get_all_products()

        BATCH_SIZE = 20

        async with httpx.AsyncClient(timeout=120, headers=self.headers) as client:
            async def enrich(product):
                try:
                    resp = await client.get(
                        f"{self.base_url}/products/{product['id']}/metafields.json"
                    )
                    resp.raise_for_status()
                    product["metafields"] = resp.json().get("metafields", [])
                except Exception:
                    product["metafields"] = []
                return product

            for i in range(0, len(products), BATCH_SIZE):
                batch = products[i:i + BATCH_SIZE]
                await asyncio.gather(*[enrich(p) for p in batch])

        return products


# Singleton
shopify_client = ShopifyAdminClient()
