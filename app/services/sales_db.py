"""
SQLite-based sales database service for SmartPneu tire business.

Handles sales data from all channels (Shopify, eBay, eBay Kleinanzeigen, LeBonCoin, Cash)
using async operations with aiosqlite.
"""

import os
import sqlite3
from datetime import datetime, timedelta
from typing import Optional, List, Dict, Any

import aiosqlite


class SalesDB:
    """SQLite-based sales database service with async operations."""

    def __init__(self, db_path: str = "data/sales.db"):
        """
        Initialize the SalesDB service.

        Args:
            db_path: Path to the SQLite database file (relative to app root).
        """
        self.db_path = db_path
        self._ensure_directory()

    def _ensure_directory(self) -> None:
        """Ensure the data directory exists."""
        db_dir = os.path.dirname(self.db_path)
        if db_dir and not os.path.exists(db_dir):
            os.makedirs(db_dir, exist_ok=True)

    async def init(self) -> None:
        """
        Initialize the database by creating tables if they don't exist.

        This should be called on application startup.
        """
        async with aiosqlite.connect(self.db_path) as db:
            await db.execute(
                """
                CREATE TABLE IF NOT EXISTS sales (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    sku TEXT NOT NULL,
                    channel TEXT NOT NULL,
                    sale_price REAL NOT NULL,
                    currency TEXT DEFAULT 'EUR',
                    quantity INTEGER DEFAULT 1,
                    order_ref TEXT,
                    customer_name TEXT,
                    customer_email TEXT,
                    notes TEXT,
                    sold_at TEXT NOT NULL,
                    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                    product_title TEXT,
                    brand TEXT,
                    model TEXT
                )
                """
            )

            # Migrate: remove CHECK constraint on channel (Python validation handles it)
            cursor = await db.execute("SELECT sql FROM sqlite_master WHERE type='table' AND name='sales'")
            row = await cursor.fetchone()
            if row and row[0] and 'CHECK' in row[0]:
                await db.execute("ALTER TABLE sales RENAME TO sales_old")
                await db.execute(
                    """
                    CREATE TABLE sales (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        sku TEXT NOT NULL,
                        channel TEXT NOT NULL,
                        sale_price REAL NOT NULL,
                        currency TEXT DEFAULT 'EUR',
                        quantity INTEGER DEFAULT 1,
                        order_ref TEXT,
                        customer_name TEXT,
                        customer_email TEXT,
                        notes TEXT,
                        sold_at TEXT NOT NULL,
                        created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                        product_title TEXT,
                        brand TEXT,
                        model TEXT
                    )
                    """
                )
                await db.execute("INSERT INTO sales SELECT * FROM sales_old")
                await db.execute("DROP TABLE sales_old")
                await db.commit()

            # Create index for common queries
            await db.execute(
                "CREATE INDEX IF NOT EXISTS idx_sales_channel ON sales(channel)"
            )
            await db.execute(
                "CREATE INDEX IF NOT EXISTS idx_sales_sku ON sales(sku)"
            )
            await db.execute(
                "CREATE INDEX IF NOT EXISTS idx_sales_sold_at ON sales(sold_at)"
            )

            # Expenses table
            await db.execute(
                """
                CREATE TABLE IF NOT EXISTS expenses (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    expense_date TEXT NOT NULL,
                    vendor TEXT,
                    description TEXT,
                    total_amount REAL NOT NULL,
                    currency TEXT DEFAULT 'EUR',
                    image_path TEXT,
                    notes TEXT,
                    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
                )
                """
            )
            await db.execute(
                "CREATE INDEX IF NOT EXISTS idx_expenses_date ON expenses(expense_date)"
            )

            # Listings table (Kleinanzeigen, Leboncoin, etc.)
            await db.execute(
                """
                CREATE TABLE IF NOT EXISTS listings (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    sku TEXT NOT NULL,
                    platform TEXT NOT NULL,
                    title TEXT,
                    price REAL,
                    currency TEXT DEFAULT 'EUR',
                    listing_url TEXT,
                    status TEXT DEFAULT 'active',
                    listed_at TEXT NOT NULL,
                    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
                )
                """
            )
            await db.execute(
                "CREATE INDEX IF NOT EXISTS idx_listings_sku ON listings(sku)"
            )
            await db.execute(
                "CREATE INDEX IF NOT EXISTS idx_listings_platform ON listings(platform)"
            )

            # Track which SKUs have been unlisted via sync
            await db.execute(
                """
                CREATE TABLE IF NOT EXISTS unlisted_skus (
                    sku TEXT PRIMARY KEY,
                    unlisted_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
                )
                """
            )

            await db.commit()

    async def mark_sku_unlisted(self, sku: str) -> None:
        """Mark a SKU as unlisted (synced to Shopify with stock 0)."""
        async with aiosqlite.connect(self.db_path) as db:
            await db.execute(
                "INSERT OR REPLACE INTO unlisted_skus (sku, unlisted_at) VALUES (?, datetime('now'))",
                (sku,),
            )
            await db.commit()

    async def unmark_sku_unlisted(self, sku: str) -> None:
        """Remove unlisted mark from a SKU."""
        async with aiosqlite.connect(self.db_path) as db:
            await db.execute("DELETE FROM unlisted_skus WHERE sku = ?", (sku,))
            await db.commit()

    async def get_unlisted_skus(self) -> set:
        """Get all SKUs currently marked as unlisted."""
        async with aiosqlite.connect(self.db_path) as db:
            cursor = await db.execute("SELECT sku FROM unlisted_skus")
            rows = await cursor.fetchall()
            return {r[0] for r in rows}

    async def record_sale(self, sale_data: Dict[str, Any]) -> Dict[str, Any]:
        """
        Record a new sale in the database.

        Args:
            sale_data: Dictionary containing sale information with keys:
                - sku (required): Tire SKU
                - channel (required): One of 'shopify', 'ebay', 'ebay_kleinanzeigen', 'leboncoin', 'cash'
                - sale_price (required): Sale price in EUR
                - quantity (optional, default=1): Number of units sold
                - currency (optional, default='EUR'): Currency code
                - order_ref (optional): External order ID
                - customer_name (optional): Customer name
                - customer_email (optional): Customer email
                - notes (optional): Notes about the sale
                - sold_at (optional): ISO datetime string (defaults to current time)
                - product_title (optional): Tire description/title
                - brand (optional): Tire brand
                - model (optional): Tire model

        Returns:
            The inserted sale record with its ID.

        Raises:
            ValueError: If required fields are missing or invalid.
            sqlite3.IntegrityError: If the data violates database constraints.
        """
        # Validate required fields
        if "sku" not in sale_data or not sale_data["sku"]:
            raise ValueError("sku is required")
        if "channel" not in sale_data or not sale_data["channel"]:
            raise ValueError("channel is required")
        if "sale_price" not in sale_data:
            raise ValueError("sale_price is required")

        # Validate channel
        valid_channels = ["shopify", "ebay", "ebay_kleinanzeigen", "leboncoin", "cash", "cash_de", "cash_fr"]
        if sale_data["channel"] not in valid_channels:
            raise ValueError(
                f"channel must be one of {valid_channels}, got {sale_data['channel']}"
            )

        # Set defaults
        sold_at = sale_data.get("sold_at", datetime.utcnow().isoformat())
        quantity = sale_data.get("quantity", 1)
        currency = sale_data.get("currency", "EUR")

        async with aiosqlite.connect(self.db_path) as db:
            cursor = await db.execute(
                """
                INSERT INTO sales (
                    sku, channel, sale_price, quantity, currency,
                    order_ref, customer_name, customer_email, notes,
                    sold_at, product_title, brand, model
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    sale_data["sku"],
                    sale_data["channel"],
                    sale_data["sale_price"],
                    quantity,
                    currency,
                    sale_data.get("order_ref"),
                    sale_data.get("customer_name"),
                    sale_data.get("customer_email"),
                    sale_data.get("notes"),
                    sold_at,
                    sale_data.get("product_title"),
                    sale_data.get("brand"),
                    sale_data.get("model"),
                ),
            )
            await db.commit()

            # Return the inserted record with its ID
            sale_id = cursor.lastrowid
            return await self._get_sale_by_id(db, sale_id)

    async def _get_sale_by_id(self, db: aiosqlite.Connection, sale_id: int) -> Dict[str, Any]:
        """
        Retrieve a sale record by ID.

        Args:
            db: Active database connection.
            sale_id: The ID of the sale to retrieve.

        Returns:
            The sale record as a dictionary.
        """
        cursor = await db.execute("SELECT * FROM sales WHERE id = ?", (sale_id,))
        row = await cursor.fetchone()
        if not row:
            return {}

        columns = [description[0] for description in cursor.description]
        return dict(zip(columns, row))

    async def get_sales(
        self,
        channel: Optional[str] = None,
        start_date: Optional[str] = None,
        end_date: Optional[str] = None,
        sku: Optional[str] = None,
        limit: int = 100,
        offset: int = 0,
    ) -> List[Dict[str, Any]]:
        """
        Query sales with optional filters.

        Args:
            channel: Filter by channel (optional).
            start_date: Filter by start date (ISO format, optional).
            end_date: Filter by end date (ISO format, optional).
            sku: Filter by SKU (optional).
            limit: Maximum number of results to return (default=100).
            offset: Number of results to skip (default=0).

        Returns:
            List of sale records matching the filters.
        """
        query = "SELECT * FROM sales WHERE 1=1"
        params = []

        if channel:
            query += " AND channel = ?"
            params.append(channel)

        if start_date:
            query += " AND sold_at >= ?"
            params.append(start_date)

        if end_date:
            query += " AND sold_at <= ?"
            params.append(end_date)

        if sku:
            query += " AND sku = ?"
            params.append(sku)

        query += " ORDER BY sold_at DESC LIMIT ? OFFSET ?"
        params.extend([limit, offset])

        async with aiosqlite.connect(self.db_path) as db:
            db.row_factory = aiosqlite.Row
            cursor = await db.execute(query, params)
            rows = await cursor.fetchall()

        return [dict(row) for row in rows]

    async def get_revenue_summary(
        self,
        start_date: Optional[str] = None,
        end_date: Optional[str] = None,
    ) -> Dict[str, Any]:
        """
        Get a comprehensive revenue summary.

        Args:
            start_date: Filter by start date (ISO format, optional).
            end_date: Filter by end date (ISO format, optional).

        Returns:
            Dictionary containing:
                - total_revenue: Total revenue in EUR
                - total_quantity: Total units sold
                - sale_count: Number of sales
                - revenue_by_channel: Dict with revenue per channel
                - revenue_by_day: List of daily revenue data
                - top_skus: List of top 10 SKUs by revenue
        """
        async with aiosqlite.connect(self.db_path) as db:
            # Base query
            where_clause = "WHERE 1=1"
            params = []
            if start_date:
                where_clause += " AND sold_at >= ?"
                params.append(start_date)
            if end_date:
                where_clause += " AND sold_at <= ?"
                params.append(end_date)

            # Total revenue
            cursor = await db.execute(
                f"SELECT SUM(sale_price * quantity) as total, COUNT(*) as count, SUM(quantity) as qty FROM sales {where_clause}",
                params,
            )
            row = await cursor.fetchone()
            total_revenue = row[0] or 0.0
            sale_count = row[1] or 0
            total_quantity = row[2] or 0

            # Revenue by channel
            cursor = await db.execute(
                f"SELECT channel, SUM(sale_price * quantity) as revenue, COUNT(*) as count FROM sales {where_clause} GROUP BY channel ORDER BY revenue DESC",
                params,
            )
            revenue_by_channel_rows = await cursor.fetchall()
            revenue_by_channel = [
                {
                    "channel": row[0],
                    "revenue": row[1] or 0.0,
                    "count": row[2],
                }
                for row in revenue_by_channel_rows
            ]

            # Revenue by day
            cursor = await db.execute(
                f"SELECT DATE(sold_at) as day, SUM(sale_price * quantity) as revenue, COUNT(*) as count FROM sales {where_clause} GROUP BY DATE(sold_at) ORDER BY day DESC",
                params,
            )
            revenue_by_day_rows = await cursor.fetchall()
            revenue_by_day = [
                {
                    "day": row[0],
                    "revenue": row[1] or 0.0,
                    "count": row[2],
                }
                for row in revenue_by_day_rows
            ]

            # Top SKUs
            cursor = await db.execute(
                f"SELECT sku, product_title, SUM(sale_price * quantity) as revenue, SUM(quantity) as quantity FROM sales {where_clause} GROUP BY sku ORDER BY revenue DESC LIMIT 10",
                params,
            )
            top_skus_rows = await cursor.fetchall()
            top_skus = [
                {
                    "sku": row[0],
                    "product_title": row[1],
                    "revenue": row[2] or 0.0,
                    "quantity": row[3],
                }
                for row in top_skus_rows
            ]

        return {
            "total_revenue": total_revenue,
            "total_quantity": total_quantity,
            "sale_count": sale_count,
            "revenue_by_channel": revenue_by_channel,
            "revenue_by_day": revenue_by_day,
            "top_skus": top_skus,
        }

    async def get_daily_revenue(self, days: int = 30) -> List[Dict[str, Any]]:
        """
        Get daily revenue for the last N days.

        Args:
            days: Number of days to include (default=30).

        Returns:
            List of daily revenue records sorted by day (newest first).
        """
        start_date = (datetime.utcnow() - timedelta(days=days)).isoformat()

        async with aiosqlite.connect(self.db_path) as db:
            cursor = await db.execute(
                """
                SELECT DATE(sold_at) as day, SUM(sale_price * quantity) as revenue, COUNT(*) as count
                FROM sales
                WHERE sold_at >= ?
                GROUP BY DATE(sold_at)
                ORDER BY day DESC
                """,
                (start_date,),
            )
            rows = await cursor.fetchall()

        return [
            {
                "day": row[0],
                "revenue": row[1] or 0.0,
                "count": row[2],
            }
            for row in rows
        ]

    async def get_channel_breakdown(
        self,
        start_date: Optional[str] = None,
        end_date: Optional[str] = None,
    ) -> List[Dict[str, Any]]:
        """
        Get revenue breakdown by channel.

        Args:
            start_date: Filter by start date (ISO format, optional).
            end_date: Filter by end date (ISO format, optional).

        Returns:
            List of channel revenue records sorted by revenue (descending).
        """
        query = "SELECT channel, SUM(sale_price * quantity) as revenue, COUNT(*) as count, SUM(quantity) as quantity FROM sales WHERE 1=1"
        params = []

        if start_date:
            query += " AND sold_at >= ?"
            params.append(start_date)
        if end_date:
            query += " AND sold_at <= ?"
            params.append(end_date)

        query += " GROUP BY channel ORDER BY revenue DESC"

        async with aiosqlite.connect(self.db_path) as db:
            cursor = await db.execute(query, params)
            rows = await cursor.fetchall()

        return [
            {
                "channel": row[0],
                "revenue": row[1] or 0.0,
                "count": row[2],
                "quantity": row[3],
            }
            for row in rows
        ]

    async def delete_sale(self, sale_id: int) -> bool:
        """
        Delete a sale record by ID.

        Args:
            sale_id: The ID of the sale to delete.

        Returns:
            True if a sale was deleted, False if no sale with that ID existed.
        """
        async with aiosqlite.connect(self.db_path) as db:
            cursor = await db.execute("DELETE FROM sales WHERE id = ?", (sale_id,))
            await db.commit()
            return cursor.rowcount > 0

    async def update_sale(self, sale_id: int, updates: Dict[str, Any]) -> Dict[str, Any]:
        """
        Update a sale record.

        Args:
            sale_id: The ID of the sale to update.
            updates: Dictionary of fields to update.

        Returns:
            The updated sale record.

        Raises:
            ValueError: If the sale doesn't exist.
        """
        # Define allowed fields that can be updated
        allowed_fields = {
            "sku",
            "channel",
            "sale_price",
            "currency",
            "quantity",
            "order_ref",
            "customer_name",
            "customer_email",
            "notes",
            "sold_at",
            "product_title",
            "brand",
            "model",
        }

        # Filter updates to only allowed fields
        filtered_updates = {k: v for k, v in updates.items() if k in allowed_fields}

        if not filtered_updates:
            raise ValueError("No valid fields to update")

        # Build dynamic UPDATE query
        set_clause = ", ".join([f"{field} = ?" for field in filtered_updates.keys()])
        query = f"UPDATE sales SET {set_clause} WHERE id = ?"
        params = list(filtered_updates.values()) + [sale_id]

        async with aiosqlite.connect(self.db_path) as db:
            cursor = await db.execute(query, params)
            await db.commit()

            if cursor.rowcount == 0:
                raise ValueError(f"Sale with ID {sale_id} not found")

            # Fetch and return the updated record
            return await self._get_sale_by_id(db, sale_id)

    # ── Expenses ────────────────────────────────────────────────────

    async def add_expense(self, data: Dict[str, Any]) -> Dict[str, Any]:
        """Record a new expense."""
        if not data.get("total_amount"):
            raise ValueError("total_amount is required")
        if not data.get("expense_date"):
            raise ValueError("expense_date is required")

        async with aiosqlite.connect(self.db_path) as db:
            cursor = await db.execute(
                """
                INSERT INTO expenses (expense_date, vendor, description, total_amount, currency, image_path, notes)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    data["expense_date"],
                    data.get("vendor", ""),
                    data.get("description", ""),
                    data["total_amount"],
                    data.get("currency", "EUR"),
                    data.get("image_path", ""),
                    data.get("notes", ""),
                ),
            )
            await db.commit()
            expense_id = cursor.lastrowid

            db.row_factory = aiosqlite.Row
            cur = await db.execute("SELECT * FROM expenses WHERE id = ?", (expense_id,))
            row = await cur.fetchone()
            return dict(row) if row else {}

    async def list_expenses(self, limit: int = 500, offset: int = 0) -> List[Dict[str, Any]]:
        """List expenses ordered by date descending."""
        async with aiosqlite.connect(self.db_path) as db:
            db.row_factory = aiosqlite.Row
            cursor = await db.execute(
                "SELECT * FROM expenses ORDER BY expense_date DESC, id DESC LIMIT ? OFFSET ?",
                (limit, offset),
            )
            rows = await cursor.fetchall()
            return [dict(r) for r in rows]

    async def delete_expense(self, expense_id: int) -> bool:
        """Delete an expense by ID."""
        async with aiosqlite.connect(self.db_path) as db:
            cursor = await db.execute("DELETE FROM expenses WHERE id = ?", (expense_id,))
            await db.commit()
            return cursor.rowcount > 0

    # ── Listings ────────────────────────────────────────────────────

    async def add_listing(self, data: Dict[str, Any]) -> Dict[str, Any]:
        """Record a new listing."""
        if not data.get("sku"):
            raise ValueError("sku is required")
        if not data.get("platform"):
            raise ValueError("platform is required")

        async with aiosqlite.connect(self.db_path) as db:
            cursor = await db.execute(
                """
                INSERT INTO listings (sku, platform, title, price, currency, listing_url, status, listed_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    data["sku"],
                    data["platform"],
                    data.get("title", ""),
                    data.get("price"),
                    data.get("currency", "EUR"),
                    data.get("listing_url", ""),
                    data.get("status", "active"),
                    data.get("listed_at", datetime.utcnow().isoformat()),
                ),
            )
            await db.commit()
            listing_id = cursor.lastrowid

            db.row_factory = aiosqlite.Row
            cur = await db.execute("SELECT * FROM listings WHERE id = ?", (listing_id,))
            row = await cur.fetchone()
            return dict(row) if row else {}

    async def list_listings(self, limit: int = 500, offset: int = 0, platform: str = None, status: str = None) -> List[Dict[str, Any]]:
        """List listings ordered by date descending."""
        query = "SELECT * FROM listings WHERE 1=1"
        params = []
        if platform:
            query += " AND platform = ?"
            params.append(platform)
        if status:
            query += " AND status = ?"
            params.append(status)
        query += " ORDER BY listed_at DESC, id DESC LIMIT ? OFFSET ?"
        params.extend([limit, offset])

        async with aiosqlite.connect(self.db_path) as db:
            db.row_factory = aiosqlite.Row
            cursor = await db.execute(query, params)
            rows = await cursor.fetchall()
            return [dict(r) for r in rows]

    async def update_listing_status(self, listing_id: int, status: str) -> bool:
        """Update listing status (active, sold, expired)."""
        async with aiosqlite.connect(self.db_path) as db:
            cursor = await db.execute(
                "UPDATE listings SET status = ? WHERE id = ?", (status, listing_id)
            )
            await db.commit()
            return cursor.rowcount > 0

    async def update_listing_url(self, listing_id: int, url: str) -> bool:
        """Update listing URL."""
        async with aiosqlite.connect(self.db_path) as db:
            cursor = await db.execute(
                "UPDATE listings SET listing_url = ? WHERE id = ?", (url, listing_id)
            )
            await db.commit()
            return cursor.rowcount > 0

    async def delete_listing(self, listing_id: int) -> bool:
        """Delete a listing by ID."""
        async with aiosqlite.connect(self.db_path) as db:
            cursor = await db.execute("DELETE FROM listings WHERE id = ?", (listing_id,))
            await db.commit()
            return cursor.rowcount > 0


# Singleton instance — uses persistent volume path from config
from app.config import settings
sales_db = SalesDB(db_path=settings.SALES_DB_PATH)
