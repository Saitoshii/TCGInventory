"""Helper utilities for scanning barcodes from card images."""

from __future__ import annotations

from pathlib import Path
from queue import Queue
from typing import Dict, Optional, List

import json

import cv2
from pyzbar.pyzbar import decode
import requests

SCRYFALL_API_URL = "https://api.scryfall.com/cards/"
DEFAULT_CARDS_PATH = Path(__file__).resolve().parent / "data" / "default-cards.json"

_CARDS_BY_ID: Dict[str, Dict] = {}
_CARDS_BY_NAME: Dict[str, Dict] = {}

#: Result type for ``fetch_card_info`` and queue entries
CardInfo = Dict[str, str]

# Queue für gescannte Karten
SCANNER_QUEUE: Queue[CardInfo] = Queue()


def _load_card_database() -> None:
    """Load the local card database if available."""
    if _CARDS_BY_ID:
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
    card = _CARDS_BY_ID.get(card_id)
    if card:
        return {
            "name": card.get("name", ""),
            "set_code": card.get("set", ""),
            "language": card.get("lang", ""),
            "cardmarket_id": card.get("id", ""),
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
    card = _CARDS_BY_NAME.get(name.lower())
    if card:
        return {
            "name": card.get("name", name),
            "set_code": card.get("set", ""),
            "language": card.get("lang", ""),
            "cardmarket_id": card.get("id", ""),
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
    }


def autocomplete_names(query: str) -> list[str]:
    """Return card name suggestions from the local database or Scryfall."""
    _load_card_database()
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
