"""WP3b: append-only Buchungsjournal, Ausgaben/Belege und EÜR-Auswertung."""

import os
import sys
import types
import hashlib
import sqlite3

import pytest

sys.modules.setdefault("cv2", types.SimpleNamespace())
_pyz = types.ModuleType("pyzbar")
_pyz.pyzbar = types.SimpleNamespace(decode=lambda *a, **k: [])
sys.modules.setdefault("pyzbar", _pyz)
sys.modules.setdefault("pyzbar.pyzbar", _pyz.pyzbar)

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(__file__))))

import TCGInventory  # noqa: E402
from TCGInventory import setup_db, auth, bookkeeping  # noqa: E402


def _db(tmp_path):
    db = str(tmp_path / "buch.db")
    for mod in (TCGInventory, setup_db, auth, bookkeeping):
        mod.DB_FILE = db
    setup_db.initialize_database()
    return db


def _order(db, oid=7, number="1001", gesamt=7.21, versand=1.55, gebuehren=0.40,
           status="sold", confirmed=1, datum="2026-03-05T10:00:00", auszahlung=None):
    with sqlite3.connect(db) as c:
        c.execute(
            "INSERT INTO orders (id, buyer_name, email_message_id, date_received, email_date,"
            " status, order_number, address, address_confirmed,"
            " amount_gesamt, amount_versand, amount_gebuehren, amount_auszahlung)"
            " VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (oid, "gaulix", f"m{oid}", datum, datum, status, number,
             "Max Müller\n01159 Dresden\nDeutschland", confirmed,
             gesamt, versand, gebuehren, auszahlung),
        )
        c.commit()
    return oid


# --- Unveränderbarkeit ---------------------------------------------------

def test_bookings_cannot_be_updated_or_deleted(tmp_path):
    db = _db(tmp_path)
    bid = bookkeeping.add_booking("2026-03-01", "ausgabe", "Verpackungsmaterial", 1250, "Kartons")

    for sql in (
        "UPDATE journal SET betrag_cent = 1 WHERE id = ?",
        "UPDATE journal SET kategorie = 'Sonstige Ausgaben' WHERE id = ?",
        "UPDATE journal SET buchungsdatum = '2020-01-01' WHERE id = ?",
        "UPDATE journal SET beschreibung = 'manipuliert' WHERE id = ?",
        "UPDATE journal SET art = 'einnahme' WHERE id = ?",
    ):
        with pytest.raises(sqlite3.IntegrityError):
            with sqlite3.connect(db) as c:
                c.execute(sql, (bid,))

    with pytest.raises(sqlite3.IntegrityError):
        with sqlite3.connect(db) as c:
            c.execute("DELETE FROM journal WHERE id = ?", (bid,))

    with sqlite3.connect(db) as c:
        row = c.execute("SELECT betrag_cent, kategorie FROM journal WHERE id=?", (bid,)).fetchone()
    assert row == (1250, "Verpackungsmaterial")     # unverändert


def test_storno_creates_linked_row(tmp_path):
    db = _db(tmp_path)
    bid = bookkeeping.add_booking("2026-03-01", "ausgabe", "Bürobedarf", 999, "Stifte")
    sid = bookkeeping.storno_booking(bid, "falscher Betrag")

    with sqlite3.connect(db) as c:
        storno = c.execute(
            "SELECT art, kategorie, betrag_cent, storniert_buchung_id FROM journal WHERE id=?",
            (sid,)).fetchone()
        original_link = c.execute(
            "SELECT storniert_durch FROM journal WHERE id=?", (bid,)).fetchone()[0]
    assert storno == ("storno", "Bürobedarf", 999, bid)
    assert original_link == sid                      # Verweispaar in beide Richtungen

    with pytest.raises(ValueError):
        bookkeeping.storno_booking(bid)               # nicht zweimal stornierbar


def test_lfd_nr_is_gapless(tmp_path):
    _db(tmp_path)
    for i in range(5):
        bookkeeping.add_booking("2026-03-01", "ausgabe", "Bürobedarf", 100 + i, f"#{i}")
    bookkeeping.storno_booking(1)
    nrs = sorted(b["lfd_nr"] for b in bookkeeping.list_bookings())
    assert nrs == list(range(1, len(nrs) + 1))


