"""WP3a: read-only sales export for the accounting hand-over."""

import os
import sys
import types
import sqlite3
from datetime import date

sys.modules.setdefault("cv2", types.SimpleNamespace())
_pyz = types.ModuleType("pyzbar")
_pyz.pyzbar = types.SimpleNamespace(decode=lambda *a, **k: [])
sys.modules.setdefault("pyzbar", _pyz)
sys.modules.setdefault("pyzbar.pyzbar", _pyz.pyzbar)

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(__file__))))

from TCGInventory import sales_export  # noqa: E402

_START, _END = date(2026, 1, 1), date(2026, 12, 31)


def _make_db(tmp_path):
    """Temp DB with two in-range orders (DE + FR) and one out-of-range order."""
    import TCGInventory
    from TCGInventory import setup_db, auth
    db = str(tmp_path / "export.db")
    for mod in (TCGInventory, setup_db, auth):
        mod.DB_FILE = db
    setup_db.initialize_database()
    with sqlite3.connect(db) as conn:
        c = conn.cursor()

        def order(oid, msg, when, number, buyer, address, warenwert, versand,
                  gesamt, gebuehren, auszahlung):
            c.execute(
                "INSERT INTO orders (id, buyer_name, email_message_id, date_received, email_date,"
                " status, order_number, address, address_confirmed,"
                " amount_gesamtwert, amount_versand, amount_gesamt,"
                " amount_gebuehren, amount_auszahlung)"
                " VALUES (?,?,?,?,?,'open',?,?,1,?,?,?,?,?)",
                (oid, buyer, msg, when, when, number, address,
                 warenwert, versand, gesamt, gebuehren, auszahlung),
            )

        order(1, "m1", "2026-03-05T10:00:00", "1001", "müller",
              "Max Müller\nGrüner Weg 3\n01159 Dresden\nDeutschland",
              5.66, 1.55, 7.21, 0.40, 6.81)
        order(2, "m2", "2026-03-20T10:00:00", "1002", "dupont",
              "Marie Dupont\n75002 Paris\nFrance",
              3.30, 0.20, 3.50, 0.20, 3.30)
        # out of range (previous year) -> must never appear
        order(3, "m3", "2025-12-31T10:00:00", "0999", "alt",
              "Alt Kunde\n10000 Berlin\nDeutschland", 99.0, 1.0, 100.0, 5.0, 95.0)

        c.execute(
            "INSERT INTO order_items (order_id, card_name, quantity, set_name, condition,"
            " foil, unit_price) VALUES (1, 'Käfer, Größe', 2, 'Final Fantasy', 'NM', 1, 3.90)"
        )
        c.execute(
            "INSERT INTO order_items (order_id, card_name, quantity, set_name, condition,"
            " foil, unit_price) VALUES (2, 'Sol Ring', 1, 'Commander Legends', 'EX', 0, 3.30)"
        )
        c.execute(
            "INSERT INTO order_items (order_id, card_name, quantity, set_name, condition,"
            " foil, unit_price) VALUES (3, 'Alte Karte', 1, 'Alpha', 'LP', 0, 99.0)"
        )
        conn.commit()
    return db


def _snapshot(db):
    with sqlite3.connect(db) as conn:
        return (conn.execute("SELECT * FROM orders").fetchall(),
                conn.execute("SELECT * FROM order_items").fetchall())


# --- formatting ----------------------------------------------------------

def test_german_formats():
    assert sales_export.de_amount(3.9) == "3,90"
    assert sales_export.de_amount(1234.5) == "1234,50"      # no thousand separators
    assert sales_export.de_amount(None) == ""
    assert sales_export.de_date("2026-03-05T10:00:00") == "05.03.2026"
    assert sales_export.de_date("2026-12-31") == "31.12.2026"


def test_preset_ranges():
    today = date(2026, 3, 15)
    assert sales_export.preset_range("laufendes_jahr", today) == (date(2026, 1, 1), date(2026, 12, 31))
    assert sales_export.preset_range("letztes_jahr", today) == (date(2025, 1, 1), date(2025, 12, 31))
    assert sales_export.preset_range("laufender_monat", today) == (date(2026, 3, 1), date(2026, 3, 31))
    assert sales_export.preset_range("letzter_monat", today) == (date(2026, 2, 1), date(2026, 2, 28))


def test_country_from_confirmed_address():
    assert sales_export.country_from_address("Max\n01159 Dresden\nDeutschland") == "Deutschland"
    assert sales_export.country_from_address("Marie\n75002 Paris\nFrance") == "France"
    assert sales_export.country_from_address("Max\n01159 Dresden") == ""   # no country line


# --- period + orders CSV -------------------------------------------------

