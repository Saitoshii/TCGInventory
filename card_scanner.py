"""Helper utilities for scanning barcodes from card images."""

from __future__ import annotations

from pathlib import Path
from queue import Queue
from typing import Dict, Optional, List, Iterable

import json
import sqlite3

import cv2
from pyzbar.pyzbar import decode
import requests

SCRYFALL_API_URL = "https://api.scryfall.com/cards/"
DEFAULT_CARDS_PATH = Path(__file__).resolve().parent / "data" / "default-cards.json"
DEFAULT_DB_PATH = Path(__file__).resolve().parent / "data" / "default-cards.db"

_CARDS_BY_ID: Dict[str, Dict] = {}
_CARDS_BY_NAME: Dict[str, Dict] = {}
_DB_CONN: sqlite3.Connection | None = None

#: Result type for ``fetch_card_info`` and queue entries
CardInfo = Dict[str, str]

# Queue für gescannte Karten
SCANNER_QUEUE: Queue[CardInfo] = Queue()


def _load_card_database() -> None:
    """Open the local card database connection if available."""
    global _DB_CONN
    if _DB_CONN:
        return
    if DEFAULT_DB_PATH.exists():
        _DB_CONN = sqlite3.connect(DEFAULT_DB_PATH)
        _DB_CONN.row_factory = sqlite3.Row
        return
    if not DEFAULT_CARDS_PATH.exists():
        print(f"⚠️  Lokale Kartendatei {DEFAULT_CARDS_PATH} nicht gefunden.")
        return
    try:
        with DEFAULT_CARDS_PATH.open("r", encoding="utf-8") as f:
            cards: List[Dict] = json.load(f)
    except Exception as exc:  # pragma: no cover - simple placeholder
        print(f"❌ Fehler beim Laden der Kartendaten: {exc}")
        return
    for card in cards:
        cid = card.get("id")
        name = card.get("name", "").lower()
        if cid:
            _CARDS_BY_ID[cid] = card
        if name and name not in _CARDS_BY_NAME:
            _CARDS_BY_NAME[name] = card

def scan_image(path: str) -> Optional[str]:
    """Scan an image file for barcodes and return the first result as string."""
    image = cv2.imread(str(Path(path)))
    if image is None:
        print(f"❌ Bild {path} konnte nicht geladen werden.")
        return None
    codes = decode(image)
    if not codes:
        print("❌ Kein Barcode gefunden.")
        return None
    return codes[0].data.decode("utf-8")

def fetch_card_info(card_id: str) -> Optional[CardInfo]:
    """Retrieve card details from the local database or Scryfall."""
    _load_card_database()
    if _DB_CONN:
        c = _DB_CONN.execute(
            "SELECT name, set_code, lang, cardmarket_id, collector_number, image_url FROM cards WHERE id=?",
            (card_id,),
        )
        row = c.fetchone()
        if row:
            return {
                "name": row[0],
                "set_code": row[1],
                "language": row[2],
                "cardmarket_id": row[3],
                "collector_number": row[4],
                "image_url": row[5],
            }
    card = _CARDS_BY_ID.get(card_id)
    if card:
        return {
            "name": card.get("name", ""),
            "set_code": card.get("set", ""),
            "language": card.get("lang", ""),
            "cardmarket_id": card.get("id", ""),
            "collector_number": card.get("collector_number", ""),
            "image_url": (card.get("image_uris") or {}).get("normal", ""),
        }

    try:
        resp = requests.get(f"{SCRYFALL_API_URL}{card_id}", timeout=5)
        resp.raise_for_status()
    except requests.RequestException as exc:
        print(f"❌ Fehler beim Abrufen der Kartendaten: {exc}")
        return None
    data = resp.json()
    return {
        "name": data.get("name"),
        "set_code": data.get("set"),
        "language": data.get("lang"),
        "cardmarket_id": data.get("id"),
    }


