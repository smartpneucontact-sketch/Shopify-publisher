"""
Event bus router for inter-service communication in Railway deployment.

Manages event subscriptions and dispatches events to registered subscribers
across the Railway internal network (*.railway.internal).
"""

import asyncio
import logging
from collections import deque
from datetime import datetime
from typing import Dict, List, Set, Optional
from pydantic import BaseModel, Field
import httpx
from fastapi import APIRouter, BackgroundTasks, HTTPException

# Configure logging
logger = logging.getLogger(__name__)

# Create router
router = APIRouter(tags=["events"])

# ============================================================================
# Data Models
# ============================================================================


class EventPayload(BaseModel):
    """Represents an event being emitted."""

    type: str = Field(..., description="Event type identifier")
    data: dict = Field(default_factory=dict, description="Event payload data")
    timestamp: Optional[datetime] = Field(
        default_factory=datetime.utcnow, description="Event creation timestamp"
    )


class EventLogEntry(BaseModel):
    """Represents a logged event in the ring buffer."""

    type: str
    data: dict
    timestamp: datetime
    subscribers_notified: int


class SubscriberInfo(BaseModel):
    """Information about a subscriber."""

    url: str
    event_type: str


class SubscriberListResponse(BaseModel):
    """Response for subscriber listing endpoint."""

    event_type: str
    subscriber_count: int
    subscribers: List[str]


# ============================================================================
# Event Bus State Management
# ============================================================================


class EventBus:
    """Manages event subscriptions and dispatching."""

    def __init__(self):
        """Initialize the event bus with default subscribers."""
        self.subscribers: Dict[str, Set[str]] = {}
        self.event_log: deque = deque(maxlen=50)  # Ring buffer for last 50 events
        self._setup_default_subscribers()

    def _setup_default_subscribers(self) -> None:
        """Set up default event subscriptions from environment variables."""
        import os

        # Product picture queue (Studio service)
        studio_host = os.getenv("STUDIO_HOST", "studio.railway.internal")
        studio_product_queue = os.getenv(
            "STUDIO_PRODUCT_QUEUE_ENDPOINT", f"http://{studio_host}/webhook/products"
        )
        self.add_subscriber("product.created", studio_product_queue)

        # Publisher auto-list endpoint
        publisher_host = os.getenv("PUBLISHER_HOST", "localhost:8000")
        publisher_eBay_endpoint = os.getenv(
            "PUBLISHER_EBAY_ENDPOINT", f"http://{publisher_host}/api/auto-list"
        )
        self.add_subscriber("images.uploaded", publisher_eBay_endpoint)

        # Future marketplace sync
        marketplace_host = os.getenv("MARKETPLACE_HOST", "marketplace.railway.internal")
        marketplace_endpoint = os.getenv(
            "MARKETPLACE_SYNC_ENDPOINT", f"http://{marketplace_host}/sync/products"
        )
        self.add_subscriber("product.updated", marketplace_endpoint)

        # Future analytics
        analytics_host = os.getenv("ANALYTICS_HOST", "analytics.railway.internal")
        analytics_endpoint = os.getenv(
            "ANALYTICS_ENDPOINT", f"http://{analytics_host}/events/sales"
        )
        self.add_subscriber("sale.recorded", analytics_endpoint)

        logger.info("Event bus initialized with default subscribers")

    def add_subscriber(self, event_type: str, subscriber_url: str) -> None:
        """
        Register a subscriber for an event type.

        Args:
            event_type: The type of event to subscribe to
            subscriber_url: The URL to notify when event is emitted
        """
        if event_type not in self.subscribers:
            self.subscribers[event_type] = set()
        self.subscribers[event_type].add(subscriber_url)
        logger.info(f"Subscriber registered: {event_type} -> {subscriber_url}")

    def remove_subscriber(self, event_type: str, subscriber_url: str) -> bool:
        """
        Remove a subscriber for an event type.

        Args:
            event_type: The type of event
            subscriber_url: The subscriber URL to remove

        Returns:
            True if subscriber was removed, False if not found
        """
        if event_type in self.subscribers and subscriber_url in self.subscribers[event_type]:
            self.subscribers[event_type].remove(subscriber_url)
            logger.info(f"Subscriber removed: {event_type} -> {subscriber_url}")
            return True
        return False

    def get_subscribers(self, event_type: str) -> List[str]:
        """
        Get all subscribers for an event type.

        Args:
            event_type: The type of event

        Returns:
            List of subscriber URLs
        """
        return list(self.subscribers.get(event_type, []))

    def get_all_subscribers(self) -> Dict[str, List[str]]:
        """
        Get all subscribers grouped by event type.

        Returns:
            Dictionary mapping event types to lists of subscriber URLs
        """
        return {event_type: list(urls) for event_type, urls in self.subscribers.items()}

    def log_event(self, event: EventPayload, subscribers_notified: int) -> None:
        """
        Log an event to the ring buffer.

        Args:
            event: The event that was emitted
            subscribers_notified: Number of subscribers notified
        """
        entry = EventLogEntry(
            type=event.type,
            data=event.data,
            timestamp=event.timestamp or datetime.utcnow(),
            subscribers_notified=subscribers_notified,
        )
        self.event_log.append(entry)

    def get_event_log(self) -> List[EventLogEntry]:
        """
        Get the event log (last 50 events).

        Returns:
            List of logged events in chronological order
        """
        return list(self.event_log)


