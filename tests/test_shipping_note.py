"""WP2c: branded shipping-note (Beileger) PDF generation + panel route tests."""

import os
import sys
import types
import sqlite3

import pytest

sys.modules.setdefault("cv2", types.SimpleNamespace())
_pyz = types.ModuleType("pyzbar")
_pyz.pyzbar = types.SimpleNamespace(decode=lambda *a, **k: [])
sys.modules.setdefault("pyzbar", _pyz)
sys.modules.setdefault("pyzbar.pyzbar", _pyz.pyzbar)

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(__file__))))

from datetime import date  # noqa: E402
from TCGInventory.shipping_note import (  # noqa: E402
    render_shipping_note, get_shop_config, _eur, _greeting, _format_date, detect_language,
    ADDRESS_TOP_MM, ADDRESS_LEFT_MM, ADDRESS_WIDTH_MM,
)


def _text(pdf_bytes):
    from io import BytesIO
    import importlib
    pypdf = importlib.import_module("pypdf")
    return pypdf.PdfReader(BytesIO(pdf_bytes)).pages[0].extract_text()


def test_greeting_sanitizes_garbage_buyer():
    assert _greeting("gaulix") == "Hallo gaulix,"
    assert _greeting("Obi83") == "Hallo Obi83,"
    assert _greeting("die Bestellung stornieren.") == "Hallo,"   # sentence -> neutral
    assert _greeting("") == "Hallo,"
    assert _greeting("Unknown Buyer") == "Hallo,"                # has space -> neutral


def test_config_has_real_sender_address():
    cfg = get_shop_config()
    assert cfg["street"] == "Iltisweg 7"
    assert cfg["zip_city"] == "24983 Handewitt"
    assert cfg["city"] == "Handewitt"
    assert cfg["footer_sender_line"].endswith("Deutschland")
    assert cfg["badge_path"].endswith("cardmarket_seal.png")


def test_footer_has_badge_and_no_contact_line():
    pytest.importorskip("pypdf")
    pdf = render_shipping_note(
        recipient_lines=["Max Mustermann", "01159 Dresden"],
        order_number="1286648200", buyer_name="gaulix",
        positions=[{"quantity": 1, "name": "Sol Ring", "set_name": "CMR",
                    "condition": "NM", "unit_price": 2.0}],
        totals={"shipping": 1.55},
    )
    text = _text(pdf)
    # v3 footer: seal + thank-you + return address, no e-mail contact line.
    assert "kontakt@zurfestung.de" not in text
    assert "Iltisweg 7" in text and "Deutschland" in text


def test_totals_are_consistent_from_item_prices():
    pypdf = pytest.importorskip("pypdf")  # noqa: F841
    pdf = render_shipping_note(
        recipient_lines=["Jan de Vries", "1234 AB Amsterdam", "NIEDERLANDE"],
        order_number="1287799674",
        buyer_name="die Bestellung stornieren.",  # buggy -> must not appear
        positions=[
            {"quantity": 1, "name": "Deadly Dispute", "set_name": "FF", "condition": "NM", "unit_price": 0.25},
            {"quantity": 1, "name": "Nasty End", "set_name": "LOTR", "condition": "EX", "unit_price": 0.02},
        ],
        totals={"shipping": 1.55},   # subtotal/total derived from the items
        lang="de",
    )
    text = _text(pdf)
    assert "die Bestellung stornieren" not in text
    assert "Hallo," in text
    assert "0,27" in text and "1,55" in text and "1,82" in text  # 0,27 + 1,55 = 1,82

_POSITIONS = [
    {"quantity": 1, "name": "Ezio Auditore da Firenze (V.1)",
     "set_name": "Universes Beyond: Assassin's Creed", "condition": "Near Mint",
     "unit_price": 3.90, "foil": False},
    {"quantity": 1, "name": "Matoya, Archon Elder", "set_name": "Final Fantasy",
     "condition": "Near Mint", "unit_price": 0.11, "foil": False},
    {"quantity": 1, "name": "Bayek of Siwa", "set_name": "Assassin's Creed",
     "condition": "Near Mint", "unit_price": 1.65, "foil": True},
]
_RECIPIENT = ["Max Mustermann", "Rudolf-Renner-Str. 36", "01159 Dresden", "DEUTSCHLAND"]


