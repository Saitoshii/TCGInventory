"""WP2a panel tests: ingestion idempotency, candidates, assignment, sold."""

import os
import sys
import types
import sqlite3

sys.modules.setdefault("cv2", types.SimpleNamespace())
_pyz = types.ModuleType("pyzbar")
_pyz.pyzbar = types.SimpleNamespace(decode=lambda *a, **k: [])
sys.modules.setdefault("pyzbar", _pyz)
sys.modules.setdefault("pyzbar.pyzbar", _pyz.pyzbar)

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(__file__))))

import TCGInventory  # noqa: E402
from TCGInventory import web, auth, setup_db, order_service, lager_manager  # noqa: E402
from TCGInventory.email_parser import parse_order_email  # noqa: E402

_ORDER = """Bestellnummer: 1250416803
Käufer: KohlkopfKlaus
Status: Bezahlt

Max Mustermann
Musterstraße 12
12345 Musterstadt
Deutschland

Sendungsverfolgung:

1x Rumble Arena (Avatar: The Last Airbender) - C - Englisch - NM 0,03 EUR
1x Matoya, Archon Elder (FINAL FANTASY) - R - Englisch - NM 0,11 EUR
1x Ezio Auditore da Firenze (V.1) (Universes Beyond: Assassin's Cre... 3,90 EUR

Gesamtwert: 4,04 EUR
Versandkosten: 1,00 EUR
Gesamtbetrag: 5,04 EUR
"""


def _setup(tmp_path):
    db = str(tmp_path / "orders.db")
    for mod in (TCGInventory, web, auth, setup_db, order_service, lager_manager):
        mod.DB_FILE = db
    setup_db.initialize_database()
    with sqlite3.connect(db) as conn:
        c = conn.cursor()
        for name, sc, lang, storage, qty in [
            ("Rumble Arena", "tla", "en", "O01-S01-P1", 1),
            ("Matoya, Archon Elder", "fin", "en", "O02-S01-P1", 2),
            ("Ezio Auditore da Firenze", "acr", "en", "O03-S01-P1", 1),
        ]:
            c.execute(
                "INSERT INTO cards (name,set_code,language,condition,price,quantity,storage_code,"
                "status,collector_number,image_url,foil,item_type,date_added) "
                "VALUES (?,?,?,?,?,?,?,'verfügbar','1',?,0,'card','2026-07-09T10:00:00')",
                (name, sc, lang, "NM", 1.0, qty, storage, "img-" + name),
            )
        conn.commit()
    svc = order_service.OrderIngestionService()
    parsed = parse_order_email(_ORDER, "msg-1", subject="für KohlkopfKlaus: Bitte versenden")
    assert svc._save_order(parsed) is True
    web.app.config["TESTING"] = True
    client = web.app.test_client()
    with client.session_transaction() as s:
        s["user"] = "tester"
    return db, svc, parsed, client


def _card(db, name):
    with sqlite3.connect(db) as conn:
        return conn.execute("SELECT quantity, status FROM cards WHERE name=?", (name,)).fetchone()


def test_ingestion_is_idempotent(tmp_path):
    db, svc, parsed, _ = _setup(tmp_path)
    assert svc._save_order(parsed) is False
    with sqlite3.connect(db) as conn:
        assert conn.execute("SELECT COUNT(*) FROM orders").fetchone()[0] == 1


def test_panel_shows_storage_candidates_and_address(tmp_path):
    _, _, _, client = _setup(tmp_path)
    body = client.get("/orders").get_data(as_text=True)
    assert "O01-S01-P1" in body          # matched position shows storage
    assert "Kandidaten" in body           # unresolved Ezio offers candidate selection
    assert "Max Mustermann" in body       # address prefilled and editable


def test_assign_candidate_links_card(tmp_path):
    db, _, _, client = _setup(tmp_path)
    with sqlite3.connect(db) as conn:
        item_id = conn.execute(
            "SELECT id FROM order_items WHERE card_name='Ezio Auditore da Firenze'"
        ).fetchone()[0]
        card_id = conn.execute(
            "SELECT id FROM cards WHERE name='Ezio Auditore da Firenze'"
        ).fetchone()[0]
    client.post(f"/orders/items/{item_id}/assign", data={"card_id": str(card_id)})
    with sqlite3.connect(db) as conn:
        status, cid = conn.execute(
            "SELECT match_status, card_id FROM order_items WHERE id=?", (item_id,)
        ).fetchone()
    assert status == "matched" and cid == card_id


def test_sold_decrements_exact_card_and_is_idempotent(tmp_path):
    db, _, _, client = _setup(tmp_path)
    order_id = 1
    client.post(f"/orders/{order_id}/mark_sold")
    assert _card(db, "Rumble Arena") == (0, "archiviert")   # 1 -> 0 archived
    assert _card(db, "Matoya, Archon Elder")[0] == 1        # 2 -> 1
    # second click must not decrement again
    client.post(f"/orders/{order_id}/mark_sold")
    assert _card(db, "Matoya, Archon Elder")[0] == 1
    with sqlite3.connect(db) as conn:
        assert conn.execute("SELECT COUNT(*) FROM orders WHERE status='open'").fetchone()[0] == 0


def test_address_update(tmp_path):
    db, _, _, client = _setup(tmp_path)
    client.post("/orders/1/address", data={"address": "Neue Adresse 5\n99999 Stadt"})
    with sqlite3.connect(db) as conn:
        addr = conn.execute("SELECT address FROM orders WHERE id=1").fetchone()[0]
    assert "Neue Adresse 5" in addr
