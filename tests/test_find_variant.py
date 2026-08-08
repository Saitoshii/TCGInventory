"""Kartensuche gegen die lokale Scryfall-Datenbank.

Getestet wird der Weg, der auch produktiv läuft: die aufbereitete SQLite-Datei.
Die rohe Bulk-JSON wird bewusst nicht mehr geladen — sie ginge vollständig in
den Arbeitsspeicher und wäre auf dem Raspberry Pi nicht tragbar.
"""

import os
import sys
import sqlite3
import types

# Stub out heavy dependencies used by card_scanner
sys.modules.setdefault('cv2', types.SimpleNamespace())
pyzbar = types.ModuleType('pyzbar')
pyzbar.pyzbar = types.SimpleNamespace(decode=lambda *a, **k: [])
sys.modules.setdefault('pyzbar', pyzbar)
sys.modules.setdefault('pyzbar.pyzbar', pyzbar.pyzbar)

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(__file__))))

import TCGInventory.card_scanner as cs  # noqa: E402
from TCGInventory import build_card_db  # noqa: E402


def _baue_db(tmp_path, karten):
    """Kleine Kartendatenbank über den echten Importweg erzeugen."""
    db = tmp_path / "default-cards.db"
    build_card_db.schreibe_datenbank(karten, db)
    cs.reset_card_database()
    cs.DEFAULT_DB_PATH = db
    return db


def _identity_fixture(tmp_path):
    return _baue_db(tmp_path, [
        {"id": "en-1", "name": "Ezio, Brash Novice", "set": "acr", "lang": "en",
         "collector_number": "12", "cardmarket_id": 111},
        {"id": "de-1", "name": "Ezio, dreister Neuling", "set": "acr", "lang": "de",
         "collector_number": "12", "cardmarket_id": 222},
    ])


def test_find_variant(tmp_path):
    _baue_db(tmp_path, [
        {"id": "xyz", "name": "Sample Card", "set": "ABC", "lang": "en",
         "collector_number": "007"},
    ])
    variant = cs.find_variant("Sample Card", "ABC")
    assert variant
    assert variant["collector_number"] == "007"


def test_find_by_identity_language_match(tmp_path):
    """Exact language match returns that printing's canonical IDs."""
    _identity_fixture(tmp_path)
    res = cs.find_by_identity("acr", "12", "de")
    assert res is not None
    assert res["scryfall_id"] == "de-1"
    assert res["cardmarket_id"] == "222"
    assert res["name"] == "Ezio, dreister Neuling"


def test_find_by_identity_falls_back_to_english(tmp_path):
    """A language not present in the local DB falls back to the English printing."""
    _identity_fixture(tmp_path)
    res = cs.find_by_identity("acr", "12", "fr")
    assert res is not None
    assert res["scryfall_id"] == "en-1"       # English fallback, deterministic


def test_find_by_identity_case_insensitive_set(tmp_path):
    """Set code lookup is case-insensitive (Dragonshield 'ACR' vs Scryfall 'acr')."""
    _identity_fixture(tmp_path)
    res = cs.find_by_identity("ACR", "12", "en")
    assert res is not None
    assert res["scryfall_id"] == "en-1"


def test_find_by_identity_unknown_returns_none(tmp_path):
    """Unknown (set, collector) yields None -> caller routes to Needs-Review."""
    _identity_fixture(tmp_path)
    assert cs.find_by_identity("zzz", "999", "en") is None


def test_image_url_is_derived_from_id(tmp_path):
    """Die Bildadresse wird nicht gespeichert, sondern aus der ID gebildet."""
    _identity_fixture(tmp_path)
    res = cs.find_by_identity("acr", "12", "en")
    assert res["image_url"] == (
        "https://cards.scryfall.io/normal/front/e/n/en-1.jpg")

    with sqlite3.connect(str(cs.DEFAULT_DB_PATH)) as conn:
        spalten = [r[1] for r in conn.execute("PRAGMA table_info(cards)")]
    assert "image_url" not in spalten          # Spalte entfällt, spart Platz
