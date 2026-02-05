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
    def is_production(self) -> bool:
        return self.ENVIRONMENT == "production"


@lru_cache()
def get_settings() -> Settings:
    return Settings()


# Singleton used throughout the app
settings = get_settings()