def fetch_card_info_by_name(name: str) -> Optional[CardInfo]:
    """Retrieve card details from the local database or Scryfall."""
    _load_card_database()
    if _DB_CONN:
        c = _DB_CONN.execute(
            "SELECT name, set_code, lang, cardmarket_id, collector_number, image_url, id FROM cards WHERE lower(name)=lower(?)",
            (name,),
        )
        row = c.fetchone()
        if row:
            return {
                "name": row[0],
                "set_code": row[1],
                "language": row[2],
                "cardmarket_id": row[3],
                "collector_number": row[4],
                "image_url": row[5],
                "scryfall_id": row[6],
            }
    card = _CARDS_BY_NAME.get(name.lower())
    if card:
        return {
            "name": card.get("name", name),
            "set_code": card.get("set", ""),
            "language": card.get("lang", ""),
            "cardmarket_id": card.get("id", ""),
            "collector_number": card.get("collector_number", ""),
            "image_url": (card.get("image_uris") or {}).get("normal", ""),
            "scryfall_id": card.get("id", ""),
        }

    try:
        resp = requests.get(
            f"https://api.scryfall.com/cards/named", params={"exact": name}, timeout=5
        )
        resp.raise_for_status()
    except requests.RequestException as exc:
        print(f"❌ Fehler beim Abrufen der Kartendaten: {exc}")
        return None
    data = resp.json()
    return {
        "name": data.get("name", name),
        "set_code": data.get("set", ""),
        "language": data.get("lang", ""),
        "cardmarket_id": data.get("id", ""),
        "collector_number": data.get("collector_number", ""),
        "image_url": (data.get("image_uris") or {}).get("normal", ""),
        "scryfall_id": data.get("id", ""),
    }


def autocomplete_names(query: str) -> list[str]:
    """Return card name suggestions from the local database or Scryfall."""
    _load_card_database()
    if _DB_CONN:
        c = _DB_CONN.execute(
            "SELECT DISTINCT name FROM cards WHERE lower(name) LIKE ? ORDER BY name LIMIT 20",
            (f"{query.lower()}%",),
        )
        return [row[0] for row in c.fetchall()]
    if _CARDS_BY_NAME:
        query_l = query.lower()
        matches = [
            card.get("name", "")
            for name, card in _CARDS_BY_NAME.items()
            if name.startswith(query_l)
        ]
        if matches:
            return matches[:20]

    try:
        resp = requests.get(
            "https://api.scryfall.com/cards/autocomplete",
            params={"q": query},
            timeout=5,
        )
        resp.raise_for_status()
    except requests.RequestException as exc:
        print(f"❌ Fehler beim Abrufen der Kartenvorschläge: {exc}")
        return []
    data = resp.json()
    return data.get("data", [])

def scan_and_queue(image_path: str) -> None:
    """Scan a card from an image and put its info into the queue."""
    card_id = scan_image(image_path)
    if not card_id:
        return
    info = fetch_card_info(card_id)
    if info:
        SCANNER_QUEUE.put(info)
        print(f"📸 Karte '{info['name']}' zur Queue hinzugefügt.")


def fetch_variants(name: str) -> List[CardInfo]:
    """Return all card variants matching the given name."""
    _load_card_database()
    results: List[CardInfo] = []
    if _DB_CONN:
        c = _DB_CONN.execute(
            "SELECT id, name, set_code, lang, collector_number, cardmarket_id, image_url FROM cards WHERE lower(name)=lower(?) ORDER BY set_code",
            (name,),
        )
        for row in c.fetchall():
            results.append(
                {
                    "scryfall_id": row[0],
                    "name": row[1],
                    "set_code": row[2],
                    "language": row[3],
                    "collector_number": row[4],
                    "cardmarket_id": row[5],
                    "image_url": row[6],
                }
            )
        return results
    card = _CARDS_BY_NAME.get(name.lower())
    if card:
        results.append(
            {
                "scryfall_id": card.get("id", ""),
                "name": card.get("name", name),
                "set_code": card.get("set", ""),
                "language": card.get("lang", ""),
                "collector_number": card.get("collector_number", ""),
                "cardmarket_id": card.get("id", ""),
                "image_url": (card.get("image_uris") or {}).get("normal", ""),
            }
        )
    return results