# --- Einnahmen aus Bestellungen -----------------------------------------

def test_order_takeover_creates_three_bookings(tmp_path):
    db = _db(tmp_path)
    _order(db)
    bookkeeping.book_order(7)

    with sqlite3.connect(db) as c:
        rows = c.execute(
            "SELECT kategorie, art, betrag_cent FROM journal WHERE bestellung_id=7 ORDER BY id"
        ).fetchall()
    assert ("Warenverkauf", "einnahme", 566) in rows            # 7,21 − 1,55
    assert ("Vereinnahmte Versandkosten", "einnahme", 155) in rows
    assert ("Cardmarket-Gebühren", "ausgabe", 40) in rows
    # Brutto bleibt sichtbar: Warenverkauf + Versand == Gesamtbetrag
    assert 566 + 155 == bookkeeping.to_cent(7.21)


def test_order_cannot_be_booked_twice(tmp_path):
    db = _db(tmp_path)
    _order(db)
    bookkeeping.book_order(7)
    with pytest.raises(ValueError):
        bookkeeping.book_order(7)
    with sqlite3.connect(db) as c:
        n = c.execute("SELECT COUNT(*) FROM journal WHERE bestellung_id=7").fetchone()[0]
    assert n == 3


def test_unique_index_blocks_duplicate_at_db_level(tmp_path):
    db = _db(tmp_path)
    _order(db)
    bookkeeping.book_order(7)
    with pytest.raises(sqlite3.IntegrityError):
        bookkeeping.add_booking("2026-03-05", "einnahme", "Warenverkauf", 566,
                                "Dublette", bestellung_id=7)


def _add_item(db, order_id, unit_price, qty=1, name="Force of Negation"):
    with sqlite3.connect(db) as c:
        c.execute(
            "INSERT INTO order_items (order_id, card_name, quantity, unit_price, match_status) "
            "VALUES (?,?,?,?, 'matched')",
            (order_id, name, qty, unit_price),
        )
        c.commit()


def test_book_order_uses_item_prices_when_total_missing(tmp_path):
    """Older order without a parsed header total: Warenverkauf comes from the
    actual position prices, shipping + fees from the (manually set) fields."""
    db = _db(tmp_path)
    _order(db, oid=7, gesamt=None, versand=3.95, gebuehren=2.10)
    _add_item(db, 7, unit_price=42.00, qty=1)
    bookkeeping.book_order(7)
    with sqlite3.connect(db) as c:
        rows = c.execute(
            "SELECT kategorie, art, betrag_cent FROM journal WHERE bestellung_id=7 ORDER BY id"
        ).fetchall()
    assert ("Warenverkauf", "einnahme", 4200) in rows
    assert ("Vereinnahmte Versandkosten", "einnahme", 395) in rows
    assert ("Cardmarket-Gebühren", "ausgabe", 210) in rows


def test_book_order_refuses_all_zero(tmp_path):
    """No amounts and no item prices -> refuse instead of booking 0,00."""
    db = _db(tmp_path)
    _order(db, oid=8, number="1002", gesamt=None, versand=None, gebuehren=None)
    with pytest.raises(ValueError):
        bookkeeping.book_order(8)
    with sqlite3.connect(db) as c:
        n = c.execute("SELECT COUNT(*) FROM journal WHERE bestellung_id=8").fetchone()[0]
    assert n == 0


def test_storno_allows_corrected_rebooking(tmp_path):
    """A mistaken takeover can be corrected: storno all bookings, then re-book."""
    db = _db(tmp_path)
    _order(db, oid=7)
    ids = bookkeeping.book_order(7)
    for bid in ids:
        bookkeeping.storno_booking(bid, "Korrektur")
    assert bookkeeping.order_already_booked(7) is False
    assert any(o["id"] == 7 for o in bookkeeping.bookable_orders())
    new_ids = bookkeeping.book_order(7)          # must not collide with the storno'd rows
    assert len(new_ids) == 3
    with sqlite3.connect(db) as c:
        aktiv = c.execute(
            "SELECT COUNT(*) FROM journal WHERE bestellung_id=7 AND art<>'storno' "
            "AND storniert_durch IS NULL"
        ).fetchone()[0]
    assert aktiv == 3


