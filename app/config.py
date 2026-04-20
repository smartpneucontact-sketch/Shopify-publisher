"""
Configuration — auto-detects local vs Railway environment.

Local:  reads from .env file
Railway: reads from environment variables set in the dashboard
"""

from pydantic_settings import BaseSettings
from functools import lru_cache
import os


class Settings(BaseSettings):
    # ── Environment ──────────────────────────────────────────────────
    ENVIRONMENT: str = "development"  # "development" | "production"
    PORT: int = 8000
    HOST: str = "0.0.0.0"

    # ── Shopify ──────────────────────────────────────────────────────
    SHOPIFY_STORE_DOMAIN: str = ""        # e.g. "my-store.myshopify.com"
    SHOPIFY_ADMIN_API_TOKEN: str = ""     # Admin API access token
    SHOPIFY_API_VERSION: str = "2024-10"  # API version
    SHOPIFY_API_SECRET: str = ""          # For webhook verification (optional)

    # ── CORS ─────────────────────────────────────────────────────────
    ALLOWED_ORIGINS: str = "http://localhost:3000,http://localhost:5173"

    # ── Internal API Auth (service-to-service) ───────────────────────
    INTERNAL_API_KEY: str = ""

    # ── SmartPneu Database Service ───────────────────────────────────
    DATABASE_SERVICE_URL: str = "http://smartpneu-database.railway.internal:5070"
    DATABASE_API_KEY: str = ""

    # ── Studio Service (Product_picture_queue) ───────────────────────
    STUDIO_SERVICE_URL: str = "http://product-picture-queue.railway.internal:5000"

    # ── Product Manager Service ──────────────────────────────────────
    PRODUCT_MANAGER_SERVICE_URL: str = "http://smartpneu-product-manager.railway.internal:5000"

    # ── Anthropic (Claude API for invoice reading) ────────────────
    ANTHROPIC_API_KEY: str = ""

    # ── Data Directory (persistent volume on Railway) ──────────────
    DATA_DIR: str = "/data"

    # ── Sales Database ───────────────────────────────────────────────
    SALES_DB_PATH: str = "/data/sales.db"

    class Config:
        env_file = ".env"
        env_file_encoding = "utf-8"
        extra = "ignore"

    @property
    def shopify_base_url(self) -> str:
        return (
            f"https://{self.SHOPIFY_STORE_DOMAIN}/admin/api/"
            f"{self.SHOPIFY_API_VERSION}"
        )

    @property
    def shopify_headers(self) -> dict:
        return {
            "X-Shopify-Access-Token": self.SHOPIFY_ADMIN_API_TOKEN,
            "Content-Type": "application/json",
        }

    @property
    def shopify_graphql_url(self) -> str:
        return (
            f"https://{self.SHOPIFY_STORE_DOMAIN}/admin/api/"
            f"{self.SHOPIFY_API_VERSION}/graphql.json"
        )

    @property
    def is_production(self) -> bool:
        return self.ENVIRONMENT == "production"


@lru_cache()
def get_settings() -> Settings:
    return Settings()


# Singleton used throughout the app
settings = get_settings()
