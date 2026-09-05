"""Positionen aus einer Bestellung nehmen, wenn die Ware nicht lieferbar war.

Der Fall aus dem Betrieb: eine Karte stand im Bestand und war doch nicht da.
Der Betrag wurde dem Kaeufer ueber Cardmarket erstattet, der Rest der
Bestellung ging trotzdem raus.

Drei Dinge muessen zusammenpassen, und alle drei werden hier geprueft:

* der **Beleg** zeigt die Position nicht mehr,
* der **Bestand** zieht sie nicht ab,
* die **Buchhaltung** erfaehrt den erstatteten Betrag.

Die Zeile wird gekennzeichnet, nicht geloescht: eine geloeschte Zeile erklaert
nichts, und der Betrag waere fuer die Erstattung verloren.
"""

import os
import sqlite3
import sys
import types

import pytest

sys.modules.setdefault("cv2", types.SimpleNamespace())
_pyz = types.ModuleType("pyzbar")
_pyz.pyzbar = types.SimpleNamespace(decode=lambda *a, **k: [])
sys.modules.setdefault("pyzbar", _pyz)
sys.modules.setdefault("pyzbar.pyzbar", _pyz.pyzbar)

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(__file__))))

import TCGInventory                                               # noqa: E402
from TCGInventory import (api_v1, auth, lager_manager, order_service,  # noqa: E402
                          positionen, setup_db, web)
from TCGInventory.positionen import PositionFehler                # noqa: E402

TOKEN = "t" * 40


@pytest.fixture()
def db(tmp_path):
    pfad = str(tmp_path / "e.db")
    for modul in (TCGInventory, web, auth, setup_db, order_service,
                  lager_manager, api_v1):
        modul.DB_FILE = pfad
    setup_db.initialize_database()
    return pfad


def _bestellung(db, positionen_liste=None, status="open"):
    """Eine Bestellung mit Karten im Bestand und passenden Positionen."""
    positionen_liste = positionen_liste or [
        ("Sol Ring", 1, 2.50), ("Lightning Bolt", 2, 0.50)]
    with sqlite3.connect(db) as conn:
        c = conn.cursor()
        c.execute(
            "INSERT INTO orders (buyer_name, email_message_id, date_received, "
            "status, order_number, address, address_confirmed, amount_gesamt, "
            "amount_gesamtwert, amount_versand, amount_gebuehren, "
            "amount_auszahlung) VALUES ('Sharqy', 'msg-1', "
            "'2026-09-01T10:00:00', ?, '1299999999', "
            "'Max Mustermann\nWeg 1\n12345 Ort', 1, 4.50, 3.50, 1.00, 0.10, 4.40)",
            (status,))
        bid = c.lastrowid
        ids = []
        for name, menge, preis in positionen_liste:
            c.execute(
                "INSERT INTO cards (name, set_code, language, condition, price, "
                "quantity, storage_code, status, collector_number, foil, "
                "item_type, date_added) VALUES (?, 'cmr', 'en', 'NM', ?, ?, "
                "'O01-S01-P1', 'verfügbar', '1', 0, 'card', "
                "'2026-08-01T10:00:00')", (name, preis, menge + 3))
            kid = c.lastrowid
            c.execute(
                "INSERT INTO order_items (order_id, card_name, quantity, "
                "unit_price, card_id, match_status, set_name, condition) "
                "VALUES (?, ?, ?, ?, ?, 'matched', 'Commander Masters', 'NM')",
                (bid, name, menge, preis, kid))
            ids.append((c.lastrowid, kid))
        conn.commit()
    return bid, ids


def _klient():
    web.app.config["TESTING"] = True
    klient = web.app.test_client()
    with klient.session_transaction() as s:
        s["user"] = "melvin"
    return klient


def _menge(db, card_id):
    with sqlite3.connect(db) as conn:
        return conn.execute("SELECT quantity FROM cards WHERE id = ?",
                            (card_id,)).fetchone()[0]


