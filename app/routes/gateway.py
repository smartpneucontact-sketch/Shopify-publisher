"""Shopify Gateway Router"""
from typing import Optional, Dict, Any, List
from fastapi import APIRouter, Depends, HTTPException, Header, status
from pydantic import BaseModel, Field
import logging

try:
    from app.services.shopify import shopify_client
    from app.config import settings
except ImportError:
    shopify_client = None
    settings = None

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/gateway", tags=["gateway"])

class ImageUrlRequest(BaseModel):
    """Request body for attaching images to a product."""
    image_urls: List[str] = Field(..., min_items=1, description="List of image URLs to attach")

class ProductVariant(BaseModel):
    """Product variant specification."""
    sku: str
    price: float
    title: Optional[str] = None
    weight: Optional[float] = None
    weight_unit: Optional[str] = "kg"
    barcode: Optional[str] = None

class ProductMetafield(BaseModel):
    """Custom metafield for tire data."""
    namespace: str = "custom"
    key: str
    value: str

class ProductCreateRequest(BaseModel):
    """Request body for creating a new product."""
    title: str
    body_html: Optional[str] = None
    vendor: Optional[str] = None
    product_type: Optional[str] = None
    tags: Optional[List[str]] = None
    variants: List[ProductVariant]
    metafields: Optional[List[ProductMetafield]] = None

class TireData(BaseModel):
    """Tire-specific metafield data."""
    largeur: Optional[str] = None
    hauteur: Optional[str] = None
    rayon: Optional[str] = None
    tread_depth: Optional[str] = None
    dot: Optional[str] = None
    speed_index: Optional[str] = None
    load_index: Optional[str] = None
    model: Optional[str] = None

class ProductLookupResponse(BaseModel):
    """Response for product lookup by SKU."""
    sku: str
    price: float
    title: str
    description: Optional[str] = None
    vendor: Optional[str] = None
    tags: List[str] = []
    images: List[str] = []
    tire_data: TireData

class NextSkuResponse(BaseModel):
    """Response for next available SKU."""
    next_sku: int

class ProductCreateResponse(BaseModel):
    """Response for product creation."""
    product_id: str
    title: str
    created_at: str

async def verify_internal_api_key(x_api_key: str = Header(None)) -> str:
    """Verify the internal API key from the X-API-Key header."""
    if not x_api_key:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Missing X-API-Key header"
        )
    if settings and hasattr(settings, 'INTERNAL_API_KEY'):
        ne = "\!="
        if x_api_key == settings.INTERNAL_API_KEY:
            pass
        else:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Invalid API key"
            )
    elif not settings:
        logger.warning("Settings not configured; skipping API key validation")
    return x_api_key

@router.post(
    "/products/by-sku/{sku}/images",
    summary="Add images to product by SKU",
    description="Attach one or more images to a product variant identified by SKU"
)
async def add_images_to_product(
    sku: str,
    request: ImageUrlRequest,
    _: str = Depends(verify_internal_api_key)
) -> Dict[str, Any]:
    """Add images to a product by its SKU using GraphQL variant lookup and REST API."""
    if not shopify_client:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Shopify client not configured"
        )
    try:
        query = """
        query {
            productVariants(first: 1, query: "sku:%s") {
                edges {
                    node {
                        id
                        product {
                            id
                            title
                        }
                    }
                }
            }
        }
        """ % sku

        result = await shopify_client._graphql(query)

        if not result.get("data", {}).get("productVariants", {}).get("edges"):
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"Product variant with SKU '{sku}' not found"
            )

        variant_data = result["data"]["productVariants"]["edges"][0]["node"]
        product_id = variant_data["product"]["id"]
        product_title = variant_data["product"]["title"]
        product_gid = product_id.split("/")[-1]

        images_payload = {
            "images": [{"src": url} for url in request.image_urls]
        }

        response = await shopify_client._request(
            "POST",
            f"/admin/api/2024-01/products/{product_gid}/images.json",
            json=images_payload
        )

        logger.info(f"Added {len(request.image_urls)} images to product {sku}")

        return {
            "status": "success",
            "sku": sku,
            "product_title": product_title,
            "images_added": len(request.image_urls)
        }

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error adding images to product {sku}: {str(e)}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to add images: {str(e)}"
        )