# --- Zufluss / Auswertung ------------------------------------------------

def test_summary_uses_payment_date_and_lists_pending(tmp_path):
    db = _db(tmp_path)
    _order(db)
    bookkeeping.book_order(7)

    before = bookkeeping.summary("2026-01-01", "2026-12-31")
    assert before["summe_einnahmen"] == 0            # noch nicht zugeflossen
    assert before["offen_count"] == 3
    assert before["offen_einnahme"] == 721

    n = bookkeeping.assign_payment_date([7], "2026-04-10")
    assert n == 3
    after = bookkeeping.summary("2026-01-01", "2026-12-31")
    assert after["summe_einnahmen"] == 721
    assert after["ausgaben"]["Cardmarket-Gebühren"] == 40
    assert after["offen_count"] == 0

    # Zahlungseingang liegt 2026 -> im Vorjahr darf nichts erscheinen
    other = bookkeeping.summary("2025-01-01", "2025-12-31")
    assert other["summe_einnahmen"] == 0


def test_storno_changes_sums_correctly(tmp_path):
    _db(tmp_path)
    b1 = bookkeeping.add_booking("2026-05-01", "ausgabe", "Verpackungsmaterial", 1250, "Kartons")
    bookkeeping.add_booking("2026-05-02", "ausgabe", "Bürobedarf", 999, "Stifte")

    r = bookkeeping.summary("2026-01-01", "2026-12-31")
    assert r["summe_ausgaben"] == 1250 + 999

    bookkeeping.storno_booking(b1)
    r2 = bookkeeping.summary("2026-01-01", "2026-12-31")
    assert r2["summe_ausgaben"] == 999                # stornierte Buchung fällt heraus
    assert "Verpackungsmaterial" not in r2["ausgaben"]


def test_cent_amounts_have_no_rounding_errors(tmp_path):
    _db(tmp_path)
    for _ in range(10):
        bookkeeping.add_booking("2026-06-01", "ausgabe", "Sonstige Ausgaben",
                                bookkeeping.to_cent("0,10"), "10 Cent")
    r = bookkeeping.summary("2026-01-01", "2026-12-31")
    assert r["summe_ausgaben"] == 100                 # exakt 1,00 €
    assert bookkeeping.cent_to_de(100) == "1,00"
    assert bookkeeping.to_cent("3,90") == 390 and bookkeeping.to_cent("1.234,56") == 123456


# --- Belege --------------------------------------------------------------

def test_receipt_is_stored_with_checksum(tmp_path, monkeypatch):
    _db(tmp_path)
    monkeypatch.setattr(bookkeeping, "BELEGE_DIR", tmp_path / "belege")
    data = b"%PDF-1.4 Rechnung"
    bid = bookkeeping.save_receipt("Rechnung Mai.pdf", data, "application/pdf")
    info = bookkeeping.get_receipt(bid)
    assert info["sha256"] == hashlib.sha256(data).hexdigest()
    assert info["original_name"] == "Rechnung Mai.pdf"
    assert info["pfad"].exists()
    assert info["pfad"].read_bytes() == data          # unverändert gespeichert

    with pytest.raises(ValueError):
        bookkeeping.save_receipt("virus.exe", b"x")   # nur PDF/JPG/PNG


def test_receipts_cannot_be_deleted(tmp_path, monkeypatch):
    db = _db(tmp_path)
    monkeypatch.setattr(bookkeeping, "BELEGE_DIR", tmp_path / "belege")
    bid = bookkeeping.save_receipt("beleg.png", b"\x89PNG", "image/png")
    with pytest.raises(sqlite3.IntegrityError):
        with sqlite3.connect(db) as c:
            c.execute("DELETE FROM belege WHERE id = ?", (bid,))


# --- CSV + Routen --------------------------------------------------------