def _render():
    return render_shipping_note(
        recipient_lines=_RECIPIENT,
        order_number="1286648200",
        positions=_POSITIONS,
        buyer_name="gaulix",
        totals={"subtotal": 5.66, "shipping": 1.55, "total": 7.21},
        date="11.07.2026",
    )


# --- formatting / config -------------------------------------------------

def test_eur_german_format():
    assert _eur(7.21) == "7,21 €"
    assert _eur(0.11) == "0,11 €"
    assert _eur(1234.5) == "1.234,50 €"
    assert _eur(None) == "0,00 €"


def test_din_constants_unchanged():
    assert ADDRESS_LEFT_MM == 20
    assert ADDRESS_WIDTH_MM == 85
    assert ADDRESS_TOP_MM == 45


# --- bilingual (DE / EN) --------------------------------------------------

def test_eur_and_date_per_language():
    assert _eur(3.90, "de") == "3,90 €" and _eur(3.90, "en") == "€3.90"
    assert _eur(1234.5, "de") == "1.234,50 €" and _eur(1234.5, "en") == "€1,234.50"
    assert _format_date(date(2026, 7, 11), "de") == "11.07.2026"
    assert _format_date(date(2026, 7, 11), "en") == "11 July 2026"


def test_language_detection():
    assert detect_language(["X", "01159 Dresden", "Deutschland"]) == "de"
    assert detect_language(["X", "12 Rue", "75002 Paris", "FRANCE"]) == "en"
    assert detect_language(["X", "1000 NL", "Niederlande"]) == "en"
    assert detect_language(["X", "01159 Dresden"]) == "de"   # no country -> default DE


def test_de_note_drops_country_line():
    pytest.importorskip("pypdf")
    pdf = render_shipping_note(
        ["Max Mustermann", "Rudolf-Renner-Str. 36", "01159 Dresden", "Deutschland"],
        "1286648200", _POSITIONS, buyer_name="gaulix", totals={"shipping": 1.55},
        date=date(2026, 7, 11),
    )
    t = _text(pdf)
    assert "Bestellung 1286648200" in t and "Hallo gaulix" in t
    assert "Zwischensumme" in t and "3,90 €" in t          # German label + format
    assert "DEUTSCHLAND" not in t                          # domestic: no country line
    assert t.count("Deutschland") == 1                     # only footer carries it


def test_en_note_uppercase_country_line():
    pytest.importorskip("pypdf")
    pdf = render_shipping_note(
        ["Marie Dupont", "12 Rue de la Paix", "75002 Paris", "France"],
        "1286648201", _POSITIONS, buyer_name="mdupont", totals={"shipping": 2.10},
        date=date(2026, 7, 11),
    )
    t = _text(pdf)
    assert "Order 1286648201" in t and "Hello mdupont" in t
    assert "Subtotal" in t and "Total" in t
    assert "€3.90" in t                                    # English number format
    assert "FRANCE" in t                                   # foreign: uppercase country
    assert "11 July 2026" in t


def test_config_short_sender_and_env(monkeypatch):
    cfg = get_shop_config()
    # Sender line uses the SHORT brand so it fits the envelope window; no contact.
    assert cfg["sender_line"] == "Zur Festung · Iltisweg 7 · 24983 Handewitt"
    assert "contact_line" not in cfg
    monkeypatch.setenv("SHOP_STREET", "Neuer Weg 3")
    monkeypatch.setenv("SHOP_ZIP_CITY", "10000 Berlin")
    cfg2 = get_shop_config()
    assert "Neuer Weg 3" in cfg2["sender_line"] and "10000 Berlin" in cfg2["sender_line"]


# --- PDF generation ------------------------------------------------------

def test_render_produces_valid_pdf():
    pdf = _render()
    assert pdf[:5] == b"%PDF-"
    assert b"%%EOF" in pdf[-8:]
    assert len(pdf) > 3000  # includes embedded fonts + logo