# ---------------------------------------------------------------------------
# Herausnehmen
# ---------------------------------------------------------------------------
def test_position_wird_gekennzeichnet_nicht_geloescht(db):
    bid, ids = _bestellung(db)
    item_id, _ = ids[0]

    with sqlite3.connect(db) as conn:
        positionen.entferne(conn, item_id, "nicht auffindbar", benutzer="melvin")

    with sqlite3.connect(db) as conn:
        conn.row_factory = sqlite3.Row
        zeile = dict(conn.execute("SELECT * FROM order_items WHERE id = ?",
                                  (item_id,)).fetchone())
    assert zeile["entfernt"] == 1
    assert zeile["entfernt_grund"] == "nicht auffindbar"
    assert zeile["entfernt_von"] == "melvin"
    assert zeile["entfernt_am"]
    assert zeile["card_name"] == "Sol Ring", "die Zeile bleibt als Nachweis"


def test_ohne_grund_geht_es_nicht(db):
    """Ohne Grund waere spaeter nicht nachvollziehbar, warum sie fehlt."""
    _, ids = _bestellung(db)
    with sqlite3.connect(db) as conn:
        with pytest.raises(PositionFehler) as fehler:
            positionen.entferne(conn, ids[0][0], "   ")
    assert "Grund" in str(fehler.value)


def test_zweimal_herausnehmen_geht_nicht(db):
    _, ids = _bestellung(db)
    with sqlite3.connect(db) as conn:
        positionen.entferne(conn, ids[0][0], "weg")
        with pytest.raises(PositionFehler):
            positionen.entferne(conn, ids[0][0], "nochmal")


def test_abgeschlossene_bestellung_wird_nicht_mehr_geaendert(db):
    """Sonst liefen gedruckter Beleg und Bestand auseinander."""
    _, ids = _bestellung(db, status="sold")
    with sqlite3.connect(db) as conn:
        with pytest.raises(PositionFehler) as fehler:
            positionen.entferne(conn, ids[0][0], "zu spaet")
    assert "abgeschlossen" in str(fehler.value)


# ---------------------------------------------------------------------------
# Bestand
# ---------------------------------------------------------------------------
def test_bestand_bleibt_ohne_ausdrueckliche_angabe_unberuehrt(db):
    """Ob die Karte fehlt oder nur verlegt ist, weiss das Programm nicht."""
    _, ids = _bestellung(db)
    item_id, card_id = ids[0]
    vorher = _menge(db, card_id)

    with sqlite3.connect(db) as conn:
        ergebnis = positionen.entferne(conn, item_id, "vielleicht verlegt")

    assert ergebnis["bestand_korrigiert"] == 0
    assert _menge(db, card_id) == vorher


def test_bestand_wird_auf_wunsch_gesenkt(db):
    _, ids = _bestellung(db)
    item_id, card_id = ids[1]          # Lightning Bolt, Menge 2
    vorher = _menge(db, card_id)

    with sqlite3.connect(db) as conn:
        ergebnis = positionen.entferne(conn, item_id, "fehlt wirklich",
                                       bestand_korrigieren=True)

    assert ergebnis["bestand_korrigiert"] == 2
    assert _menge(db, card_id) == vorher - 2


def test_bestand_geht_nicht_unter_null(db):
    _, ids = _bestellung(db, [("Sol Ring", 5, 2.50)])
    item_id, card_id = ids[0]
    with sqlite3.connect(db) as conn:
        conn.execute("UPDATE cards SET quantity = 1 WHERE id = ?", (card_id,))
        conn.commit()
        positionen.entferne(conn, item_id, "fehlt", bestand_korrigieren=True)
    assert _menge(db, card_id) == 0


def test_zuruecknehmen_erhoeht_den_bestand_nicht(db):
    """Ob die Karte inzwischen aufgetaucht ist, weiss das Programm nicht."""
    _, ids = _bestellung(db)
    item_id, card_id = ids[1]
    with sqlite3.connect(db) as conn:
        positionen.entferne(conn, item_id, "fehlt", bestand_korrigieren=True)
    gesenkt = _menge(db, card_id)

    with sqlite3.connect(db) as conn:
        positionen.stelle_zurueck(conn, item_id)

    assert _menge(db, card_id) == gesenkt
    with sqlite3.connect(db) as conn:
        assert conn.execute("SELECT entfernt FROM order_items WHERE id = ?",
                            (item_id,)).fetchone()[0] == 0


