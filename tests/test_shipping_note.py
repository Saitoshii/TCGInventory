"""WP2b: shipping-note (Beileger) PDF generation + panel route tests."""

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

from TCGInventory.shipping_note import (  # noqa: E402
    render_shipping_note, get_shop_config, ADDRESS_TOP_MM, ADDRESS_LEFT_MM, ADDRESS_WIDTH_MM,
)


# --- generator -----------------------------------------------------------

def test_render_produces_valid_pdf_with_content():
    pdf = render_shipping_note(
        recipient_lines=["Max Mustermann", "Musterstrasse 12", "12345 Musterstadt", "Deutschland"],
        order_number="1250416803",
        positions=[
            {"quantity": 1, "name": "Rumble Arena", "set_code": "tla", "foil": False},
            {"quantity": 2, "name": "Matoya, Archon Elder", "set_code": "fin", "foil": True},
        ],
        config={"name": "TCG Inventory", "sender_line": "Absender Zeile", "logo_path": ""},
        compress=False,
    )
    assert pdf[:5] == b"%PDF-"
    assert b"%%EOF" in pdf[-8:]
    for needle in (b"Max Mustermann", b"1250416803", b"Rumble Arena",
                   b"Matoya, Archon Elder", b"Beileger zur Bestellung", b"Foil", b"TLA"):
        assert needle in pdf, f"missing in PDF: {needle!r}"


def test_din_constants_documented():
    # The single adjust value + envelope-window geometry are exposed as constants.
    assert ADDRESS_LEFT_MM == 20
    assert ADDRESS_WIDTH_MM == 85
    assert ADDRESS_TOP_MM == 45


def test_config_is_env_overridable(monkeypatch):
    monkeypatch.setenv("SHOP_NAME", "Mein Laden")
    monkeypatch.setenv("SHOP_SENDER_LINE", "Mein Laden · Weg 1 · 1 Ort")
    cfg = get_shop_config()
    assert cfg["name"] == "Mein Laden"
    assert "Weg 1" in cfg["sender_line"]


# --- panel route ---------------------------------------------------------

def _setup(tmp_path):
    import TCGInventory
    from TCGInventory import web, auth, setup_db, order_service, lager_manager
    db = str(tmp_path / "note.db")
    for mod in (TCGInventory, web, auth, setup_db, order_service, lager_manager):
        mod.DB_FILE = db
    setup_db.initialize_database()
    with sqlite3.connect(db) as conn:
        c = conn.cursor()
        c.execute(
            "INSERT INTO orders (buyer_name, email_message_id, date_received, status, "
            "order_number, address, address_confirmed) "
            "VALUES ('KohlkopfKlaus','m1','2026-07-09T10:00:00','open','1250416803',"
            "'Max Mustermann\n12345 Musterstadt', 0)"
        )
        oid = c.lastrowid
        c.execute(
            "INSERT INTO order_items (order_id, card_name, quantity, card_id, match_status) "
            "VALUES (?, 'Rumble Arena', 1, NULL, 'unresolved')",
            (oid,),
        )
        conn.commit()
    web.app.config["TESTING"] = True
    client = web.app.test_client()
    with client.session_transaction() as s:
        s["user"] = "tester"
    return db, oid, client


def test_shipping_note_blocked_until_confirmed(tmp_path):
    db, oid, client = _setup(tmp_path)
    # address_confirmed = 0 -> must NOT print, redirect with hint instead.
    r = client.get(f"/orders/{oid}/shipping_note")
    assert r.status_code == 302
    assert "application/pdf" not in r.headers.get("Content-Type", "")


def test_saving_address_confirms_and_enables_print(tmp_path):
    db, oid, client = _setup(tmp_path)
    # Confirm the address via the save route.
    client.post(f"/orders/{oid}/address", data={"address": "Max Mustermann\n12345 Musterstadt"})
    with sqlite3.connect(db) as conn:
        confirmed = conn.execute("SELECT address_confirmed FROM orders WHERE id=?", (oid,)).fetchone()[0]
    assert confirmed == 1

    r = client.get(f"/orders/{oid}/shipping_note")
    assert r.status_code == 200
    assert r.headers["Content-Type"].startswith("application/pdf")
    body = r.get_data()
    assert body[:5] == b"%PDF-"
