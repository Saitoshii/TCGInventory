import os
import sys
import json
import types
from pathlib import Path

# Stub out heavy dependencies used by card_scanner
sys.modules.setdefault('cv2', types.SimpleNamespace())
pyzbar = types.ModuleType('pyzbar')
pyzbar.pyzbar = types.SimpleNamespace(decode=lambda *a, **k: [])
sys.modules.setdefault('pyzbar', pyzbar)
sys.modules.setdefault('pyzbar.pyzbar', pyzbar.pyzbar)

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(__file__))))

import TCGInventory.card_scanner as cs  # noqa: E402


def test_find_variant(tmp_path):
    card_data = [
        {
            "id": "xyz",
            "name": "Sample Card",
            "set": "ABC",
            "lang": "en",
            "collector_number": "007",
            "image_uris": {"normal": "url"},
        }
    ]
    json_file = tmp_path / "cards.json"
    json_file.write_text(json.dumps(card_data))

    cs.DEFAULT_CARDS_PATH = json_file
    cs._DB_CONN = None
    cs._CARDS_BY_ID.clear()
    cs._CARDS_BY_NAME.clear()

    variant = cs.find_variant("Sample Card", "ABC")
    assert variant
    assert variant["collector_number"] == "007"


def _load_identity_fixture(tmp_path):
    """Load a small multi-language fixture into the JSON fallback path."""
    card_data = [
        {"id": "en-1", "name": "Ezio, Brash Novice", "set": "acr", "lang": "en",
         "collector_number": "12", "cardmarket_id": 111, "image_uris": {"normal": "en-url"}},
        {"id": "de-1", "name": "Ezio, dreister Neuling", "set": "acr", "lang": "de",
         "collector_number": "12", "cardmarket_id": 222, "image_uris": {"normal": "de-url"}},
    ]
    json_file = tmp_path / "identity.json"
    json_file.write_text(json.dumps(card_data))
    cs.DEFAULT_CARDS_PATH = json_file
    cs._DB_CONN = None
    cs._CARDS_BY_ID.clear()
    cs._CARDS_BY_NAME.clear()


def test_find_by_identity_language_match(tmp_path):
    """Exact language match returns that printing's canonical IDs."""
    _load_identity_fixture(tmp_path)
    res = cs.find_by_identity("acr", "12", "de")
    assert res is not None
    assert res["scryfall_id"] == "de-1"
    assert res["cardmarket_id"] == "222"
    assert res["name"] == "Ezio, dreister Neuling"


def test_find_by_identity_falls_back_to_english(tmp_path):
    """A language not present in the local DB falls back to the English printing."""
    _load_identity_fixture(tmp_path)
    res = cs.find_by_identity("acr", "12", "fr")
    assert res is not None
    assert res["scryfall_id"] == "en-1"       # English fallback, deterministic


def test_find_by_identity_case_insensitive_set(tmp_path):
    """Set code lookup is case-insensitive (Dragonshield 'ACR' vs Scryfall 'acr')."""
    _load_identity_fixture(tmp_path)
    res = cs.find_by_identity("ACR", "12", "en")
    assert res is not None
    assert res["scryfall_id"] == "en-1"


def test_find_by_identity_unknown_returns_none(tmp_path):
    """Unknown (set, collector) yields None -> caller routes to Needs-Review."""
    _load_identity_fixture(tmp_path)
    assert cs.find_by_identity("zzz", "999", "en") is None

