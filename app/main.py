"""
Shopify Headless App — FastAPI
Works on localhost and Railway.
"""

from pathlib import Path
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles
from contextlib import asynccontextmanager

from app.config import settings
from app.routes import products, orders, customers, inventory, health, studio, ebay
from app.routes import sales, events, gateway, notifications
from app.services.sales_db import sales_db

STATIC_DIR = Path(__file__).parent / "static"


@asynccontextmanager
async def lifespan(app: FastAPI):
    print(f"🚀 Starting SmartPneu Central ({settings.ENVIRONMENT})")
    print(f"   Shop: {settings.SHOPIFY_STORE_DOMAIN}")
    print(f"   Dashboard: /dashboard")
    print(f"   Sales: /sales")
    print(f"   API Docs: /docs")
    # Initialize sales database
    await sales_db.init()
    print(f"   ✅ Sales DB initialized")
    yield
    print("👋 Shutting down...")


app = FastAPI(
    title="SmartPneu Central",
    description="Central gateway for SmartPneu — Shopify, eBay, sales tracking, and service coordination",
    version="2.0.0",
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
app.include_router(studio.router, prefix="/api/studio", tags=["Studio"])
app.include_router(ebay.router, prefix="/api/ebay", tags=["eBay"])
app.include_router(sales.router, prefix="/api/sales", tags=["Sales"])
app.include_router(events.router, prefix="/api/events", tags=["Events"])
app.include_router(gateway.router, prefix="/api/gateway", tags=["Gateway"])
app.include_router(notifications.router, prefix="/api", tags=["Notifications"])


@app.get("/")
async def root():
    return {
        "app": "SmartPneu Central",
        "environment": settings.ENVIRONMENT,
        "dashboard": "/dashboard",
        "sales": "/sales",
        "docs": "/docs",
    }


@app.get("/dashboard", response_class=HTMLResponse)
async def dashboard():
    """Serve the admin dashboard."""
    html_file = STATIC_DIR / "dashboard.html"
    return HTMLResponse(content=html_file.read_text(), status_code=200)


@app.get("/sales", response_class=HTMLResponse)
async def sales_dashboard():
    """Serve the revenue tracking dashboard."""
    html_file = STATIC_DIR / "sales.html"
    return HTMLResponse(content=html_file.read_text(), status_code=200)
