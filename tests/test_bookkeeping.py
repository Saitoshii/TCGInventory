"""WP3b: Buchhaltungsmodul — EÜR, Belege, Briefmarken, Versand-Marge.

Kernregel, die hier abgesichert wird: **Porto wird genau einmal zur Ausgabe —
beim Kauf der Briefmarke.** Der Verbrauch beim Versand bucht nie.
"""

import os
import sys
import types
import hashlib
import sqlite3
from pathlib import Path

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
    bookkeeping.BELEGE_DIR = tmp_path / "belege"
    setup_db.initialize_database()
    return db


def _order(db, oid=7, number="1001", gesamt=5.45, versand=1.55, gebuehren=0.20,
           auszahlung=5.25, status="sold", versandt="2026-05-05",
           datum="2026-05-04T10:00:00", positionen=(("Karte", 1, 3.90),)):
    with sqlite3.connect(db) as c:
        c.execute(
            "INSERT INTO orders (id, buyer_name, email_message_id, date_received, email_date,"
            " date_completed, status, order_number, address, address_confirmed,"
            " amount_gesamt, amount_versand, amount_gebuehren, amount_auszahlung)"
            " VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (oid, "gaulix", f"m{oid}", datum, datum, versandt, status, number,
             "Max Müller\n01159 Dresden", 1, gesamt, versand, gebuehren, auszahlung),
        )
        for name, menge, preis in positionen or ():
            c.execute(
                "INSERT INTO order_items (order_id, card_name, quantity, unit_price, match_status)"
                " VALUES (?,?,?,?,'matched')", (oid, name, menge, preis))
        c.commit()
    return oid


def _markenart(nennwert_cent=95):
    for m in bookkeeping.list_markenarten():
        if m["nennwert_cent"] == nennwert_cent:
            return m["id"]
    raise AssertionError(f"Markenart {nennwert_cent} fehlt")


def _client(db):
    from TCGInventory import web
    web.DB_FILE = db
    web.app.config["TESTING"] = True
    client = web.app.test_client()
    with client.session_transaction() as s:
        s["user"] = "tester"
    return client


# =========================================================================
# Unveränderbarkeit (append-only)
# =========================================================================