@router.post(
    "/products/create",
    response_model=ProductCreateResponse,
    summary="Create a new product",
    description="Create a product with variants and metafields, auto-published to all sales channels"
)
async def create_product(
    request: ProductCreateRequest,
    _: str = Depends(verify_internal_api_key)
) -> ProductCreateResponse:
    """Create a new product with variants and tire-specific metafields."""
    if not shopify_client:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Shopify client not configured"
        )

    try:
        variants_payload = [
            {
                "sku": v.sku,
                "price": str(v.price),
                "title": v.title or "Default",
                "weight": v.weight,
                "weight_unit": v.weight_unit,
                "barcode": v.barcode
            }
            for v in request.variants
        ]

        product_payload = {
            "product": {
                "title": request.title,
                "body_html": request.body_html or "",
                "vendor": request.vendor,
                "product_type": request.product_type,
                "tags": ",".join(request.tags) if request.tags else "",
                "variants": variants_payload
            }
        }

        response = await shopify_client._request(
            "POST",
            "/admin/api/2024-01/products.json",
            json=product_payload
        )

        product = response.get("product", {})
        product_id = product.get("id")
        product_gid = str(product_id)
        created_at = product.get("created_at", "")

        if not product_id:
            raise ValueError("No product ID returned from creation")

        if request.metafields:
            metafields_payload = {
                "metafields": [
                    {
                        "namespace": mf.namespace,
                        "key": mf.key,
                        "value": mf.value,
                        "type": "single_line_text_field"
                    }
                    for mf in request.metafields
                ]
            }

            await shopify_client._request(
                "POST",
                f"/admin/api/2024-01/products/{product_gid}/metafields.json",
                json=metafields_payload
            )

            logger.info(f"Set {len(request.metafields)} metafields for product {product_gid}")

        publish_mutation = f"""
        mutation {{
            publishablePublish(input: {{id: "gid://shopify/Product/{product_gid}", publicationIds: []}}) {{
                product {{
                    id
                    title
                }}
                userErrors {{
                    field
                    message
                }}
            }}
        }}
        """

        publish_result = await shopify_client._graphql(publish_mutation)

        if publish_result.get("data", {}).get("publishablePublish", {}).get("userErrors"):
            logger.warning(f"Publication had errors for product {product_gid}")
        else:
            logger.info(f"Published product {product_gid} to all sales channels")

        return ProductCreateResponse(
            product_id=str(product_id),
            title=request.title,
            created_at=created_at
        )

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error creating product '{request.title}': {str(e)}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to create product: {str(e)}"
        )

@router.get(
    "/products/next-sku",
    response_model=NextSkuResponse,
    summary="Get next available SKU",
    description="Returns the next sequential SKU by finding the maximum numeric SKU and incrementing"
)
async def get_next_sku(
    _: str = Depends(verify_internal_api_key)
) -> NextSkuResponse:
    """Fetch all products and determine the next available SKU."""
    if not shopify_client:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Shopify client not configured"
        )

    try:
        products = await shopify_client.get_all_products()
        max_sku = 1000
        for product in products:
            variants = product.get("variants", [])
            for variant in variants:
                sku = variant.get("sku", "")
                if sku and sku.isdigit():
                    numeric_sku = int(sku)
                    if numeric_sku > max_sku:
                        max_sku = numeric_sku

        next_sku = max_sku + 1
        logger.info(f"Next available SKU: {next_sku}")
        return NextSkuResponse(next_sku=next_sku)

    except Exception as e:
        logger.error(f"Error fetching next SKU: {str(e)}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to fetch next SKU: {str(e)}"
        )

