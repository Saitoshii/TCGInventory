"""Fehlende Betraege aus der Original-Bestellmail nachtragen.

Gemeldet aus dem Betrieb: die Pruefliste der Buchhaltung zeigt bei aelteren
Bestellungen "Ware fehlt - Versand fehlt - Gebuehr fehlt - Gutschrift fehlt".
Die Bestellungen sind in Ordnung; beim Einlesen hat der Parser die Betraege
nur noch nicht erfasst. Die Roh-Mail wird nicht gespeichert, die
Gmail-Message-ID steht aber an jeder Bestellung.

Der Kern dieser Tests: es wird **gelesen**, nicht geraten, und es werden
**nur Luecken** gefuellt.
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

import TCGInventory                                              # noqa: E402
from TCGInventory import (auth, lager_manager, nachlesen,        # noqa: E402
                          order_service, setup_db, web)

MAIL = """Bestellnummer: 1287799674
Käufer: Sharqy
Status: Bezahlt

Max Mustermann
Musterweg 1
12345 Musterstadt
Deutschland

Sendungsverfolgung:

1x Force of Negation (Modern Horizons) - R - Englisch - NM 1,06 EUR

Gesamtwert: 1,06 EUR
Versandkosten: 1,55 EUR
Gebühren: 0,07 EUR
Gesamtbetrag: 2,61 EUR
Auszahlungsbetrag: 2,54 EUR
"""


class FalscherDienst:
    """Ersetzt Gmail. Der echte Zugang wird hier nicht gebraucht."""

    def __init__(self, mails):
        self.mails = mails            # message_id -> Mailtext
        self.abgerufen = []

    # Nachgebaut wird nur, was hole_nachricht benutzt.
    def users(self):
        return self

    def messages(self):
        return self

    def get(self, userId=None, id=None, format=None):
        self.abgerufen.append(id)
        dienst = self

        class Aufruf:
            def execute(self):
                if id not in dienst.mails:
                    from googleapiclient.errors import HttpError
                    antwort = types.SimpleNamespace(
                        status=404, reason="Not Found")
                    raise HttpError(antwort, b"weg")
                return {"id": id, "_text": dienst.mails[id]}

        return Aufruf()


@pytest.fixture(autouse=True)
def gmail_ersatz(monkeypatch):
    """Mailtext direkt aus dem gefaelschten Nachrichtenobjekt lesen."""
    monkeypatch.setattr(nachlesen, "get_email_body", lambda m: m.get("_text", ""))
    monkeypatch.setattr(nachlesen, "get_email_subject", lambda m: "Bitte versenden")
    monkeypatch.setattr(nachlesen, "get_email_date", lambda m: "2026-07-14T10:00:00")


@pytest.fixture()
def db(tmp_path):
    pfad = str(tmp_path / "n.db")
    for modul in (TCGInventory, web, auth, setup_db, order_service, lager_manager):
        modul.DB_FILE = pfad
    setup_db.initialize_database()
    return pfad


def _bestellung(db, *, mid="msg-1", nummer=None, gesamt=None, versand=None,
                gebuehren=None, auszahlung=None, gesamtwert=None):
    with sqlite3.connect(db) as conn:
        cur = conn.execute(
            "INSERT INTO orders (buyer_name, email_message_id, date_received, "
            "status, order_number, amount_gesamt, amount_gesamtwert, "
            "amount_versand, amount_gebuehren, amount_auszahlung) "
            "VALUES ('Sharqy', ?, '2026-07-14T10:00:00', 'sold', ?, ?, ?, ?, ?, ?)",
            (mid, nummer, gesamt, gesamtwert, versand, gebuehren, auszahlung))
        conn.commit()
        return cur.lastrowid


def _lies(db, bestellung_id):
    with sqlite3.connect(db) as conn:
        conn.row_factory = sqlite3.Row
        return dict(conn.execute("SELECT * FROM orders WHERE id = ?",
                                 (bestellung_id,)).fetchone())


# ---------------------------------------------------------------------------
# Luecken erkennen
# ---------------------------------------------------------------------------
def test_findet_bestellung_ohne_betraege(db):
    _bestellung(db)
    with sqlite3.connect(db) as conn:
        assert len(nachlesen.offene_bestellungen(conn)) == 1


def test_vollstaendige_bestellung_taucht_nicht_auf(db):
    _bestellung(db, nummer="1287799674", gesamt=2.61, gesamtwert=1.06,
                versand=1.55, gebuehren=0.07, auszahlung=2.54)
    with sqlite3.connect(db) as conn:
        assert nachlesen.offene_bestellungen(conn) == []


def test_zeichenkette_none_gilt_als_leer(db):
    """In alten Zeilen steht als Bestellnummer woertlich die Zeichenkette None."""
    bid = _bestellung(db, nummer="None", gesamt=2.61, gesamtwert=1.06,
                      versand=1.55, gebuehren=0.07, auszahlung=2.54)
    with sqlite3.connect(db) as conn:
        offen = nachlesen.offene_bestellungen(conn)
    assert [z["id"] for z in offen] == [bid]


# ---------------------------------------------------------------------------
# Nachlesen
# ---------------------------------------------------------------------------
def test_traegt_alle_fehlenden_betraege_nach(db):
    bid = _bestellung(db)
    dienst = FalscherDienst({"msg-1": MAIL})

    with sqlite3.connect(db) as conn:
        ergebnis = nachlesen.lese_nach(conn, service=dienst, schreiben=True)

    assert ergebnis.ergaenzt == 1
    zeile = _lies(db, bid)
    assert zeile["amount_gesamt"] == 2.61
    assert zeile["amount_gesamtwert"] == 1.06
    assert zeile["amount_versand"] == 1.55
    assert zeile["amount_gebuehren"] == 0.07
    assert zeile["amount_auszahlung"] == 2.54
    assert zeile["order_number"] == "1287799674"


def test_vorschau_aendert_nichts(db):
    """Ohne ausdrueckliches Schreiben bleibt die Datenbank unberuehrt."""
    bid = _bestellung(db)
    vorher = _lies(db, bid)

    with sqlite3.connect(db) as conn:
        ergebnis = nachlesen.lese_nach(conn, service=FalscherDienst({"msg-1": MAIL}))

    assert ergebnis.ergaenzt == 1, "die Vorschau soll trotzdem berichten"
    assert _lies(db, bid) == vorher


def test_vorhandener_wert_wird_nie_ueberschrieben(db):
    """Der Kern: nur Luecken fuellen.

    Der Versand steht bereits mit 9,99 in der Zeile - vielleicht von Hand
    korrigiert. Die Mail sagt 1,55. Geaendert werden darf er nicht.
    """
    bid = _bestellung(db, versand=9.99)

    with sqlite3.connect(db) as conn:
        nachlesen.lese_nach(conn, service=FalscherDienst({"msg-1": MAIL}),
                            schreiben=True)

    zeile = _lies(db, bid)
    assert zeile["amount_versand"] == 9.99, "vorhandener Wert wurde ueberschrieben"
    assert zeile["amount_gesamt"] == 2.61, "die Luecken sollen gefuellt sein"


def test_fehlende_mail_wird_gemeldet_nicht_verschwiegen(db):
    _bestellung(db, mid="gibts-nicht")

    with sqlite3.connect(db) as conn:
        ergebnis = nachlesen.lese_nach(conn, service=FalscherDienst({}),
                                       schreiben=True)

    assert ergebnis.ohne_mail == 1
    assert ergebnis.ergaenzt == 0
    assert any("nicht mehr abrufbar" in m for m in ergebnis.meldungen)


def test_mail_ohne_betraege_laesst_die_felder_leer(db):
    """Lieber leer als geraten."""
    bid = _bestellung(db)
    ohne = "Bestellnummer: 1287799674\nKäufer: Sharqy\nStatus: Bezahlt\n"

    with sqlite3.connect(db) as conn:
        ergebnis = nachlesen.lese_nach(
            conn, service=FalscherDienst({"msg-1": ohne}), schreiben=True)

    zeile = _lies(db, bid)
    assert zeile["amount_gesamt"] is None
    assert zeile["order_number"] == "1287799674"   # was da war, wurde genommen
    assert ergebnis.ergaenzt == 1


def test_ohne_gmail_verbindung_passiert_nichts(db):
    bid = _bestellung(db)
    vorher = _lies(db, bid)

    with sqlite3.connect(db) as conn:
        ergebnis = nachlesen.lese_nach(conn, service=None, schreiben=True)

    assert _lies(db, bid) == vorher
    assert any("Gmail" in m for m in ergebnis.meldungen)


def test_bestand_und_positionen_bleiben_unberuehrt(db):
    """Nachlesen ist eine Sache der Betraege, nicht des Lagers."""
    with sqlite3.connect(db) as conn:
        conn.execute(
            "INSERT INTO cards (name, set_code, language, condition, price, "
            "quantity, storage_code, status, collector_number, foil, item_type, "
            "date_added) VALUES ('Force of Negation','mh1','en','NM',1.0,3,"
            "'O01-S01-P1','verfügbar','1',0,'card','2026-07-09T10:00:00')")
        conn.commit()
    bid = _bestellung(db)
    with sqlite3.connect(db) as conn:
        conn.execute("INSERT INTO order_items (order_id, card_name, quantity) "
                     "VALUES (?, 'Force of Negation', 1)", (bid,))
        conn.commit()

    def _abbild():
        with sqlite3.connect(db) as conn:
            return (conn.execute("SELECT * FROM cards").fetchall(),
                    conn.execute("SELECT * FROM order_items").fetchall())

    vorher = _abbild()
    with sqlite3.connect(db) as conn:
        nachlesen.lese_nach(conn, service=FalscherDienst({"msg-1": MAIL}),
                            schreiben=True)
    assert _abbild() == vorher


# ---------------------------------------------------------------------------
# Seite
# ---------------------------------------------------------------------------
def test_seite_zeigt_die_luecken(db):
    _bestellung(db)
    web.app.config["TESTING"] = True
    klient = web.app.test_client()
    with klient.session_transaction() as s:
        s["user"] = "tester"

    antwort = klient.get("/orders/nachlesen")
    assert antwort.status_code == 200
    text = antwort.get_data(as_text=True)
    assert "Sharqy" in text
    assert "gesamt" in text
