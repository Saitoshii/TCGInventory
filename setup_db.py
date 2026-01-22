import sqlite3

from . import DB_FILE
from .auth import init_user_db


def initialize_database() -> None:
    """Create the SQLite database and all required tables."""
    with sqlite3.connect(DB_FILE) as conn:
        cursor = conn.cursor()
        init_user_db()

        # Tabelle 1: Karten
        cursor.execute(
            """
            CREATE TABLE IF NOT EXISTS cards (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT NOT NULL,
                set_code TEXT NOT NULL,
                language TEXT,
                condition TEXT,
                price REAL,
                quantity INTEGER DEFAULT 1,
                storage_code TEXT,
                cardmarket_id TEXT,
                folder_id INTEGER,
                status TEXT DEFAULT 'verfügbar',
                date_added TEXT,
                collector_number TEXT,
                scryfall_id TEXT,
                image_url TEXT,
                foil INTEGER DEFAULT 0
            )
            """
        )

        # create folders table and column if missing
        cursor.execute(
            """
            CREATE TABLE IF NOT EXISTS folders (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT UNIQUE,
                pages INTEGER DEFAULT 0
            )
            """
        )

        cursor.execute("PRAGMA table_info(folders)")
        folder_columns = [row[1] for row in cursor.fetchall()]
        if "pages" not in folder_columns:
            cursor.execute("ALTER TABLE folders ADD COLUMN pages INTEGER DEFAULT 0")

        cursor.execute("PRAGMA table_info(cards)")
        columns = [row[1] for row in cursor.fetchall()]
        if "folder_id" not in columns:
            cursor.execute("ALTER TABLE cards ADD COLUMN folder_id INTEGER")
        if "collector_number" not in columns:
            cursor.execute("ALTER TABLE cards ADD COLUMN collector_number TEXT")
        if "scryfall_id" not in columns:
            cursor.execute("ALTER TABLE cards ADD COLUMN scryfall_id TEXT")
        if "image_url" not in columns:
            cursor.execute("ALTER TABLE cards ADD COLUMN image_url TEXT")
        if "quantity" not in columns:
            cursor.execute("ALTER TABLE cards ADD COLUMN quantity INTEGER DEFAULT 1")
        if "foil" not in columns:
            cursor.execute("ALTER TABLE cards ADD COLUMN foil INTEGER DEFAULT 0")
        # New fields for improved data model
        if "item_type" not in columns:
            cursor.execute("ALTER TABLE cards ADD COLUMN item_type TEXT DEFAULT 'card'")
        if "reserved_until" not in columns:
            cursor.execute("ALTER TABLE cards ADD COLUMN reserved_until TEXT")
        if "location_hint" not in columns:
            cursor.execute("ALTER TABLE cards ADD COLUMN location_hint TEXT")

        # Tabelle 2: Lagerplätze
        cursor.execute(
            """
            CREATE TABLE IF NOT EXISTS storage_slots (
                code TEXT PRIMARY KEY,
                is_occupied INTEGER DEFAULT 0
            )
            """
        )

        # Tabelle 3: Verkaufslog
        cursor.execute(
            """
            CREATE TABLE IF NOT EXISTS sales_log (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                card_id INTEGER,
                buyer_name TEXT,
                buyer_address TEXT,
                price REAL,
                date_sold TEXT,
                FOREIGN KEY(card_id) REFERENCES cards(id)
            )
            """
        )

        # Tabelle 4: Orders from Cardmarket emails
        cursor.execute(
            """
            CREATE TABLE IF NOT EXISTS orders (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                buyer_name TEXT NOT NULL,
                email_message_id TEXT UNIQUE NOT NULL,
                date_received TEXT NOT NULL,
                status TEXT DEFAULT 'open',
                date_completed TEXT
            )
            """
        )

        # Add email_date column to orders if missing (for email date vs. insertion date)
        cursor.execute("PRAGMA table_info(orders)")
        order_columns = [row[1] for row in cursor.fetchall()]
        if "email_date" not in order_columns:
            cursor.execute("ALTER TABLE orders ADD COLUMN email_date TEXT")

        # Tabelle 5: Order items (cards in orders)
        cursor.execute(
            """
            CREATE TABLE IF NOT EXISTS order_items (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                order_id INTEGER NOT NULL,
                card_name TEXT NOT NULL,
                quantity INTEGER NOT NULL,
                image_url TEXT,
                storage_code TEXT,
                FOREIGN KEY(order_id) REFERENCES orders(id) ON DELETE CASCADE
            )
            """
        )

        # Tabelle 6: Audit log for tracking changes
        cursor.execute(
            """
            CREATE TABLE IF NOT EXISTS audit_log (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                card_id INTEGER,
                user TEXT,
                action TEXT NOT NULL,
                field_name TEXT,
                old_value TEXT,
                new_value TEXT,
                timestamp TEXT NOT NULL,
                FOREIGN KEY(card_id) REFERENCES cards(id)
            )
            """
        )


if __name__ == "__main__":
    initialize_database()