def test_summary_csv_german_format(tmp_path):
    db = _db(tmp_path)
    _order(db)
    bookkeeping.book_order(7)
    bookkeeping.assign_payment_date([7], "2026-04-10")
    result = bookkeeping.summary("2026-01-01", "2026-12-31")
    payload = bookkeeping.summary_csv(result, "2026-01-01", "2026-12-31")

    assert payload.startswith(b"\xef\xbb\xbf")        # UTF-8 mit BOM
    text = payload.decode("utf-8-sig")
    assert "01.01.2026 - 31.12.2026" in text          # TT.MM.JJJJ
    assert "Warenverkauf;5,66" in text                # Komma-Dezimaltrenner
    assert "Überschuss" in text and "Gebühren" in text


def test_bookkeeping_routes(tmp_path, monkeypatch):
    db = _db(tmp_path)
    monkeypatch.setattr(bookkeeping, "BELEGE_DIR", tmp_path / "belege")
    _order(db)
    from TCGInventory import web
    web.DB_FILE = db
    web.app.config["TESTING"] = True
    client = web.app.test_client()
    with client.session_transaction() as s:
        s["user"] = "tester"

    page = client.get("/buchhaltung").get_data(as_text=True)
    assert "Buchhaltung" in page and "1001" in page              # übernehmbare Bestellung

    client.post("/buchhaltung/uebernehmen/7")
    with sqlite3.connect(db) as c:
        assert c.execute("SELECT COUNT(*) FROM journal WHERE bestellung_id=7").fetchone()[0] == 3

    # Ausgabe mit Beleg
    import io as _io
    client.post("/buchhaltung/ausgabe", data={
        "buchungsdatum": "2026-05-01", "betrag": "12,90",
        "kategorie": "Verpackungsmaterial", "beschreibung": "Kartons",
        "beleg": (_io.BytesIO(b"%PDF-1.4 x"), "beleg.pdf"),
    }, content_type="multipart/form-data")
    with sqlite3.connect(db) as c:
        row = c.execute("SELECT betrag_cent, beleg_id FROM journal "
                        "WHERE kategorie='Verpackungsmaterial'").fetchone()
    assert row[0] == 1290 and row[1] is not None
    assert client.get(f"/buchhaltung/beleg/{row[1]}").get_data() == b"%PDF-1.4 x"

    # Zahlungseingang zuweisen -> Auswertung rechnet danach
    client.post("/buchhaltung/zahlungseingang", data={"datum": "2026-04-10", "order_ids": ["7"]})
    summary_page = client.get("/buchhaltung/auswertung?von=2026-01-01&bis=2026-12-31").get_data(as_text=True)
    assert "Überschuss" in summary_page and "Keine Steuerberatung" in summary_page

    csv_resp = client.get("/buchhaltung/auswertung.csv?von=2026-01-01&bis=2026-12-31")
    assert csv_resp.status_code == 200
    assert csv_resp.get_data().startswith(b"\xef\xbb\xbf")


# --- Tatsächliches Porto (Ausgabe) beim Übernehmen -----------------------

def test_book_order_books_actual_postage_expense(tmp_path):
    """Das tatsaechlich gezahlte Porto wird zusaetzlich als Ausgabe gebucht;
    der vom Kunden gezahlte Versand bleibt getrennt als Einnahme."""
    db = _db(tmp_path)
    _order(db)  # versand (Kunde) = 1,55
    ids = bookkeeping.book_order(7, porto_cent=180, porto_methode="Großbrief (bis 500 g)")
    assert len(ids) == 4
    with sqlite3.connect(db) as c:
        porto = c.execute(
            "SELECT art, betrag_cent, beschreibung FROM journal "
            "WHERE bestellung_id=7 AND kategorie='Porto/Versand'").fetchone()
        einnahme_versand = c.execute(
            "SELECT betrag_cent FROM journal WHERE bestellung_id=7 "
            "AND kategorie='Vereinnahmte Versandkosten'").fetchone()[0]
    assert porto[0] == "ausgabe" and porto[1] == 180 and "Großbrief" in porto[2]
    assert einnahme_versand == 155           # Kundenversand unveraendert und getrennt


