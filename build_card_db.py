import json
import sqlite3
from pathlib import Path

DATA_DIR = Path(__file__).resolve().parent / "data"
JSON_PATH = DATA_DIR / "default-cards.json"
DB_PATH = DATA_DIR / "default-cards.db"


def import_cards(json_path: Path = JSON_PATH, db_path: Path = DB_PATH) -> None:
    """Import Scryfall card data from JSON into a SQLite database."""
    if not json_path.exists():
        raise FileNotFoundError(json_path)
    cards = json.loads(json_path.read_text(encoding="utf-8"))
    with sqlite3.connect(db_path) as conn:
        c = conn.cursor()
        c.execute(
            """
            CREATE TABLE IF NOT EXISTS cards (
                id TEXT PRIMARY KEY,
                name TEXT,
                set_code TEXT,
                lang TEXT,
                collector_number TEXT,
                cardmarket_id TEXT,
                image_url TEXT
            )
            """
        )
        c.execute("CREATE INDEX IF NOT EXISTS idx_name ON cards(name)")
        c.execute("DELETE FROM cards")
        for card in cards:
            image_url = ""
            if isinstance(card.get("image_uris"), dict):
                image_url = card["image_uris"].get("normal") or card["image_uris"].get("small") or ""
            c.execute(
                """INSERT OR REPLACE INTO cards (id, name, set_code, lang, collector_number, cardmarket_id, image_url)
                   VALUES (?, ?, ?, ?, ?, ?, ?)""",
                (
                    card.get("id"),
                    card.get("name"),
                    card.get("set"),
                    card.get("lang"),
                    card.get("collector_number", ""),
                    str(card.get("cardmarket_id", "")),
                    image_url,
                ),
            )
        conn.commit()


if __name__ == "__main__":
    import_cards()
    print(f"Database written to {DB_PATH}")
