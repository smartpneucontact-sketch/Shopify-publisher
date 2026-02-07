"""Studio (Print Queue) proxy — fetches image counts per SKU from SmartPneu Studio."""

from fastapi import APIRouter, HTTPException
import httpx
import os

router = APIRouter()

STUDIO_URL = os.getenv("STUDIO_URL", "https://product-picture-queue-production.up.railway.app")


@router.get("/sku-counts")
async def get_sku_image_counts():
    """Fetch all images from studio and return {sku: {total, pending, completed, ...}}."""
    try:
        async with httpx.AsyncClient(timeout=30) as client:
            resp = await client.get(f"{STUDIO_URL}/api/images")
            resp.raise_for_status()
            images = resp.json()

        # Group by SKU
        counts = {}
        for img in images:
            sku = img.get("sku")
            if not sku:
                continue
            if sku not in counts:
                counts[sku] = {"total": 0, "pending": 0, "processing": 0, "completed": 0, "failed": 0, "draft": 0}
            counts[sku]["total"] += 1
            status = img.get("status", "pending")
            if status in counts[sku]:
                counts[sku][status] += 1

        return {"sku_counts": counts, "total_images": len(images)}

    except httpx.HTTPError as e:
        raise HTTPException(status_code=502, detail=f"Studio API error: {str(e)}")
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/images")
async def get_studio_images(sku: str | None = None, status: str | None = None):
    """Proxy to studio /api/images with optional filters."""
    try:
        params = {}
        if status:
            params["status"] = status

        async with httpx.AsyncClient(timeout=30) as client:
            resp = await client.get(f"{STUDIO_URL}/api/images", params=params)
            resp.raise_for_status()
            images = resp.json()

        # Filter by SKU if specified
        if sku:
            images = [img for img in images if img.get("sku") == sku]

        return {"images": images, "count": len(images)}

    except httpx.HTTPError as e:
        raise HTTPException(status_code=502, detail=f"Studio API error: {str(e)}")
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