def test_book_order_without_porto_stays_three_bookings(tmp_path):
    db = _db(tmp_path)
    _order(db)
    ids = bookkeeping.book_order(7)           # kein Porto angegeben
    assert len(ids) == 3
    with sqlite3.connect(db) as c:
        n = c.execute("SELECT COUNT(*) FROM journal WHERE bestellung_id=7 "
                      "AND kategorie='Porto/Versand'").fetchone()[0]
    assert n == 0


def _take_client(db):
    from TCGInventory import web
    web.DB_FILE = db
    web.app.config["TESTING"] = True
    client = web.app.test_client()
    with client.session_transaction() as s:
        s["user"] = "tester"
    return client


def test_take_order_route_books_porto_from_manual_amount(tmp_path):
    db = _db(tmp_path)
    _order(db, oid=9, number="1009")
    client = _take_client(db)
    client.post("/buchhaltung/uebernehmen/9",
                data={"porto_methode": "Großbrief (bis 500 g)", "porto_betrag": "1,80"})
    with sqlite3.connect(db) as c:
        row = c.execute("SELECT betrag_cent FROM journal WHERE bestellung_id=9 "
                        "AND kategorie='Porto/Versand'").fetchone()
    assert row and row[0] == 180


def test_take_order_route_uses_suggested_price_when_amount_blank(tmp_path):
    db = _db(tmp_path)
    _order(db, oid=10, number="1010")
    client = _take_client(db)
    client.post("/buchhaltung/uebernehmen/10",
                data={"porto_methode": "Standardbrief (bis 20 g)", "porto_betrag": ""})
    with sqlite3.connect(db) as c:
        row = c.execute("SELECT betrag_cent FROM journal WHERE bestellung_id=10 "
                        "AND kategorie='Porto/Versand'").fetchone()
    assert row and row[0] == 95


# --- Briefmarken-Vorrat (vorab gekauft, keine Doppelbuchung) -------------

def test_buy_stamps_books_once_and_fills_stock(tmp_path):
    db = _db(tmp_path)
    bid = bookkeeping.buy_stamps(95, 10, "2026-05-01")
    with sqlite3.connect(db) as c:
        booking = c.execute("SELECT kategorie, art, betrag_cent FROM journal WHERE id=?",
                            (bid,)).fetchone()
        anzahl = c.execute("SELECT anzahl FROM briefmarken WHERE wert_cent=95").fetchone()[0]
    assert booking == ("Porto/Versand", "ausgabe", 950)   # 10 × 0,95 = 9,50, einmal gebucht
    assert anzahl == 10
    bookkeeping.buy_stamps(95, 5, "2026-05-02")           # gleicher Wert summiert sich
    with sqlite3.connect(db) as c:
        assert c.execute("SELECT anzahl FROM briefmarken WHERE wert_cent=95").fetchone()[0] == 15


def test_use_stamp_decrements_and_never_negative(tmp_path):
    db = _db(tmp_path)
    bookkeeping.buy_stamps(125, 1, "2026-05-01")
    assert bookkeeping.use_stamp(125) is True
    assert bookkeeping.use_stamp(125) is False            # kein Vorrat mehr
    with sqlite3.connect(db) as c:
        assert c.execute("SELECT anzahl FROM briefmarken WHERE wert_cent=125").fetchone()[0] == 0


def test_take_order_from_stock_deducts_without_extra_booking(tmp_path):
    db = _db(tmp_path)
    _order(db, oid=11, number="1011")
    bookkeeping.buy_stamps(95, 3, "2026-05-01")
    client = _take_client(db)
    client.post("/buchhaltung/uebernehmen/11", data={"porto_methode": "vorrat:95"})
    with sqlite3.connect(db) as c:
        porto_on_order = c.execute(
            "SELECT COUNT(*) FROM journal WHERE bestellung_id=11 AND kategorie='Porto/Versand'"
        ).fetchone()[0]
        bestand = c.execute("SELECT anzahl FROM briefmarken WHERE wert_cent=95").fetchone()[0]
        order_bookings = c.execute(
            "SELECT COUNT(*) FROM journal WHERE bestellung_id=11").fetchone()[0]
    assert porto_on_order == 0        # keine Doppelbuchung an der Bestellung
    assert bestand == 2               # eine Marke abgezogen
    assert order_bookings == 3        # Warenverkauf + Versand + Gebühren


