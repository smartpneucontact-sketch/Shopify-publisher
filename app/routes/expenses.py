"""
Expenses API routes — record, list, delete expenses + Claude invoice reading.
"""

import base64
import os
import uuid
from datetime import datetime
from typing import Optional

import httpx
from fastapi import APIRouter, File, UploadFile, HTTPException, Form
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field

from app.config import settings
from app.services.sales_db import sales_db

router = APIRouter()

DATA_DIR = settings.DATA_DIR
INVOICES_DIR = os.path.join(DATA_DIR, "invoices")


# ── Models ──────────────────────────────────────────────────────────

class ExpenseRequest(BaseModel):
    expense_date: str = Field(..., description="Date of the expense (YYYY-MM-DD)")
    vendor: Optional[str] = Field(default="", description="Vendor / supplier name")
    description: Optional[str] = Field(default="", description="Expense description")
    total_amount: float = Field(..., gt=0, description="Total amount")
    currency: str = Field(default="EUR")
    image_path: Optional[str] = Field(default="", description="Path to invoice image")
    notes: Optional[str] = Field(default="", description="Additional notes")


class InvoiceExtraction(BaseModel):
    vendor: str = ""
    date: str = ""
    total: float = 0.0


# ── Invoice reading with Claude ─────────────────────────────────────

@router.post("/parse-invoice")
async def parse_invoice(file: UploadFile = File(...)):
    """Upload an invoice image and extract vendor, date, total using Claude API."""
    if not settings.ANTHROPIC_API_KEY:
        raise HTTPException(status_code=500, detail="ANTHROPIC_API_KEY not configured")

    contents = await file.read()
    if len(contents) > 20 * 1024 * 1024:
        raise HTTPException(status_code=400, detail="File too large (max 20MB)")

    # Determine media type
    ct = file.content_type or "image/jpeg"
    if ct not in ("image/jpeg", "image/png", "image/gif", "image/webp", "application/pdf"):
        raise HTTPException(status_code=400, detail=f"Unsupported file type: {ct}")

    b64 = base64.b64encode(contents).decode("utf-8")

    # Save the image for later reference
    os.makedirs(INVOICES_DIR, exist_ok=True)
    ext = ct.split("/")[-1].replace("jpeg", "jpg")
    filename = f"{datetime.utcnow().strftime('%Y%m%d_%H%M%S')}_{uuid.uuid4().hex[:6]}.{ext}"
    filepath = os.path.join(INVOICES_DIR, filename)
    with open(filepath, "wb") as f:
        f.write(contents)

    # Build content based on type
    if ct == "application/pdf":
        source_block = {
            "type": "document",
            "source": {"type": "base64", "media_type": ct, "data": b64},
        }
    else:
        source_block = {
            "type": "image",
            "source": {"type": "base64", "media_type": ct, "data": b64},
        }

    # Call Claude API
    try:
        async with httpx.AsyncClient(timeout=60) as client:
            resp = await client.post(
                "https://api.anthropic.com/v1/messages",
                headers={
                    "x-api-key": settings.ANTHROPIC_API_KEY,
                    "anthropic-version": "2023-06-01",
                    "content-type": "application/json",
                },
                json={
                    "model": "claude-sonnet-4-20250514",
                    "max_tokens": 300,
                    "messages": [
                        {
                            "role": "user",
                            "content": [
                                source_block,
                                {
                                    "type": "text",
                                    "text": (
                                        "Extract the following from this invoice:\n"
                                        "1. Vendor/supplier name\n"
                                        "2. Invoice date (format: YYYY-MM-DD)\n"
                                        "3. Total amount (number only, no currency symbol)\n\n"
                                        "Respond ONLY in this exact JSON format, nothing else:\n"
                                        '{"vendor": "...", "date": "YYYY-MM-DD", "total": 0.00}'
                                    ),
                                },
                            ],
                        }
                    ],
                },
            )

        if resp.status_code != 200:
            raise HTTPException(status_code=502, detail=f"Claude API error: {resp.status_code}")

        result = resp.json()
        text = result["content"][0]["text"].strip()

        # Parse JSON from response (handle markdown code blocks)
        if text.startswith("```"):
            text = text.split("\n", 1)[1].rsplit("```", 1)[0].strip()

        import json
        parsed = json.loads(text)

        return {
            "vendor": parsed.get("vendor", ""),
            "date": parsed.get("date", ""),
            "total": float(parsed.get("total", 0)),
            "image_path": filename,
        }

    except httpx.HTTPError as e:
        raise HTTPException(status_code=502, detail=f"Claude API request failed: {str(e)}")
    except (json.JSONDecodeError, KeyError, ValueError) as e:
        return {
            "vendor": "",
            "date": "",
            "total": 0,
            "image_path": filename,
            "error": f"Could not parse invoice: {str(e)}",
        }


# ── Serve invoice images ────────────────────────────────────────────

@router.get("/invoice/{filename}")
async def get_invoice(filename: str):
    """Serve a saved invoice image."""
    # Prevent path traversal
    if ".." in filename or "/" in filename or "\\" in filename:
        raise HTTPException(status_code=400, detail="Invalid filename")
    filepath = os.path.join(INVOICES_DIR, filename)
    if not os.path.exists(filepath):
        raise HTTPException(status_code=404, detail="Invoice not found")
    return FileResponse(filepath)


# ── CRUD ────────────────────────────────────────────────────────────

@router.post("/")
async def record_expense(expense: ExpenseRequest):
    """Record a new expense."""
    try:
        result = await sales_db.add_expense(expense.model_dump())
        return result
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.get("/")
async def list_expenses(limit: int = 500, offset: int = 0):
    """List all expenses."""
    return await sales_db.list_expenses(limit=limit, offset=offset)


@router.delete("/{expense_id}")
async def delete_expense(expense_id: int):
    """Delete an expense."""
    deleted = await sales_db.delete_expense(expense_id)
    if not deleted:
        raise HTTPException(status_code=404, detail="Expense not found")
    return {"ok": True}