def test_bookings_cannot_be_updated_or_deleted(tmp_path):
    db = _db(tmp_path)
    bid = bookkeeping.add_booking("2026-03-01", "ausgabe", "Verpackungsmaterial", 1250, "Kartons")

    for sql in (
        "UPDATE journal SET betrag_cent = 1 WHERE id = ?",
        "UPDATE journal SET kategorie = 'Sonstige Ausgaben' WHERE id = ?",
        "UPDATE journal SET buchungsdatum = '2020-01-01' WHERE id = ?",
        "UPDATE journal SET beschreibung = 'manipuliert' WHERE id = ?",
        "UPDATE journal SET art = 'einnahme' WHERE id = ?",
        "UPDATE journal SET erfasst_am = '2020-01-01' WHERE id = ?",
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


def test_storno_creates_linked_row_and_only_once(tmp_path):
    db = _db(tmp_path)
    bid = bookkeeping.add_booking("2026-03-01", "ausgabe", "Bürobedarf", 999, "Stifte")
    sid = bookkeeping.storno_booking(bid, "falscher Betrag")

    with sqlite3.connect(db) as c:
        storno = c.execute(
            "SELECT art, kategorie, betrag_cent, storniert_buchung_id FROM journal WHERE id=?",
            (sid,)).fetchone()
        original = c.execute(
            "SELECT betrag_cent, kategorie, storniert_durch FROM journal WHERE id=?",
            (bid,)).fetchone()
    assert storno == ("storno", "Bürobedarf", 999, bid)
    assert original == (999, "Bürobedarf", sid)      # Original inhaltlich unverändert

    with pytest.raises(ValueError):
        bookkeeping.storno_booking(bid)               # nicht zweimal
    with pytest.raises(ValueError):
        bookkeeping.storno_booking(sid)               # Storno nicht stornierbar


def test_lfd_nr_is_gapless(tmp_path):
    _db(tmp_path)
    for i in range(5):
        bookkeeping.add_booking("2026-03-01", "ausgabe", "Bürobedarf", 100 + i, f"#{i}")
    nrs = sorted(b["lfd_nr"] for b in bookkeeping.list_bookings())
    assert nrs == list(range(1, len(nrs) + 1))


# =========================================================================
# Bestellungen
# =========================================================================

def test_order_takeover_creates_exactly_three_bookings(tmp_path):
    db = _db(tmp_path)
    _order(db)
    ids = bookkeeping.book_order(7)
    assert len(ids) == 3

    with sqlite3.connect(db) as c:
        rows = dict((k, v) for k, v in c.execute(
            "SELECT kategorie, betrag_cent FROM journal WHERE bestellung_id=7"))
        datum = c.execute(
            "SELECT buchungsdatum FROM journal WHERE bestellung_id=7 LIMIT 1").fetchone()[0]
    assert rows["Warenverkauf"] == 390                # 5,45 − 1,55
    assert rows["Vereinnahmte Versandkosten"] == 155
    assert rows["Cardmarket-Gebühren"] == 20
    assert datum == "2026-05-05"                      # Versanddatum

    # Porto wird bei der Bestellung NICHT gebucht.
    with sqlite3.connect(db) as c:
        n = c.execute("SELECT COUNT(*) FROM journal WHERE kategorie=?",
                      (bookkeeping.KAT_PORTO,)).fetchone()[0]
    assert n == 0


def test_order_cannot_be_booked_twice(tmp_path):
    db = _db(tmp_path)
    _order(db)
    bookkeeping.book_order(7)
    with pytest.raises(ValueError):
        bookkeeping.book_order(7)
    with sqlite3.connect(db) as c:
        assert c.execute("SELECT COUNT(*) FROM journal WHERE bestellung_id=7").fetchone()[0] == 3


def test_unique_index_blocks_duplicate_at_db_level(tmp_path):
    db = _db(tmp_path)
    _order(db)
    bookkeeping.book_order(7)
    with pytest.raises(sqlite3.IntegrityError):
        bookkeeping.add_booking("2026-03-02", "einnahme", "Warenverkauf", 390,
                                "Dublette", bestellung_id=7)


def test_orders_before_business_start_are_hidden(tmp_path):
    """Bestellungen von vor dem Geschäftsbeginn werden nicht zur Übernahme
    angeboten — vor der Gründung gehören sie nicht in die EÜR."""
    db = _db(tmp_path)
    _order(db, oid=20, number="alt-1", versandt="2026-04-30", datum="2026-04-29T10:00:00")
    _order(db, oid=21, number="neu-1", versandt="2026-05-01", datum="2026-05-01T10:00:00")
    _order(db, oid=22, number="neu-2", versandt="2026-06-15", datum="2026-06-14T10:00:00")

    nummern = [o["order_number"] for o in bookkeeping.bookable_orders()]
    assert "alt-1" not in nummern
    assert set(nummern) == {"neu-1", "neu-2"}          # Stichtag inklusive 01.05.
    assert bookkeeping.count_vor_geschaeftsbeginn() == 1

    # Der Stichtag ist konfigurierbar.
    original = bookkeeping.GESCHAEFTSBEGINN
    try:
        bookkeeping.GESCHAEFTSBEGINN = "2026-06-01"
        assert [o["order_number"] for o in bookkeeping.bookable_orders()] == ["neu-2"]
    finally:
        bookkeeping.GESCHAEFTSBEGINN = original


def test_missing_shipping_books_zero_and_flags_review(tmp_path):
    db = _db(tmp_path)
    # Versandkosten fehlen in der Mail; Positionssumme 3,90, Gesamt 3,90.
    _order(db, oid=8, number="1002", gesamt=3.90, versand=None, gebuehren=0.20,
           positionen=(("Karte", 1, 3.90),))
    bookkeeping.book_order(8)
    with sqlite3.connect(db) as c:
        versand = c.execute(
            "SELECT betrag_cent FROM journal WHERE bestellung_id=8 AND kategorie=?",
            ("Vereinnahmte Versandkosten",)).fetchone()[0]
    assert versand == 0
    liste = bookkeeping.pruefliste()
    assert any(p["id"] == 8 and "Versandkosten fehlen" in p["buchung_pruefen"] for p in liste)


def test_inconsistent_sum_goes_to_review_without_silent_fix(tmp_path):
    db = _db(tmp_path)
    # Positionssumme (2,00) passt nicht zu Gesamt − Versand (5,45 − 1,55 = 3,90).
    _order(db, oid=9, number="1003", positionen=(("Karte", 1, 2.00),))
    bookkeeping.book_order(9)
    with sqlite3.connect(db) as c:
        waren = c.execute(
            "SELECT betrag_cent FROM journal WHERE bestellung_id=9 AND kategorie='Warenverkauf'"
        ).fetchone()[0]
    assert waren == 390                    # keine stille Korrektur auf 200
    assert any(p["id"] == 9 and "weicht" in p["buchung_pruefen"] for p in bookkeeping.pruefliste())


# =========================================================================
# Briefmarken — Doppelbuchung ausgeschlossen
# =========================================================================

def test_stamp_purchase_books_once_and_adds_stock(tmp_path):
    db = _db(tmp_path)
    art = _markenart(95)
    bid = bookkeeping.buy_stamps("2026-03-02", art, 10, 950)

    with sqlite3.connect(db) as c:
        rows = c.execute(
            "SELECT art, kategorie, betrag_cent FROM journal WHERE id=?", (bid,)).fetchall()
        anzahl = c.execute("SELECT COUNT(*) FROM journal WHERE kategorie=?",
                           (bookkeeping.KAT_PORTO,)).fetchone()[0]
    assert rows == [("ausgabe", "Porto/Briefmarken", 950)]
    assert anzahl == 1                                  # genau eine Buchung
    assert bookkeeping.get_markenart(art)["bestand"] == 10


def test_consumption_never_books_and_reduces_stock(tmp_path):
    db = _db(tmp_path)
    art = _markenart(95)
    bookkeeping.buy_stamps("2026-03-02", art, 10, 950)
    vor = bookkeeping.list_bookings()

    bookkeeping.consume_stamps(art, 1, bestellung_id=None, datum="2026-03-03")

    assert len(bookkeeping.list_bookings()) == len(vor)   # keine neue Buchung
    assert bookkeeping.get_markenart(art)["bestand"] == 9


def test_immediate_purchase_books_exactly_once(tmp_path):
    db = _db(tmp_path)
    art = _markenart(110)
    bookkeeping.buy_and_consume("2026-03-02", art, 1, 110, bestellung_id=None)

    with sqlite3.connect(db) as c:
        n = c.execute("SELECT COUNT(*) FROM journal WHERE kategorie=?",
                      (bookkeeping.KAT_PORTO,)).fetchone()[0]
        summe = c.execute("SELECT SUM(betrag_cent) FROM journal WHERE kategorie=?",
                          (bookkeeping.KAT_PORTO,)).fetchone()[0]
    assert n == 1 and summe == 110                       # genau eine, nicht zwei
    assert bookkeeping.get_markenart(art)["bestand"] == 0  # gekauft und sofort verbraucht


def test_porto_total_is_independent_of_consumption_count(tmp_path):
    _db(tmp_path)
    art = _markenart(95)
    bookkeeping.buy_stamps("2026-03-01", art, 10, 950)
    for _ in range(7):
        bookkeeping.consume_stamps(art, 1, datum="2026-03-05")

    s = bookkeeping.summary("2026-01-01", "2026-12-31")
    assert s["ausgaben"][bookkeeping.KAT_PORTO] == 950    # unabhängig von 7 Verbräuchen


def test_nennwert_change_keeps_history(tmp_path):
    db = _db(tmp_path)
    alt = _markenart(95)
    bookkeeping.buy_stamps("2026-03-01", alt, 10, 950)
    bookkeeping.consume_stamps(alt, 1, datum="2026-03-02")

    neu = bookkeeping.change_nennwert(alt, 100)
    assert neu != alt

    with sqlite3.connect(db) as c:
        betrag = c.execute("SELECT betrag_cent FROM journal WHERE kategorie=?",
                           (bookkeeping.KAT_PORTO,)).fetchone()[0]
        portowert = c.execute(
            "SELECT portowert_cent FROM markenverbrauch WHERE markenart_id=?", (alt,)).fetchone()[0]
        alt_aktiv = c.execute("SELECT aktiv FROM markenart WHERE id=?", (alt,)).fetchone()[0]
        neu_wert = c.execute("SELECT nennwert_cent FROM markenart WHERE id=?", (neu,)).fetchone()[0]
    assert betrag == 950            # Buchung unverändert
    assert portowert == 95          # historischer Verbrauchswert eingefroren
    assert alt_aktiv == 0 and neu_wert == 100


def test_storno_of_stamp_purchase_removes_stock(tmp_path):
    _db(tmp_path)
    art = _markenart(95)
    bid = bookkeeping.buy_stamps("2026-03-01", art, 10, 950)
    assert bookkeeping.get_markenart(art)["bestand"] == 10
    bookkeeping.storno_booking(bid, "Fehlkauf")
    assert bookkeeping.get_markenart(art)["bestand"] == 0


def test_inventory_correction_sets_stock_without_booking(tmp_path):
    _db(tmp_path)
    art = _markenart(180)
    vorher = len(bookkeeping.list_bookings())
    bookkeeping.set_bestand(art, 7)
    assert bookkeeping.get_markenart(art)["bestand"] == 7
    assert len(bookkeeping.list_bookings()) == vorher     # keine Buchung


# =========================================================================
# Auszahlungen / Zufluss
# =========================================================================

def test_assignment_sets_inflow_on_all_bookings(tmp_path):
    db = _db(tmp_path)
    _order(db)
    bookkeeping.book_order(7)
    az = bookkeeping.create_auszahlung("2026-04-10", 525, "Sammelauszahlung")
    n = bookkeeping.assign_orders_to_auszahlung(az, [7])
    assert n == 3
    with sqlite3.connect(db) as c:
        rows = c.execute(
            "SELECT DISTINCT zahlungseingang_am, auszahlung_id FROM journal WHERE bestellung_id=7"
        ).fetchall()
    assert rows == [("2026-04-10", az)]


def test_order_belongs_to_only_one_auszahlung(tmp_path):
    db = _db(tmp_path)
    _order(db)
    bookkeeping.book_order(7)
    a1 = bookkeeping.create_auszahlung("2026-04-10", 525)
    a2 = bookkeeping.create_auszahlung("2026-05-10", 525)
    bookkeeping.assign_orders_to_auszahlung(a1, [7])
    assert bookkeeping.assign_orders_to_auszahlung(a2, [7]) == 0   # keine Umbuchung
    assert bookkeeping.order_auszahlung_id(7) == a1


def test_summary_uses_inflow_and_lists_pending(tmp_path):
    db = _db(tmp_path)
    _order(db)
    bookkeeping.book_order(7)

    vorher = bookkeeping.summary("2026-01-01", "2026-12-31")
    assert vorher["summe_einnahmen"] == 0        # noch nicht zugeflossen
    assert vorher["offen_count"] == 2 and vorher["offen_einnahme"] == 545
    assert vorher["ausgaben"]["Cardmarket-Gebühren"] == 20   # Ausgabe zählt sofort

    az = bookkeeping.create_auszahlung("2026-04-10", 525)
    bookkeeping.assign_orders_to_auszahlung(az, [7])
    nachher = bookkeeping.summary("2026-01-01", "2026-12-31")
    assert nachher["summe_einnahmen"] == 545 and nachher["offen_count"] == 0


# =========================================================================
# Auswertung
# =========================================================================

def test_specification_example_end_to_end(tmp_path):
    """Bestellung 5,45 € (Ware 3,90 + Versand 1,55), Gebühr 0,20,
    Markenkauf 10 × 0,95 € = 9,50 € am selben Tag, eine Marke verbraucht."""
    db = _db(tmp_path)
    _order(db)
    bookkeeping.book_order(7)
    art = _markenart(95)
    bookkeeping.buy_stamps("2026-03-02", art, 10, 950)
    bookkeeping.consume_stamps(art, 1, bestellung_id=7, datum="2026-03-02")
    az = bookkeeping.create_auszahlung("2026-03-02", 525)
    bookkeeping.assign_orders_to_auszahlung(az, [7])

    s = bookkeeping.summary("2026-01-01", "2026-12-31")
    assert s["summe_einnahmen"] == 545
    assert s["summe_ausgaben"] == 970          # 9,50 Marken + 0,20 Gebühr
    assert s["ueberschuss"] == -425

    m = bookkeeping.versand_marge("2026-01-01", "2026-12-31")
    assert m["vereinnahmt_cent"] == 155 and m["porto_cent"] == 95
    assert m["marge_cent"] == 60


def test_storno_changes_totals_correctly(tmp_path):
    db = _db(tmp_path)
    _order(db)
    bookkeeping.book_order(7)
    az = bookkeeping.create_auszahlung("2026-03-02", 525)
    bookkeeping.assign_orders_to_auszahlung(az, [7])
    vor = bookkeeping.summary("2026-01-01", "2026-12-31")
    assert vor["summe_einnahmen"] == 545

    with sqlite3.connect(db) as c:
        bid = c.execute(
            "SELECT id FROM journal WHERE bestellung_id=7 AND kategorie='Warenverkauf'"
        ).fetchone()[0]
    bookkeeping.storno_booking(bid, "Rücksendung")

    nach = bookkeeping.summary("2026-01-01", "2026-12-31")
    assert nach["summe_einnahmen"] == 155        # nur noch die Versandkosten


def test_no_rounding_errors_in_cent_arithmetic(tmp_path):
    _db(tmp_path)
    for _ in range(10):
        bookkeeping.add_booking("2026-03-01", "ausgabe", "Bürobedarf", 1, "1 Cent")
    s = bookkeeping.summary("2026-01-01", "2026-12-31")
    assert s["summe_ausgaben"] == 10
    assert bookkeeping.to_cent("0,10") == 10 and bookkeeping.cent_to_de(10) == "0,10"
    assert bookkeeping.to_cent("1.234,56") == 123456


def test_summary_csv_german_format(tmp_path):
    db = _db(tmp_path)
    _order(db)
    bookkeeping.book_order(7)
    az = bookkeeping.create_auszahlung("2026-03-02", 525)
    bookkeeping.assign_orders_to_auszahlung(az, [7])
    result = bookkeeping.summary("2026-01-01", "2026-12-31")
    payload = bookkeeping.summary_csv(result, "2026-01-01", "2026-12-31")

    assert payload.startswith(b"\xef\xbb\xbf")        # UTF-8 mit BOM
    text = payload.decode("utf-8-sig")
    assert "01.01.2026 - 31.12.2026" in text          # TT.MM.JJJJ
    assert "Warenverkauf;3,90" in text                # Semikolon + Komma-Dezimal
    assert "Überschuss" in text and "Gebühren" in text  # Umlaute korrekt


# =========================================================================
# Belege
# =========================================================================

def test_receipt_stored_with_checksum_and_kept_after_storno(tmp_path):
    db = _db(tmp_path)
    data = b"%PDF-1.4 Testbeleg"
    bid = bookkeeping.save_receipt("Rechnung Mai.pdf", data, "application/pdf")
    info = bookkeeping.get_receipt(bid)
    assert info["sha256"] == hashlib.sha256(data).hexdigest()
    assert Path(info["pfad"]).read_bytes() == data     # unverändert gespeichert

    buchung = bookkeeping.add_booking("2026-05-01", "ausgabe", "Verpackungsmaterial",
                                      1290, "Kartons", beleg_id=bid)
    bookkeeping.storno_booking(buchung, "Fehler")
    assert bookkeeping.get_receipt(bid) is not None    # Beleg bleibt erhalten
    assert Path(info["pfad"]).exists()

    with pytest.raises(ValueError):
        bookkeeping.save_receipt("virus.exe", b"x")    # nur PDF/JPG/PNG
    with pytest.raises(sqlite3.IntegrityError):
        with sqlite3.connect(db) as c:
            c.execute("DELETE FROM belege WHERE id = ?", (bid,))


# =========================================================================
# Abgrenzung
# =========================================================================

def test_no_vat_fields_in_data_model(tmp_path):
    db = _db(tmp_path)
    verboten = ("ust", "umsatzsteuer", "vorsteuer", "steuersatz", "mwst", "netto_cent")
    with sqlite3.connect(db) as c:
        tabellen = [r[0] for r in c.execute(
            "SELECT name FROM sqlite_master WHERE type='table'")]
        for t in tabellen:
            for col in [r[1].lower() for r in c.execute(f"PRAGMA table_info({t})")]:
                assert not any(v in col for v in verboten), f"{t}.{col}"


def test_upgrade_keeps_old_bookings_and_stamp_stock(tmp_path):
    """Bestandsdatenbank aus der Vorversion: alte Buchungen bleiben unverändert
    (auch mit der alten Kategorie) und der Markenbestand wird übernommen."""
    db = str(tmp_path / "alt.db")
    for mod in (TCGInventory, setup_db, auth, bookkeeping):
        mod.DB_FILE = db
    bookkeeping.BELEGE_DIR = tmp_path / "belege"

    conn = sqlite3.connect(db)
    conn.executescript(
        """
        CREATE TABLE journal (id INTEGER PRIMARY KEY AUTOINCREMENT,
          lfd_nr INTEGER NOT NULL UNIQUE, erfasst_am TEXT NOT NULL,
          buchungsdatum TEXT NOT NULL, art TEXT NOT NULL, kategorie TEXT NOT NULL,
          betrag_cent INTEGER NOT NULL, beschreibung TEXT, bestellung_id INTEGER,
          beleg_id INTEGER, storniert_buchung_id INTEGER, storniert_durch INTEGER,
          zahlungseingang_am TEXT);
        CREATE TABLE briefmarken (wert_cent INTEGER PRIMARY KEY,
          anzahl INTEGER NOT NULL DEFAULT 0);
        INSERT INTO briefmarken VALUES (95, 7), (99, 3);
        INSERT INTO journal (lfd_nr, erfasst_am, buchungsdatum, art, kategorie,
                             betrag_cent, beschreibung)
          VALUES (1, '2026-07-17', '2026-07-17', 'ausgabe', 'Porto/Versand', 2200, 'alt');
        """
    )
    conn.commit()
    conn.close()

    setup_db.initialize_database()          # Update

    with sqlite3.connect(db) as c:
        alt = c.execute("SELECT kategorie, betrag_cent FROM journal WHERE lfd_nr=1").fetchone()
    assert alt == ("Porto/Versand", 2200)   # unverändert erhalten

    bestaende = {m["nennwert_cent"]: m["bestand"] for m in bookkeeping.list_markenarten()}
    assert bestaende[95] == 7               # bekannter Wert -> Standardbrief
    assert bestaende[99] == 3               # unbekannter Wert -> eigene Markenart

    s = bookkeeping.summary("2026-01-01", "2026-12-31")
    assert s["ausgaben"]["Porto/Versand"] == 2200   # Alt-Kategorie bleibt sichtbar


def test_no_dragonshield_price_data_in_bookkeeping():
    quelle = Path(bookkeeping.__file__).read_text(encoding="utf-8").lower()
    assert "price_bought" not in quelle
    assert "from cards" not in quelle and "join cards" not in quelle


# =========================================================================
# Routen
# =========================================================================

def test_bookkeeping_routes(tmp_path):
    db = _db(tmp_path)
    _order(db)
    client = _client(db)

    page = client.get("/buchhaltung").get_data(as_text=True)
    assert "Buchhaltung" in page and "1001" in page

    client.post("/buchhaltung/uebernehmen/7", data={"frankierung": ""})
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

    # Markenkauf über die Route
    art = _markenart(95)
    client.post("/buchhaltung/briefmarken/kauf", data={
        "datum": "2026-03-02", "markenart_id": str(art),
        "stueckzahl": "10", "betrag": "9,50"})
    assert bookkeeping.get_markenart(art)["bestand"] == 10

    # Auszahlung anlegen und zuordnen
    client.post("/buchhaltung/auszahlungen", data={"datum": "2026-04-10", "betrag": "5,25"})
    az = bookkeeping.list_auszahlungen()[0]["id"]
    client.post(f"/buchhaltung/auszahlungen/{az}/zuordnen", data={"order_ids": ["7"]})
    assert bookkeeping.order_auszahlung_id(7) == az

    for url in ("/buchhaltung?ansicht=journal", "/buchhaltung/briefmarken",
                "/buchhaltung/auszahlungen", "/buchhaltung/marge",
                "/buchhaltung/auswertung"):
        assert client.get(url).status_code == 200

    csv_resp = client.get("/buchhaltung/auswertung.csv?von=2026-01-01&bis=2026-12-31")
    assert csv_resp.status_code == 200
    assert csv_resp.get_data().startswith(b"\xef\xbb\xbf")


def test_take_order_with_stock_frankierung_books_no_porto(tmp_path):
    db = _db(tmp_path)
    _order(db)
    art = _markenart(95)
    bookkeeping.buy_stamps("2026-03-01", art, 5, 475)
    client = _client(db)

    client.post("/buchhaltung/uebernehmen/7",
                data={"frankierung": "vorrat", "markenart_id": str(art), "stueckzahl": "1"})

    with sqlite3.connect(db) as c:
        porto_buchungen = c.execute(
            "SELECT COUNT(*) FROM journal WHERE kategorie=?", (bookkeeping.KAT_PORTO,)
        ).fetchone()[0]
    assert porto_buchungen == 1                     # nur der Kauf, nicht der Verbrauch
    assert bookkeeping.get_markenart(art)["bestand"] == 4
    assert bookkeeping.consumption_for_order(7)[0]["portowert_cent"] == 95


def test_take_order_with_immediate_purchase_books_once(tmp_path):
    db = _db(tmp_path)
    _order(db)
    art = _markenart(95)
    client = _client(db)

    client.post("/buchhaltung/uebernehmen/7",
                data={"frankierung": "sofort", "markenart_id": str(art),
                      "stueckzahl": "1", "betrag": "0,95"})

    with sqlite3.connect(db) as c:
        rows = c.execute("SELECT betrag_cent FROM journal WHERE kategorie=?",
                         (bookkeeping.KAT_PORTO,)).fetchall()
    assert rows == [(95,)]                          # genau eine Ausgabebuchung
    assert bookkeeping.get_markenart(art)["bestand"] == 0
    assert len(bookkeeping.consumption_for_order(7)) == 1