def test_stamp_returned_to_stock_on_storno_when_requested(tmp_path):
    db = _db(tmp_path)
    _order(db, oid=11, number="1011")
    bookkeeping.buy_stamps(95, 2, "2026-05-01")     # Bestand 2
    client = _take_client(db)
    client.post("/buchhaltung/uebernehmen/11", data={"porto_methode": "vorrat:95"})
    with sqlite3.connect(db) as c:
        assert c.execute("SELECT anzahl FROM briefmarken WHERE wert_cent=95").fetchone()[0] == 1
        assert c.execute("SELECT porto_briefmarke_cent FROM orders WHERE id=11").fetchone()[0] == 95
        bid = c.execute("SELECT id FROM journal WHERE bestellung_id=11 AND kategorie='Warenverkauf'").fetchone()[0]

    # Storno mit Häkchen -> Marke zurück in den Vorrat, Vermerk gelöscht
    client.post(f"/buchhaltung/storno/{bid}", data={"order_id": "11", "briefmarke_zurueck": "1"})
    with sqlite3.connect(db) as c:
        assert c.execute("SELECT anzahl FROM briefmarken WHERE wert_cent=95").fetchone()[0] == 2
        assert c.execute("SELECT porto_briefmarke_cent FROM orders WHERE id=11").fetchone()[0] is None


def test_stamp_not_returned_when_unchecked(tmp_path):
    db = _db(tmp_path)
    _order(db, oid=12, number="1012")
    bookkeeping.buy_stamps(125, 1, "2026-05-01")
    client = _take_client(db)
    client.post("/buchhaltung/uebernehmen/12", data={"porto_methode": "vorrat:125"})
    with sqlite3.connect(db) as c:
        bid = c.execute("SELECT id FROM journal WHERE bestellung_id=12 AND kategorie='Warenverkauf'").fetchone()[0]
    # Storno ohne Häkchen -> Marke bleibt draußen
    client.post(f"/buchhaltung/storno/{bid}", data={"order_id": "12"})
    with sqlite3.connect(db) as c:
        assert c.execute("SELECT anzahl FROM briefmarken WHERE wert_cent=125").fetchone()[0] == 0
        assert c.execute("SELECT porto_briefmarke_cent FROM orders WHERE id=12").fetchone()[0] == 125


def test_return_order_stamp_only_once(tmp_path):
    db = _db(tmp_path)
    _order(db, oid=13)
    bookkeeping.buy_stamps(95, 1, "2026-05-01")
    bookkeeping.use_stamp(95)
    bookkeeping.set_order_stamp(13, 95)
    assert bookkeeping.return_order_stamp(13) == 95     # zurückgelegt
    assert bookkeeping.return_order_stamp(13) is None   # kein zweites Mal
    with sqlite3.connect(db) as c:
        assert c.execute("SELECT anzahl FROM briefmarken WHERE wert_cent=95").fetchone()[0] == 1


def test_stamps_route_buys_and_shows_stock(tmp_path):
    db = _db(tmp_path)
    client = _take_client(db)
    client.post("/buchhaltung/briefmarken",
                data={"wert": "0,95", "anzahl": "10", "buchungsdatum": "2026-05-01"})
    page = client.get("/buchhaltung/briefmarken").get_data(as_text=True)
    assert "0,95" in page
    with sqlite3.connect(db) as c:
        assert c.execute("SELECT anzahl FROM briefmarken WHERE wert_cent=95").fetchone()[0] == 10


def test_storno_of_stamp_purchase_removes_from_stock(tmp_path):
    db = _db(tmp_path)
    bid = bookkeeping.buy_stamps(95, 10, "2026-05-01")
    with sqlite3.connect(db) as c:
        assert c.execute("SELECT anzahl FROM briefmarken WHERE wert_cent=95").fetchone()[0] == 10
    client = _take_client(db)
    client.post(f"/buchhaltung/storno/{bid}", data={"briefmarken_entfernen": "1"})
    with sqlite3.connect(db) as c:
        assert c.execute("SELECT anzahl FROM briefmarken WHERE wert_cent=95").fetchone()[0] == 0
        assert c.execute("SELECT storniert FROM briefmarken_kauf WHERE buchung_id=?", (bid,)).fetchone()[0] == 1