# Global event bus instance
event_bus = EventBus()


# ============================================================================
# Async HTTP Client for Subscriber Notification
# ============================================================================


async def notify_subscriber(url: str, event: EventPayload) -> bool:
    """
    Notify a single subscriber about an event.

    Makes an async HTTP POST to the subscriber URL with the event payload.
    Catches and logs errors without re-raising to prevent event emission failure.

    Args:
        url: The subscriber's webhook URL
        event: The event payload to send

    Returns:
        True if notification was successful, False otherwise
    """
    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            response = await client.post(
                url,
                json=event.model_dump(),
                headers={"Content-Type": "application/json"},
            )
            response.raise_for_status()
            logger.info(f"Event '{event.type}' notified to {url} (status: {response.status_code})")
            return True
    except httpx.TimeoutException:
        logger.error(f"Timeout notifying subscriber {url} for event '{event.type}'")
        return False
    except httpx.ConnectError as e:
        logger.error(f"Connection error notifying {url}: {str(e)}")
        return False
    except httpx.HTTPStatusError as e:
        logger.error(
            f"HTTP error from {url}: {e.response.status_code} {e.response.reason_phrase}"
        )
        return False
    except Exception as e:
        logger.error(f"Unexpected error notifying {url}: {str(e)}", exc_info=True)
        return False


async def dispatch_event(event: EventPayload) -> int:
    """
    Dispatch an event to all registered subscribers.

    Makes concurrent requests to all subscribers for the event type.
    Does not fail if any individual subscriber is unreachable.

    Args:
        event: The event to dispatch

    Returns:
        Number of subscribers successfully notified
    """
    subscribers = event_bus.get_subscribers(event.type)

    if not subscribers:
        logger.info(f"No subscribers registered for event type '{event.type}'")
        return 0

    logger.info(
        f"Dispatching event '{event.type}' to {len(subscribers)} subscriber(s)"
    )

    # Create concurrent tasks for all subscribers
    tasks = [notify_subscriber(url, event) for url in subscribers]
    results = await asyncio.gather(*tasks, return_exceptions=False)

    # Count successful notifications
    successful = sum(1 for result in results if result)
    logger.info(
        f"Event '{event.type}' dispatched: {successful}/{len(subscribers)} "
        "subscribers notified successfully"
    )

    return successful


# ============================================================================
# API Endpoints
# ============================================================================


