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
           status="sold", confirmed=1, datum="2026-03-05T10:00:00"):
    with sqlite3.connect(db) as c:
        c.execute(
            "INSERT INTO orders (id, buyer_name, email_message_id, date_received, email_date,"
            " status, order_number, address, address_confirmed,"
            " amount_gesamt, amount_versand, amount_gebuehren)"
            " VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
            (oid, "gaulix", f"m{oid}", datum, datum, status, number,
             "Max Müller\n01159 Dresden\nDeutschland", confirmed,
             gesamt, versand, gebuehren),
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
    assert "Buchungsjournal" in page and "1001" in page          # übernehmbare Bestellung

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
