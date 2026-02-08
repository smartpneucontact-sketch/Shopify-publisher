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
        self.graphql_url = settings.shopify_graphql_url

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

    async def _graphql(self, query: str, variables: Optional[dict] = None) -> dict:
        """Execute a GraphQL query against the Shopify Admin API."""
        body = {"query": query}
        if variables:
            body["variables"] = variables
        async with httpx.AsyncClient(timeout=120) as client:
            response = await client.post(
                self.graphql_url,
                headers=self.headers,
                json=body,
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

    async def set_product_metafield(self, product_id: int, namespace: str, key: str, value: str, mf_type: str = "single_line_text_field") -> dict:
        """Create or update a product metafield."""
        return await self._request("POST", f"products/{product_id}/metafields.json", json_body={
            "metafield": {
                "namespace": namespace,
                "key": key,
                "value": value,
                "type": mf_type,
            }
        })

    async def set_product_metafields_bulk(self, product_id: int, metafields: list[dict]) -> list:
        """Set multiple metafields on a product. Each dict: {key, value, type?}"""
        results = []
        for mf in metafields:
            r = await self.set_product_metafield(
                product_id,
                namespace=mf.get("namespace", "ebay"),
                key=mf["key"],
                value=str(mf["value"]),
                mf_type=mf.get("type", "single_line_text_field"),
            )
            results.append(r)
        return results

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

    async def get_product_categories(self) -> dict:
        """Fetch product ID → category name mapping via GraphQL.
        Returns dict like { "gid://shopify/Product/123": "Automotive Tires" }
        """
        categories = {}
        cursor = None
        while True:
            after = f', after: "{cursor}"' if cursor else ""
            query = f"""
            {{
              products(first: 250{after}) {{
                edges {{
                  cursor
                  node {{
                    id
                    productCategory {{
                      productTaxonomyNode {{
                        fullName
                        name
                      }}
                    }}
                  }}
                }}
                pageInfo {{
                  hasNextPage
                }}
              }}
            }}
            """
            data = await self._graphql(query)
            products = data.get("data", {}).get("products", {})
            edges = products.get("edges", [])
            for edge in edges:
                node = edge["node"]
                cat = node.get("productCategory")
                if cat and cat.get("productTaxonomyNode"):
                    tax = cat["productTaxonomyNode"]
                    categories[node["id"]] = {
                        "name": tax.get("name", ""),
                        "fullName": tax.get("fullName", ""),
                    }
                cursor = edge.get("cursor")
            if not products.get("pageInfo", {}).get("hasNextPage", False):
                break
        return categories

    async def get_products_with_metafields(self) -> list:
        """Fetch ALL products + metafields + categories via GraphQL in ~1-2 calls."""
        all_products = []
        cursor = None

        while True:
            after = f', after: "{cursor}"' if cursor else ""
            query = f"""
            {{
              products(first: 50{after}) {{
                edges {{
                  cursor
                  node {{
                    id
                    title
                    handle
                    status
                    publishedAt
                    productType
                    vendor
                    bodyHtml
                    tags
                    createdAt
                    updatedAt
                    collections(first: 10) {{
                      edges {{
                        node {{
                          title
                          handle
                        }}
                      }}
                    }}
                    productCategory {{
                      productTaxonomyNode {{
                        fullName
                        name
                      }}
                    }}
                    images(first: 20) {{
                      edges {{
                        node {{
                          id
                          src
                          altText
                        }}
                      }}
                    }}
                    variants(first: 50) {{
                      edges {{
                        node {{
                          id
                          title
                          sku
                          price
                          inventoryQuantity
                        }}
                      }}
                    }}
                    metafields(first: 30) {{
                      edges {{
                        node {{
                          namespace
                          key
                          value
                          type
                        }}
                      }}
                    }}
                  }}
                }}
                pageInfo {{
                  hasNextPage
                }}
              }}
            }}
            """
            data = await self._graphql(query)
            errors = data.get("errors")
            if errors:
                print(f"⚠️ GraphQL errors: {errors}")

            products = data.get("data", {}).get("products", {})
            edges = products.get("edges", [])

            for edge in edges:
                node = edge["node"]
                # Convert GraphQL format to REST-like format for dashboard compatibility
                gid = node["id"]
                numeric_id = int(gid.split("/")[-1]) if "/" in gid else gid

                # Category
                cat = node.get("productCategory")
                tax_name = ""
                tax_full = ""
                if cat and cat.get("productTaxonomyNode"):
                    tax_name = cat["productTaxonomyNode"].get("name", "")
                    tax_full = cat["productTaxonomyNode"].get("fullName", "")

                product = {
                    "id": numeric_id,
                    "title": node.get("title", ""),
                    "handle": node.get("handle", ""),
                    "status": node.get("status", "").lower(),
                    "published_at": node.get("publishedAt"),
                    "product_type": node.get("productType", ""),
                    "vendor": node.get("vendor", ""),
                    "body_html": node.get("bodyHtml", ""),
                    "tags": ", ".join(node.get("tags", [])) if isinstance(node.get("tags"), list) else node.get("tags", ""),
                    "collections": [e["node"]["title"] for e in node.get("collections", {}).get("edges", [])],
                    "created_at": node.get("createdAt", ""),
                    "updated_at": node.get("updatedAt", ""),
                    "taxonomy_category": tax_name,
                    "taxonomy_full": tax_full,
                    "images": [
                        {"id": img["node"]["id"], "src": img["node"]["src"], "alt": img["node"].get("altText")}
                        for img in node.get("images", {}).get("edges", [])
                    ],
                    "variants": [
                        {
                            "id": v["node"]["id"],
                            "title": v["node"].get("title", ""),
                            "sku": v["node"].get("sku", ""),
                            "price": v["node"].get("price", ""),
                            "inventory_quantity": v["node"].get("inventoryQuantity", 0),
                        }
                        for v in node.get("variants", {}).get("edges", [])
                    ],
                    "metafields": [
                        {
                            "namespace": mf["node"]["namespace"],
                            "key": mf["node"]["key"],
                            "value": mf["node"]["value"],
                            "type": mf["node"].get("type", ""),
                        }
                        for mf in node.get("metafields", {}).get("edges", [])
                    ],
                }
                all_products.append(product)
                cursor = edge.get("cursor")

            print(f"📦 Fetched {len(all_products)} products so far...")

            if not products.get("pageInfo", {}).get("hasNextPage", False):
                break

        print(f"✅ Done: {len(all_products)} products with metafields loaded via GraphQL")
        return all_products


# Singleton
shopify_client = ShopifyAdminClient()
