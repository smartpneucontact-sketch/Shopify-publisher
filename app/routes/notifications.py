"""Notification routes — WhatsApp reminders and alerts."""

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel
from typing import Optional

from app.services.whatsapp import send_whatsapp, check_and_remind, is_configured

router = APIRouter(prefix="/notifications", tags=["notifications"])


class WhatsAppMessage(BaseModel):
    message: str


@router.get("/whatsapp/status")
async def whatsapp_status():
    """Check if Twilio WhatsApp is configured."""
    return {
        "configured": is_configured(),
        "note": "Set TWILIO_ACCOUNT_SID, TWILIO_AUTH_TOKEN, TWILIO_WHATSAPP_FROM, TWILIO_WHATSAPP_TO env vars"
    }


@router.post("/whatsapp/test")
async def test_whatsapp():
    """Send a test WhatsApp message."""
    if not is_configured():
        raise HTTPException(status_code=400, detail="Twilio not configured")

    result = await send_whatsapp("✅ SmartPneu WhatsApp notifications are working!")
    return result


@router.post("/whatsapp/check-daily")
async def daily_sales_check():
    """
    Check if any sales were recorded today. If not, send a WhatsApp reminder.
    This endpoint should be called daily at 10 PM Paris time via a cron job.

    On Railway, set up a cron job or use an external scheduler (e.g. cron-job.org)
    to call this endpoint at 22:00 CET/CEST.
    """
    return await check_and_remind()


@router.post("/whatsapp/send")
async def send_custom_message(body: WhatsAppMessage):
    """Send a custom WhatsApp message."""
    if not is_configured():
        raise HTTPException(status_code=400, detail="Twilio not configured")

    result = await send_whatsapp(body.message)
    return result