def test_reverse_stamp_purchase_never_negative_and_once(tmp_path):
    db = _db(tmp_path)
    bid = bookkeeping.buy_stamps(95, 10, "2026-05-01")
    bookkeeping.use_stamp(95)                 # 3 verbraucht -> Bestand 7
    bookkeeping.use_stamp(95)
    bookkeeping.use_stamp(95)
    info = bookkeeping.reverse_stamp_purchase(bid)
    assert info == {"wert_cent": 95, "anzahl": 10}
    with sqlite3.connect(db) as c:
        assert c.execute("SELECT anzahl FROM briefmarken WHERE wert_cent=95").fetchone()[0] == 0  # nicht negativ
    assert bookkeeping.reverse_stamp_purchase(bid) is None       # kein zweites Mal


def test_stock_correction_route(tmp_path):
    db = _db(tmp_path)
    bookkeeping.buy_stamps(95, 10, "2026-05-01")
    client = _take_client(db)
    client.post("/buchhaltung/briefmarken/bestand", data={"wert": "0,95", "anzahl": "3"})
    with sqlite3.connect(db) as c:
        assert c.execute("SELECT anzahl FROM briefmarken WHERE wert_cent=95").fetchone()[0] == 3


# --- Gruppierung + Kontrolle gegen die Cardmarket-Auszahlung --------------

def test_journal_by_order_groups_and_reconciles(tmp_path):
    db = _db(tmp_path)
    # Auszahlung = Waren + Versand - Gebuehr = (7,21-1,55) + 1,55 - 0,40 = 6,81
    _order(db, oid=7, auszahlung=6.81)
    bookkeeping.book_order(7)
    bookkeeping.add_booking("2026-05-01", "ausgabe", "Verpackungsmaterial", 500, "Kartons")

    grouped = bookkeeping.journal_by_order()
    assert len(grouped["orders"]) == 1
    g = grouped["orders"][0]
    assert g["netto_cent"] == g["auszahlung_cent"] == 681
    assert g["reconcile_ok"] is True
    assert any(b["kategorie"] == "Verpackungsmaterial" for b in grouped["sonstige"])


def test_journal_by_order_flags_mismatch(tmp_path):
    db = _db(tmp_path)
    # Gebuehr fehlt beim Buchen -> Summe passt nicht zur Auszahlung
    _order(db, oid=7, gebuehren=None, auszahlung=6.81)
    bookkeeping.book_order(7)
    g = bookkeeping.journal_by_order()["orders"][0]
    assert g["has_auszahlung"] is True
    assert g["reconcile_ok"] is False       # Warnung: Gebuehr fehlt


def test_take_order_corrects_amounts_before_booking(tmp_path):
    """Verrutschte Betraege (wie Bestellung 94) beim Uebernehmen korrigieren:
    Versand 1,25->1,55 und Gebuehr 0,04->0,02, dann stimmt die Auszahlung."""
    db = _db(tmp_path)
    _order(db, oid=94, number="1291026619", gesamt=1.59, versand=1.25,
           gebuehren=0.04, auszahlung=1.57)
    client = _take_client(db)
    client.post("/buchhaltung/uebernehmen/94",
                data={"versand": "1,55", "gebuehren": "0,02", "porto_methode": ""})
    with sqlite3.connect(db) as c:
        rows = dict((k, v) for k, v in c.execute(
            "SELECT kategorie, betrag_cent FROM journal WHERE bestellung_id=94"))
    assert rows["Warenverkauf"] == 4          # 1,59 - 1,55
    assert rows["Vereinnahmte Versandkosten"] == 155
    assert rows["Cardmarket-Gebühren"] == 2
    g = bookkeeping.journal_by_order()["orders"][0]
    assert g["reconcile_ok"] is True          # 4 + 155 - 2 == 157
