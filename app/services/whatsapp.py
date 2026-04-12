"""
Twilio WhatsApp notification service for SmartPneu.
Sends daily reminders if no sales have been recorded by 10 PM Paris time.

Required env vars:
  TWILIO_ACCOUNT_SID    — your Twilio account SID
  TWILIO_AUTH_TOKEN      — your Twilio auth token
  TWILIO_WHATSAPP_FROM   — e.g. "whatsapp:+14155238886" (Twilio sandbox number)
  TWILIO_WHATSAPP_TO     — e.g. "whatsapp:+33612345678" (your personal number)
"""

import os
import httpx
from datetime import datetime, timezone, timedelta

TWILIO_SID = os.getenv("TWILIO_ACCOUNT_SID", "")
TWILIO_TOKEN = os.getenv("TWILIO_AUTH_TOKEN", "")
TWILIO_FROM = os.getenv("TWILIO_WHATSAPP_FROM", "")
TWILIO_TO = os.getenv("TWILIO_WHATSAPP_TO", "")

PARIS_TZ = timezone(timedelta(hours=2))  # CEST (summer), adjust if needed


def is_configured() -> bool:
    return bool(TWILIO_SID and TWILIO_TOKEN and TWILIO_FROM and TWILIO_TO)


async def send_whatsapp(message: str) -> dict:
    """Send a WhatsApp message via Twilio API."""
    if not is_configured():
        return {"status": "error", "message": "Twilio not configured. Set TWILIO_* env vars."}

    url = f"https://api.twilio.com/2010-04-01/Accounts/{TWILIO_SID}/Messages.json"

    async with httpx.AsyncClient(timeout=30) as client:
        resp = await client.post(
            url,
            auth=(TWILIO_SID, TWILIO_TOKEN),
            data={
                "From": TWILIO_FROM,
                "To": TWILIO_TO,
                "Body": message,
            },
        )

        if resp.status_code in (200, 201):
            data = resp.json()
            return {"status": "sent", "sid": data.get("sid")}
        else:
            return {
                "status": "error",
                "code": resp.status_code,
                "message": resp.text,
            }


async def check_and_remind() -> dict:
    """
    Check if any sales were recorded today. If not, send a WhatsApp reminder.
    Designed to be called at 10 PM Paris time via a cron job or scheduled task.
    """
    from app.services.sales_db import sales_db

    # Get today's date in Paris timezone
    now_paris = datetime.now(PARIS_TZ)
    today_start = now_paris.replace(hour=0, minute=0, second=0, microsecond=0)

    # Query today's sales across all channels
    sales = await sales_db.get_sales(
        channel=None,
        start_date=today_start,
        end_date=None,
        sku=None,
        limit=1,
        offset=0,
    )

    if sales:
        return {
            "status": "ok",
            "message": f"Sales found today ({len(sales)}+), no reminder needed.",
            "reminded": False,
        }

    # No sales today — send reminder
    message = (
        f"🚨 SmartPneu Reminder\n\n"
        f"No sales have been recorded today ({now_paris.strftime('%A %d %B %Y')}).\n\n"
        f"Don't forget to log your sales!\n"
        f"📱 Go to your dashboard to record them."
    )

    result = await send_whatsapp(message)
    return {
        "status": "reminded",
        "message": "No sales today — WhatsApp reminder sent.",
        "reminded": True,
        "twilio": result,
    }