@router.post("/emit", response_model=dict)
async def emit_event(payload: EventPayload, background_tasks: BackgroundTasks) -> dict:
    """
    Emit an event and fan out to all subscribers.

    The event is dispatched asynchronously to all registered subscribers.
    Subscribers are notified via HTTP POST to their registered endpoints.

    Args:
        payload: Event payload with at minimum a 'type' field
        background_tasks: FastAPI background tasks for async dispatch

    Returns:
        Dictionary with event details and subscriber count
    """
    if not payload.type or not payload.type.strip():
        raise HTTPException(status_code=400, detail="Event type cannot be empty")

    # Ensure timestamp is set
    if payload.timestamp is None:
        payload.timestamp = datetime.utcnow()

    # Schedule background dispatch
    background_tasks.add_task(dispatch_event, payload)

    # Log the event
    subscriber_count = len(event_bus.get_subscribers(payload.type))
    event_bus.log_event(payload, subscriber_count)

    logger.info(f"Event '{payload.type}' emitted with {subscriber_count} potential subscribers")

    return {
        "status": "accepted",
        "type": payload.type,
        "timestamp": payload.timestamp.isoformat(),
        "subscribers_to_notify": subscriber_count,
        "message": "Event queued for dispatch to subscribers",
    }


@router.get("/subscribers", response_model=List[SubscriberListResponse])
async def list_subscribers() -> List[SubscriberListResponse]:
    """
    List all registered event types and their subscriber counts.

    Returns:
        List of subscriber information for each registered event type
    """
    subscribers = event_bus.get_all_subscribers()

    if not subscribers:
        return []

    response = [
        SubscriberListResponse(
            event_type=event_type,
            subscriber_count=len(urls),
            subscribers=urls,
        )
        for event_type, urls in subscribers.items()
    ]

    return sorted(response, key=lambda x: x.event_type)


@router.post("/subscribe", response_model=dict)
async def subscribe(event_type: str, subscriber_url: str) -> dict:
    """
    Dynamically register a new subscriber for an event type.

    Stores the subscription in memory (resets on restart).
    Useful for adding new subscribers without redeploying.

    Args:
        event_type: The event type to subscribe to
        subscriber_url: The webhook URL to notify

    Returns:
        Confirmation of subscription
    """
    if not event_type or not event_type.strip():
        raise HTTPException(status_code=400, detail="Event type cannot be empty")

    if not subscriber_url or not subscriber_url.strip():
        raise HTTPException(status_code=400, detail="Subscriber URL cannot be empty")

    # Validate URL format
    if not (subscriber_url.startswith("http://") or subscriber_url.startswith("https://")):
        raise HTTPException(
            status_code=400, detail="Subscriber URL must start with http:// or https://"
        )

    event_bus.add_subscriber(event_type, subscriber_url)

    return {
        "status": "subscribed",
        "event_type": event_type,
        "subscriber_url": subscriber_url,
        "total_subscribers": len(event_bus.get_subscribers(event_type)),
    }


@router.get("/log", response_model=List[EventLogEntry])
async def get_event_log() -> List[EventLogEntry]:
    """
    Retrieve the event log with the last 50 emitted events.

    Returns a ring buffer of recent events for debugging and monitoring.

    Returns:
        List of logged events in chronological order (oldest first)
    """
    return event_bus.get_event_log()


@router.post("/unsubscribe", response_model=dict)
async def unsubscribe(event_type: str, subscriber_url: str) -> dict:
    """
    Remove a subscriber from an event type.

    Args:
        event_type: The event type to unsubscribe from
        subscriber_url: The subscriber URL to remove

    Returns:
        Confirmation of unsubscription
    """
    if not event_type or not event_type.strip():
        raise HTTPException(status_code=400, detail="Event type cannot be empty")

    removed = event_bus.remove_subscriber(event_type, subscriber_url)

    if not removed:
        raise HTTPException(
            status_code=404,
            detail=f"Subscriber {subscriber_url} not found for event type {event_type}",
        )

    return {
        "status": "unsubscribed",
        "event_type": event_type,
        "subscriber_url": subscriber_url,
        "remaining_subscribers": len(event_bus.get_subscribers(event_type)),
    }


@router.get("/health", response_model=dict)
async def health_check() -> dict:
    """
    Health check endpoint for the event bus.

    Returns:
        Status information about the event bus
    """
    subscribers = event_bus.get_all_subscribers()
    total_subscribers = sum(len(urls) for urls in subscribers.values())

    return {
        "status": "healthy",
        "event_types_registered": len(subscribers),
        "total_subscribers": total_subscribers,
        "log_size": len(event_bus.get_event_log()),
    }
