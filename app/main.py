"""
Shopify Headless App — FastAPI
Works on localhost and Railway.
"""

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from contextlib import asynccontextmanager

from app.config import settings
from app.routes import products, orders, customers, inventory, health


@asynccontextmanager
async def lifespan(app: FastAPI):
    print(f"🚀 Starting Shopify App ({settings.ENVIRONMENT})")
    print(f"   Shop: {settings.SHOPIFY_STORE_DOMAIN}")
    yield
    print("👋 Shutting down...")


app = FastAPI(
    title="Shopify Headless App",
    description="Headless Shopify storefront powered by the Admin API",
    version="1.0.0",
    lifespan=lifespan,
)

# ── CORS ─────────────────────────────────────────────────────────────
allowed_origins = (
    settings.ALLOWED_ORIGINS.split(",") if settings.ALLOWED_ORIGINS else ["*"]
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=allowed_origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# ── Routes ───────────────────────────────────────────────────────────
app.include_router(health.router, tags=["Health"])
app.include_router(products.router, prefix="/api/products", tags=["Products"])
app.include_router(orders.router, prefix="/api/orders", tags=["Orders"])
app.include_router(customers.router, prefix="/api/customers", tags=["Customers"])
app.include_router(inventory.router, prefix="/api/inventory", tags=["Inventory"])


@app.get("/")
async def root():
    return {
        "app": "Shopify Headless App",
        "environment": settings.ENVIRONMENT,
        "docs": "/docs",
    }