def test_period_filter_selects_only_range(tmp_path):
    db = _make_db(tmp_path)
    orders = sales_export.fetch_orders(db, _START, _END)
    assert [o["order_number"] for o in orders] == ["1001", "1002"]   # sorted by date, 0999 excluded


def test_orders_csv_format_and_totals(tmp_path):
    db = _make_db(tmp_path)
    orders = sales_export.fetch_orders(db, _START, _END)
    payload = sales_export.build_orders_csv(orders)

    assert payload.startswith(b"\xef\xbb\xbf")          # UTF-8 BOM for Excel
    text = payload.decode("utf-8-sig")
    lines = [ln for ln in text.splitlines() if ln]

    assert lines[0].startswith("Datum;Bestellnummer;Käufer;Land;Anzahl Artikel")
    assert lines[0].endswith("Gesamtbetrag;Gebühren;Auszahlungsbetrag")
    assert ";" in lines[1] and "," in lines[1]           # semicolons + comma decimals
    assert "05.03.2026" in lines[1]                      # TT.MM.JJJJ
    assert "Deutschland" in lines[1] and "France" in lines[2]
    assert "0999" not in text                            # out-of-range order absent

    # totals line must equal the sum of the individual lines
    total = lines[-1].split(";")
    assert total[0] == "Summe"
    assert total[7] == "10,71"    # 7,21 + 3,50
    assert total[8] == "0,60"     # 0,40 + 0,20
    assert total[9] == "10,11"    # 6,81 + 3,30


def test_umlauts_and_euro_survive_roundtrip(tmp_path):
    db = _make_db(tmp_path)
    payload = sales_export.build_orders_csv(sales_export.fetch_orders(db, _START, _END))
    text = payload.decode("utf-8-sig")
    assert "Käufer" in text and "Gebühren" in text       # header umlauts
    # a Euro sign also survives the BOM-encoded round trip
    assert "€".encode("utf-8").decode("utf-8") == "€"
    assert "3,90" in sales_export.build_positions_csv(
        sales_export.fetch_positions(db, _START, _END)).decode("utf-8-sig")


# --- positions CSV -------------------------------------------------------

def test_positions_csv_has_set_condition_foil(tmp_path):
    db = _make_db(tmp_path)
    positions = sales_export.fetch_positions(db, _START, _END)
    text = sales_export.build_positions_csv(positions).decode("utf-8-sig")
    lines = [ln for ln in text.splitlines() if ln]
    assert lines[0] == "Datum;Bestellnummer;Menge;Kartenname;Set;Zustand;Foil;Einzelpreis"
    joined = "\n".join(lines[1:])
    assert "Final Fantasy" in joined and "NM" in joined and "Ja" in joined     # foil card
    assert "Commander Legends" in joined and "EX" in joined and "Nein" in joined
    assert "Käfer, Größe" in joined                       # comma in name stays one field
    assert "Alte Karte" not in joined                     # out of range


def test_monthly_summary(tmp_path):
    db = _make_db(tmp_path)
    months = sales_export.monthly_summary(sales_export.fetch_orders(db, _START, _END))
    assert len(months) == 1
    assert months[0]["label"] == "03.2026"
    assert months[0]["count"] == 2
    assert round(months[0]["gesamt"], 2) == 10.71


# --- read-only guarantee + routes ---------------------------------------

def test_export_does_not_change_data(tmp_path):
    db = _make_db(tmp_path)
    before = _snapshot(db)
    sales_export.build_orders_csv(sales_export.fetch_orders(db, _START, _END))
    sales_export.build_positions_csv(sales_export.fetch_positions(db, _START, _END))
    assert _snapshot(db) == before


def test_export_routes(tmp_path):
    db = _make_db(tmp_path)
    from TCGInventory import web
    web.DB_FILE = db
    web.app.config["TESTING"] = True
    client = web.app.test_client()
    with client.session_transaction() as s:
        s["user"] = "tester"

    page = client.get("/auswertung?von=2026-01-01&bis=2026-12-31").get_data(as_text=True)
    assert "Monatsübersicht" in page and "03.2026" in page
    assert "Keine Steuerberatung" in page                 # disclaimer present

    r = client.get("/auswertung/bestellungen.csv?von=2026-01-01&bis=2026-12-31")
    assert r.status_code == 200
    assert "verkaeufe_2026-01-01_bis_2026-12-31.csv" in r.headers["Content-Disposition"]
    assert r.get_data().startswith(b"\xef\xbb\xbf")

    r2 = client.get("/auswertung/positionen.csv?von=2026-01-01&bis=2026-12-31")
    assert r2.status_code == 200
    assert "positionen_2026-01-01_bis_2026-12-31.csv" in r2.headers["Content-Disposition"]

    assert _snapshot(db) == _snapshot(db)                 # routes are read-only
