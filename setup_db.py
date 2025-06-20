import sqlite3

from . import DB_FILE


def initialize_database() -> None:
    """Create the SQLite database and all required tables."""
    with sqlite3.connect(DB_FILE) as conn:
        cursor = conn.cursor()

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
                image_url TEXT
            )
            """
        )

        # create folders table and column if missing
        cursor.execute(
            """
            CREATE TABLE IF NOT EXISTS folders (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT UNIQUE
            )
            """
        )

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


if __name__ == "__main__":
    initialize_database()
