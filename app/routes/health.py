"""Health check endpoint — used by Railway for deployment health."""

from fastapi import APIRouter
from app.config import settings

router = APIRouter()


@router.get("/health")
async def health_check():
    return {
        "status": "healthy",
        "environment": settings.ENVIRONMENT,
        "shop": settings.SHOPIFY_STORE_DOMAIN,
    }