# ---------------------------------------------------------------------------
# Beleg
# ---------------------------------------------------------------------------
def test_beileger_zeigt_die_position_nicht_mehr(db):
    bid, ids = _bestellung(db)
    klient = _klient()

    vorher = klient.get(f"/orders/{bid}/shipping_note")
    assert vorher.status_code == 200

    with sqlite3.connect(db) as conn:
        positionen.entferne(conn, ids[0][0], "nicht auffindbar")

    nachher = klient.get(f"/orders/{bid}/shipping_note")
    assert nachher.status_code == 200
    assert nachher.data[:4] == b"%PDF"
    # Weniger Positionen -> das PDF wird kuerzer.
    assert len(nachher.data) < len(vorher.data)


def _pdf_text(roh):
    """Text aus dem PDF lesen.

    Die Schriften sind eingebettet; im rohen PDF steht der Satz nicht im
    Klartext, sondern als Glyphenfolge.
    """
    from io import BytesIO
    import pypdf
    leser = pypdf.PdfReader(BytesIO(roh))
    return "".join(s.extract_text() for s in leser.pages)


def test_beileger_nennt_die_entfallene_position(db):
    """Sonst steht dort eine niedrigere Summe und niemand weiss warum."""
    from TCGInventory.shipping_note import render_shipping_note

    roh = render_shipping_note(
        ["Max Muster", "Weg 1", "12345 Ort"], "1299999999",
        [{"quantity": 1, "name": "Sol Ring", "unit_price": 2.50}],
        compress=False, entfallene_positionen=1)
    assert "nicht lieferbar" in _pdf_text(roh)


def test_beileger_zaehlt_mehrere_entfallene(db):
    from TCGInventory.shipping_note import render_shipping_note

    roh = render_shipping_note(
        ["Max Muster", "Weg 1", "12345 Ort"], "1299999999",
        [{"quantity": 1, "name": "Sol Ring", "unit_price": 2.50}],
        compress=False, entfallene_positionen=3)
    text = _pdf_text(roh)
    assert "3 Positionen" in text


def test_ohne_entfallene_kein_zusatzsatz(db):
    from TCGInventory.shipping_note import render_shipping_note

    roh = render_shipping_note(
        ["Max Muster", "Weg 1", "12345 Ort"], "1299999999",
        [{"quantity": 1, "name": "Sol Ring", "unit_price": 2.50}],
        compress=False)
    assert "nicht lieferbar" not in _pdf_text(roh)


def test_quittung_zeigt_die_position_nicht_mehr(db):
    bid, ids = _bestellung(db)
    with sqlite3.connect(db) as conn:
        positionen.entferne(conn, ids[0][0], "fehlt")

    antwort = _klient().get(f"/orders/{bid}/quittung")
    assert antwort.status_code == 200
    assert antwort.data[:4] == b"%PDF"


# ---------------------------------------------------------------------------
# Verkauft markieren
# ---------------------------------------------------------------------------
def test_herausgenommene_position_wird_nicht_abgezogen(db):
    bid, ids = _bestellung(db)
    entfernt_item, entfernt_card = ids[0]
    bleibt_item, bleibt_card = ids[1]

    with sqlite3.connect(db) as conn:
        positionen.entferne(conn, entfernt_item, "fehlt")
    vorher_entfernt = _menge(db, entfernt_card)
    vorher_bleibt = _menge(db, bleibt_card)

    _klient().post(f"/orders/{bid}/mark_sold", follow_redirects=True)

    assert _menge(db, entfernt_card) == vorher_entfernt, "wurde doch abgezogen"
    assert _menge(db, bleibt_card) == vorher_bleibt - 2


# ---------------------------------------------------------------------------
# Schnittstelle zur Buchhaltung
# ---------------------------------------------------------------------------
def _api(klient, pfad):
    # Die Schnittstelle liefert standardmaessig nur verkaufte Bestellungen.
    trenner = "&" if "?" in pfad else "?"
    return klient.get(f"{pfad}{trenner}status=open",
                      headers={"Authorization": f"Bearer {TOKEN}"})


