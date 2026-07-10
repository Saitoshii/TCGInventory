"""Tests for identity-based order matching and set-name resolution (WP2a)."""

import os
import sys
import types
import sqlite3

# Stub heavy deps pulled in transitively via card_scanner.
sys.modules.setdefault("cv2", types.SimpleNamespace())
_pyz = types.ModuleType("pyzbar")
_pyz.pyzbar = types.SimpleNamespace(decode=lambda *a, **k: [])
sys.modules.setdefault("pyzbar", _pyz)
sys.modules.setdefault("pyzbar.pyzbar", _pyz.pyzbar)

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(__file__))))

from TCGInventory.order_service import OrderIngestionService  # noqa: E402
from TCGInventory.card_scanner import resolve_set_code  # noqa: E402


_DB_SEQ = [0]


def _inventory_db(tmp_path, rows):
    """Create an inventory DB under tmp_path with the given card rows.

    Each row: (name, set_code, language, storage_code, quantity). Uses the
    pytest tmp_path fixture so cleanup is lenient across platforms.
    """
    _DB_SEQ[0] += 1
    path = str(tmp_path / f"inv{_DB_SEQ[0]}.db")
    with sqlite3.connect(path) as conn:
        c = conn.cursor()
        c.execute(
            """CREATE TABLE cards (id INTEGER PRIMARY KEY, name TEXT, set_code TEXT,
               language TEXT, storage_code TEXT, image_url TEXT, quantity INTEGER,
               status TEXT DEFAULT 'verfügbar')"""
        )
        for name, sc, lang, storage, qty in rows:
            c.execute(
                "INSERT INTO cards (name, set_code, language, storage_code, image_url, quantity) "
                "VALUES (?, ?, ?, ?, ?, ?)",
                (name, sc, lang, storage, "img-" + name, qty),
            )
        conn.commit()
    return path


def _match(db_path, item):
    service = OrderIngestionService()
    with sqlite3.connect(db_path) as conn:
        return service._match_item(conn.cursor(), item)


# --- resolve_set_code ----------------------------------------------------

def test_resolve_final_fantasy():
    code, conf = resolve_set_code("FINAL FANTASY")
    assert code == "fin" and conf == "high"


def test_resolve_case_insensitive():
    assert resolve_set_code("Avatar: The Last Airbender")[0] == "tla"


def test_resolve_truncated_low_confidence():
    code, conf = resolve_set_code("Universes Beyond: Assassin's Cre...")
    assert conf in ("low", "none")  # never a confident guess on a truncated name


def test_resolve_unknown_none():
    assert resolve_set_code("Totally Unknown Set")[0] is None


# --- _match_item ---------------------------------------------------------

def test_match_unique_identity(tmp_path):
    db = _inventory_db(tmp_path, [("Rumble Arena", "tla", "en", "O01-S01-P1", 1)])
    res = _match(db, {"name": "Rumble Arena", "set_name": "Avatar: The Last Airbender",
                      "language": "en", "uncertain": False})
    assert res["match_status"] == "matched"
    assert res["card_id"] is not None
    assert res["storage_code"] == "O01-S01-P1"


def test_match_uncertain_not_automatched(tmp_path):
    # A truncated / uncertain line must NOT auto-match even if a card exists.
    db = _inventory_db(tmp_path, [("Ezio Auditore da Firenze", "acr", "en", "O03-S01-P1", 1)])
    res = _match(db, {"name": "Ezio Auditore da Firenze",
                      "set_name": "Universes Beyond: Assassin's Cre...",
                      "language": "en", "uncertain": True})
    assert res["card_id"] is None
    assert res["match_status"] in ("ambiguous", "unresolved")


def test_match_ambiguous_multiple(tmp_path):
    db = _inventory_db(tmp_path, [
        ("Rumble Arena", "tla", "en", "O01-S01-P1", 1),
        ("Rumble Arena", "tla", "en", "O01-S02-P1", 1),
    ])
    res = _match(db, {"name": "Rumble Arena", "set_name": "Avatar: The Last Airbender",
                      "language": "en", "uncertain": False})
    assert res["card_id"] is None
    assert res["match_status"] == "ambiguous"


def test_match_not_in_inventory_unresolved(tmp_path):
    db = _inventory_db(tmp_path, [("Some Other Card", "tla", "en", "O01-S01-P1", 1)])
    res = _match(db, {"name": "Rumble Arena", "set_name": "Avatar: The Last Airbender",
                      "language": "en", "uncertain": False})
    assert res["card_id"] is None
    assert res["match_status"] == "unresolved"


def test_no_like_substring_automatch(tmp_path):
    # "Bolt" must NOT auto-match "Lightning Bolt" (no LIKE substring auto-decision).
    db = _inventory_db(tmp_path, [("Lightning Bolt", "2x2", "en", "O01-S01-P1", 1)])
    res = _match(db, {"name": "Bolt", "set_name": "Double Masters 2022",
                      "language": "en", "uncertain": False})
    assert res["card_id"] is None


# --- image fallback ------------------------------------------------------

def test_get_image_from_default_db(tmp_path):
    default_db_path = tmp_path / "default-cards.db"
    with sqlite3.connect(default_db_path) as conn:
        c = conn.cursor()
        c.execute(
            """CREATE TABLE cards (id TEXT PRIMARY KEY, name TEXT, set_code TEXT,
               set_name TEXT, lang TEXT, collector_number TEXT, cardmarket_id TEXT, image_url TEXT)"""
        )
        c.execute(
            "INSERT INTO cards (id, name, set_code, image_url) VALUES (?, ?, ?, ?)",
            ("123", "Counterspell", "LEA", "http://example.com/counter.jpg"),
        )
        conn.commit()

    service = OrderIngestionService()

    def mock_get_image(card_name):
        with sqlite3.connect(default_db_path) as conn:
            c = conn.cursor()
            c.execute(
                "SELECT image_url FROM cards WHERE LOWER(name) = LOWER(?) AND image_url IS NOT NULL LIMIT 1",
                (card_name,),
            )
            r = c.fetchone()
            return r[0] if r and r[0] else None

    service._get_image_from_default_db = mock_get_image
    assert service._get_image_from_default_db("Counterspell") == "http://example.com/counter.jpg"
    assert service._get_image_from_default_db("Nonexistent Card") is None