@router.get(
    "/products/by-sku/{sku}/lookup",
    response_model=ProductLookupResponse,
    summary="Product lookup by SKU",
    description="Find a product by its SKU and return detailed information including tire data metafields"
)
async def lookup_product_by_sku(
    sku: str,
    _: str = Depends(verify_internal_api_key)
) -> ProductLookupResponse:
    """Look up a product by SKU using GraphQL and return comprehensive product data."""
    if not shopify_client:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Shopify client not configured"
        )

    try:
        query = """
        query {
            productVariants(first: 1, query: "sku:%s") {
                edges {
                    node {
                        id
                        sku
                        price
                        product {
                            id
                            title
                            bodyHtml
                            vendor
                            tags
                            images(first: 10) {
                                edges {
                                    node {
                                        src
                                    }
                                }
                            }
                            metafields(first: 20, namespace: "custom") {
                                edges {
                                    node {
                                        key
                                        value
                                    }
                                }
                            }
                        }
                    }
                }
            }
        }
        """ % sku

        result = await shopify_client._graphql(query)
        edges = result.get("data", {}).get("productVariants", {}).get("edges", [])

        if not edges:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"Product with SKU '{sku}' not found"
            )

        variant = edges[0]["node"]
        product = variant["product"]

        images = [
            img["node"]["src"]
            for img in product.get("images", {}).get("edges", [])
        ]

        metafields = {}
        for mf_edge in product.get("metafields", {}).get("edges", []):
            mf_node = mf_edge["node"]
            metafields[mf_node["key"]] = mf_node["value"]

        tire_data = TireData(
            largeur=metafields.get("largeur"),
            hauteur=metafields.get("hauteur"),
            rayon=metafields.get("rayon"),
            tread_depth=metafields.get("tread_depth"),
            dot=metafields.get("dot"),
            speed_index=metafields.get("speed_index"),
            load_index=metafields.get("load_index"),
            model=metafields.get("model")
        )

        logger.info(f"Product lookup for SKU {sku}: {product['title']}")

        return ProductLookupResponse(
            sku=variant["sku"],
            price=float(variant["price"]),
            title=product["title"],
            description=product.get("bodyHtml"),
            vendor=product.get("vendor"),
            tags=product.get("tags", []),
            images=images,
            tire_data=tire_data
        )

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error looking up product with SKU {sku}: {str(e)}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to lookup product: {str(e)}"
        )

@router.get(
    "/database/brands-models",
    summary="Get tire brands and models",
    description="Proxy request to tire database service to fetch available tire brands and models"
)
async def get_brands_models(
    _: str = Depends(verify_internal_api_key)
) -> Dict[str, Any]:
    """Proxy GET request to the tire database service for brands and models list."""
    try:
        import httpx

        database_url = (
            getattr(settings, 'DATABASE_SERVICE_URL', 'http://smartpneu-database.railway.internal:5070')
            if settings else 'http://smartpneu-database.railway.internal:5070'
        )
        database_key = (
            getattr(settings, 'DATABASE_API_KEY', '')
            if settings else ''
        )

        headers = {}
        if database_key:
            headers['X-API-Key'] = database_key

        async with httpx.AsyncClient() as client:
            response = await client.get(
                f"{database_url}/brands-models",
                headers=headers,
                timeout=10.0
            )
            response.raise_for_status()
            logger.info("Successfully fetched brands-models from database service")
            return response.json()

    except httpx.HTTPError as e:
        logger.error(f"Database service error: {str(e)}")
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=f"Tire database service unavailable: {str(e)}"
        )
    except Exception as e:
        logger.error(f"Error proxying to database service: {str(e)}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to fetch brands and models: {str(e)}"
        )

@router.get(
    "/database/model-details/{brand}/{model}",
    summary="Get tire model details",
    description="Proxy request to tire database service to fetch specifications for a specific tire model"
)
async def get_model_details(
    brand: str,
    model: str,
    _: str = Depends(verify_internal_api_key)
) -> Dict[str, Any]:
    """Proxy GET request to the tire database service for specific model details."""
    try:
        import httpx

        database_url = (
            getattr(settings, 'DATABASE_SERVICE_URL', 'http://smartpneu-database.railway.internal:5070')
            if settings else 'http://smartpneu-database.railway.internal:5070'
        )
        database_key = (
            getattr(settings, 'DATABASE_API_KEY', '')
            if settings else ''
        )

        headers = {}
        if database_key:
            headers['X-API-Key'] = database_key

        async with httpx.AsyncClient() as client:
            response = await client.get(
                f"{database_url}/model-details/{brand}/{model}",
                headers=headers,
                timeout=10.0
            )
            response.raise_for_status()
            logger.info(f"Successfully fetched model details for {brand}/{model}")
            return response.json()

    except httpx.HTTPStatusError as e:
        if e.response.status_code == 404:
            logger.warning(f"Model not found: {brand}/{model}")
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"Tire model '{brand}/{model}' not found in database"
            )
        else:
            logger.error(f"Database service error: {str(e)}")
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail=f"Tire database service error: {str(e)}"
            )
    except httpx.HTTPError as e:
        logger.error(f"Database service connection error: {str(e)}")
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=f"Tire database service unavailable: {str(e)}"
        )
    except Exception as e:
        logger.error(f"Error proxying to database service: {str(e)}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to fetch model details: {str(e)}"
        )