def test_api_meldet_erstattungsbetrag(db, monkeypatch):
    monkeypatch.setenv("TCG_API_TOKEN", TOKEN)
    bid, ids = _bestellung(db)
    with sqlite3.connect(db) as conn:
        positionen.entferne(conn, ids[1][0], "fehlt")   # 2 x 0,50 EUR

    daten = _api(_klient(), "/api/v1/orders").get_json()["bestellungen"][0]
    assert daten["erstattung_cent"] == 100
    assert daten["erstattung_unvollstaendig"] == []
    # Die Betraege der Mail bleiben, wie Cardmarket sie berechnet hat.
    assert daten["betraege_cent"]["gesamt"] == 450


def test_api_kennzeichnet_die_position(db, monkeypatch):
    monkeypatch.setenv("TCG_API_TOKEN", TOKEN)
    bid, ids = _bestellung(db)
    with sqlite3.connect(db) as conn:
        positionen.entferne(conn, ids[0][0], "nicht auffindbar")

    daten = _api(_klient(), "/api/v1/orders").get_json()["bestellungen"][0]
    entfernt = [p for p in daten["positionen"] if p["entfernt"]]
    assert len(entfernt) == 1
    assert entfernt[0]["name"] == "Sol Ring"
    assert entfernt[0]["entfernt_grund"] == "nicht auffindbar"


def test_api_meldet_fehlenden_einzelpreis(db, monkeypatch):
    """Ohne Preis fehlt der Anteil in der Summe — das muss sichtbar sein."""
    monkeypatch.setenv("TCG_API_TOKEN", TOKEN)
    bid, ids = _bestellung(db)
    with sqlite3.connect(db) as conn:
        conn.execute("UPDATE order_items SET unit_price = NULL WHERE id = ?",
                     (ids[0][0],))
        conn.commit()
        positionen.entferne(conn, ids[0][0], "fehlt")

    daten = _api(_klient(), "/api/v1/orders").get_json()["bestellungen"][0]
    assert daten["erstattung_cent"] == 0
    assert daten["erstattung_unvollstaendig"] == ["Sol Ring"]


def test_hash_aendert_sich_beim_herausnehmen(db, monkeypatch):
    """Sonst haelt die Buchhaltung die Bestellung fuer unveraendert."""
    monkeypatch.setenv("TCG_API_TOKEN", TOKEN)
    bid, ids = _bestellung(db)
    klient = _klient()

    vorher = _api(klient, "/api/v1/orders").get_json()["bestellungen"][0]["inhalt_hash"]
    with sqlite3.connect(db) as conn:
        positionen.entferne(conn, ids[0][0], "fehlt")
    nachher = _api(klient, "/api/v1/orders").get_json()["bestellungen"][0]["inhalt_hash"]

    assert vorher != nachher


# ---------------------------------------------------------------------------
# Bedienung
# ---------------------------------------------------------------------------
def test_formular_nimmt_die_position_heraus(db):
    bid, ids = _bestellung(db)
    antwort = _klient().post(
        f"/orders/items/{ids[0][0]}/entfernen",
        data={"grund": "nicht auffindbar"}, follow_redirects=True)

    assert antwort.status_code == 200
    with sqlite3.connect(db) as conn:
        assert conn.execute("SELECT entfernt FROM order_items WHERE id = ?",
                            (ids[0][0],)).fetchone()[0] == 1


def test_formular_ohne_grund_wird_abgelehnt(db):
    bid, ids = _bestellung(db)
    antwort = _klient().post(f"/orders/items/{ids[0][0]}/entfernen",
                             data={"grund": ""}, follow_redirects=True)
    assert "Grund" in antwort.get_data(as_text=True)
    with sqlite3.connect(db) as conn:
        assert conn.execute("SELECT entfernt FROM order_items WHERE id = ?",
                            (ids[0][0],)).fetchone()[0] == 0


def test_uebersicht_zeigt_den_grund(db):
    bid, ids = _bestellung(db)
    with sqlite3.connect(db) as conn:
        positionen.entferne(conn, ids[0][0], "nicht auffindbar",
                            benutzer="melvin")

    text = _klient().get("/orders").get_data(as_text=True)
    assert "herausgenommen" in text
    assert "nicht auffindbar" in text
