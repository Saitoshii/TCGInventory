"""Helper utilities for scanning barcodes from card images."""

from __future__ import annotations

from pathlib import Path
from queue import Queue
from typing import Dict, Optional

import cv2
from pyzbar.pyzbar import decode
import requests

SCRYFALL_API_URL = "https://api.scryfall.com/cards/"

#: Result type for ``fetch_card_info`` and queue entries
CardInfo = Dict[str, str]

# Queue für gescannte Karten
SCANNER_QUEUE: Queue[CardInfo] = Queue()

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
    """Retrieve card details from Scryfall."""
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
    """Retrieve card details from Scryfall using the card name."""
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

def scan_and_queue(image_path: str) -> None:
    """Scan a card from an image and put its info into the queue."""
    card_id = scan_image(image_path)
    if not card_id:
        return
    info = fetch_card_info(card_id)
    if info:
        SCANNER_QUEUE.put(info)
        print(f"📸 Karte '{info['name']}' zur Queue hinzugefügt.")