def test_pdf_content_extractable():
    """When pypdf is available, verify the expected content is really on the page."""
    pypdf = pytest.importorskip("pypdf")
    from io import BytesIO
    reader = pypdf.PdfReader(BytesIO(_render()))
    page_text = reader.pages[0].extract_text()
    assert "1286648200" in page_text
    assert "Max Mustermann" in page_text
    for pos in _POSITIONS:
        assert pos["name"].split(" (")[0] in page_text
        assert pos["set_name"] in page_text
    assert "7,21" in page_text and "€" in page_text  # correct total, Euro sign


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
            "order_number, address, address_confirmed, amount_gesamtwert, amount_versand, amount_gesamt) "
            "VALUES ('gaulix','m1','2026-07-09T10:00:00','open','1286648200',"
            "'Max Mustermann\n01159 Dresden', 0, 5.66, 1.55, 7.21)"
        )
        oid = c.lastrowid
        c.execute(
            "INSERT INTO order_items (order_id, card_name, quantity, set_name, condition, "
            "unit_price, foil, card_id, match_status) "
            "VALUES (?, 'Matoya, Archon Elder', 1, 'Final Fantasy', 'Near Mint', 0.11, 0, NULL, 'unresolved')",
            (oid,),
        )
        conn.commit()
    web.app.config["TESTING"] = True
    client = web.app.test_client()
    with client.session_transaction() as s:
        s["user"] = "tester"
    return db, oid, client


def test_shipping_note_blocked_until_confirmed(tmp_path):
    _, oid, client = _setup(tmp_path)
    r = client.get(f"/orders/{oid}/shipping_note")
    assert r.status_code == 302
    assert "application/pdf" not in r.headers.get("Content-Type", "")


def test_shipping_note_after_confirm(tmp_path):
    db, oid, client = _setup(tmp_path)
    client.post(f"/orders/{oid}/address", data={"address": "Max Mustermann\n01159 Dresden"})
    with sqlite3.connect(db) as conn:
        assert conn.execute("SELECT address_confirmed FROM orders WHERE id=?", (oid,)).fetchone()[0] == 1
    r = client.get(f"/orders/{oid}/shipping_note")
    assert r.status_code == 200
    assert r.headers["Content-Type"].startswith("application/pdf")
    assert r.get_data()[:5] == b"%PDF-"


def test_manual_condition_shows_on_note(tmp_path):
    pytest.importorskip("pypdf")
    db, oid, client = _setup(tmp_path)
    with sqlite3.connect(db) as conn:
        item_id = conn.execute("SELECT id FROM order_items WHERE order_id=?", (oid,)).fetchone()[0]
    # Manually set the condition in the panel...
    client.post(f"/orders/items/{item_id}/condition", data={"condition": "LP"})
    with sqlite3.connect(db) as conn:
        assert conn.execute("SELECT condition FROM order_items WHERE id=?", (item_id,)).fetchone()[0] == "LP"
    # ...and it must appear on the printed note.
    client.post(f"/orders/{oid}/address", data={"address": "Max Mustermann\n01159 Dresden"})
    text = _text(client.get(f"/orders/{oid}/shipping_note").get_data())
    assert "LP" in text
    assert "Matoya, Archon Elder" in text


def test_language_override_switches_note_to_english(tmp_path):
    pytest.importorskip("pypdf")
    db, oid, client = _setup(tmp_path)
    # German recipient -> would auto-detect DE; override to EN.
    client.post(f"/orders/{oid}/address", data={"address": "Max Mustermann\n01159 Dresden\nDeutschland"})
    client.post(f"/orders/{oid}/language", data={"language": "en"})
    with sqlite3.connect(db) as conn:
        assert conn.execute("SELECT print_language FROM orders WHERE id=?", (oid,)).fetchone()[0] == "en"
    text = _text(client.get(f"/orders/{oid}/shipping_note").get_data())
    assert "Order" in text and "Hello" in text          # English texts on the note
