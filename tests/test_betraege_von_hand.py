"""Betraege von Hand erfassen, wenn die Mail sie nicht hergibt.

Der Regelfall bleibt: die Zahlen stammen aus der Bestell-Mail. Manche
Bestellungen geben sie aber nicht mehr her — die Mail wurde vor der
Betragserfassung eingelesen, war ein anderer Mailtyp, oder sie ist im Postfach
nicht mehr auffindbar. Ohne eine Eingabe von Hand blieben diese Bestellungen
fuer immer unbuchbar.

Zwei Dinge sind dabei wichtig und werden hier festgehalten:

1. **Dieselben Kontrollrechnungen wie in der Buchhaltung.** Was das Formular
   annimmt, muss dort buchbar sein — sonst scheitert die Eingabe erst drueben
   und niemand versteht, warum.
2. **Die Handeingabe bleibt sichtbar.** Eine von Hand gesetzte Zahl ist keine
   Quelle, sondern eine Entscheidung.
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
from TCGInventory import (api_v1, auth, betraege, lager_manager,  # noqa: E402
                          order_service, setup_db, web)
from TCGInventory.betraege import BetragFehler, parse_betrag, pruefe  # noqa: E402


# ---------------------------------------------------------------------------
# Eingaben lesen
# ---------------------------------------------------------------------------
@pytest.mark.parametrize("eingabe, cent", [
    ("2,61", 261),
    ("2.61", 261),
    ("0,07", 7),
    ("0,7", 70),        # eine Nachkommastelle sind Zehntel-Euro
    ("12", 1200),
    (" 3,95 € ", 395),
    ("", None),
    ("   ", None),
])
def test_betrag_lesen(eingabe, cent):
    assert parse_betrag(eingabe) == cent


@pytest.mark.parametrize("eingabe", ["abc", "1,2,3", "3,999", "1.2.3", "-,-"])
def test_unsinnige_eingabe_wird_abgelehnt(eingabe):
    with pytest.raises(BetragFehler):
        parse_betrag(eingabe)


def test_leer_ist_nicht_null():
    """Der Unterschied entscheidet ueber „fehlt" gegen „0,00 EUR"."""
    assert parse_betrag("") is None
    assert parse_betrag("0") == 0


def test_kein_fliesskomma_bei_krummen_betraegen():
    """0,07 muss 7 Cent sein — nicht 6,999999."""
    assert parse_betrag("0,07") == 7
    assert parse_betrag("1234,56") == 123456


# ---------------------------------------------------------------------------
# Kontrollrechnungen — wortgleich zur Buchhaltung
# ---------------------------------------------------------------------------
def _werte(gesamt=261, warenwert=106, versand=155, gebuehren=7, auszahlung=254):
    return {"gesamt": gesamt, "warenwert": warenwert, "versand": versand,
            "gebuehren": gebuehren, "auszahlung": auszahlung}


def test_stimmige_betraege_gehen_durch():
    assert pruefe(_werte()) == []


def test_pflichtfelder_werden_verlangt():
    fehler = pruefe(_werte(gesamt=None, versand=None, gebuehren=None))
    assert len(fehler) == 3
    assert any("Gesamtbetrag" in f for f in fehler)
    assert any("Versandkosten" in f for f in fehler)
    assert any("Gebühr" in f for f in fehler)


def test_auszahlung_muss_aufgehen():
    fehler = pruefe(_werte(auszahlung=200))
    assert len(fehler) == 1
    assert "Kontrollrechnung" in fehler[0]
    assert "2,54 €" in fehler[0]        # gerechnet: 261 - 7


def test_warenwert_plus_versand_muss_den_gesamtbetrag_ergeben():
    """Genau der Fall aus dem Betrieb: 182 + 155 gegen 182."""
    fehler = pruefe(_werte(gesamt=182, warenwert=182, versand=155,
                           gebuehren=3, auszahlung=179))
    assert len(fehler) == 1
    assert "3,37 €" in fehler[0]        # 182 + 155
    assert "1,82 €" in fehler[0]


def test_warenwert_darf_fehlen():
    """Gebucht wird ohnehin Gesamt minus Versand."""
    assert pruefe(_werte(warenwert=None)) == []


def test_auszahlung_darf_fehlen():
    assert pruefe(_werte(auszahlung=None)) == []


def test_gesamtbetrag_null_ist_kein_gueltiger_verkauf():
    fehler = pruefe(_werte(gesamt=0))
    assert any("größer als null" in f for f in fehler)


def test_vorschlag_fuer_die_auszahlung():
    assert betraege.vorschlag_auszahlung(261, 7) == 254
    assert betraege.vorschlag_auszahlung(None, 7) is None


# ---------------------------------------------------------------------------
# Speichern
# ---------------------------------------------------------------------------
@pytest.fixture()
def db(tmp_path):
    pfad = str(tmp_path / "b.db")
    for modul in (TCGInventory, web, auth, setup_db, order_service,
                  lager_manager, api_v1):
        modul.DB_FILE = pfad
    setup_db.initialize_database()
    with sqlite3.connect(pfad) as conn:
        conn.execute(
            "INSERT INTO orders (buyer_name, email_message_id, date_received, "
            "status) VALUES ('Raigom', 'msg-1', '2026-07-10T10:00:00', 'sold')")
        conn.commit()
    return pfad


def _zeile(db, order_id=1):
    with sqlite3.connect(db) as conn:
        conn.row_factory = sqlite3.Row
        return dict(conn.execute("SELECT * FROM orders WHERE id = ?",
                                 (order_id,)).fetchone())


def test_speichern_setzt_betraege_und_kennzeichnung(db):
    with sqlite3.connect(db) as conn:
        gesetzt, fehler = betraege.speichere(
            conn, 1, _werte(), "melvin", bestellnummer="1287799674")

    assert fehler == []
    assert gesetzt == 5
    zeile = _zeile(db)
    assert zeile["amount_gesamt"] == 2.61
    assert zeile["amount_gebuehren"] == 0.07
    assert zeile["order_number"] == "1287799674"
    assert zeile["betraege_manuell"] == 1
    assert zeile["betraege_von"] == "melvin"
    assert zeile["betraege_am"]


def test_bei_fehlern_wird_nichts_geschrieben(db):
    """Halb erfasst waere schlimmer als gar nicht erfasst."""
    with sqlite3.connect(db) as conn:
        gesetzt, fehler = betraege.speichere(
            conn, 1, _werte(auszahlung=999), "melvin")

    assert fehler and gesetzt == 0
    zeile = _zeile(db)
    assert zeile["amount_gesamt"] is None
    assert zeile["betraege_manuell"] in (0, None)


def test_unbekannte_bestellung(db):
    with sqlite3.connect(db) as conn:
        gesetzt, fehler = betraege.speichere(conn, 999, _werte(), "melvin")
    assert gesetzt == 0
    assert any("gibt es nicht" in f for f in fehler)


# ---------------------------------------------------------------------------
# Weg durch die Oberflaeche und die Schnittstelle
# ---------------------------------------------------------------------------
def _klient():
    web.app.config["TESTING"] = True
    klient = web.app.test_client()
    with klient.session_transaction() as s:
        s["user"] = "melvin"
    return klient


def test_formular_speichert_und_meldet(db):
    klient = _klient()
    antwort = klient.post("/orders/1/betraege", data={
        "order_number": "1287799674", "gesamt": "2,61", "warenwert": "1,06",
        "versand": "1,55", "gebuehren": "0,07", "auszahlung": "2,54",
    }, follow_redirects=True)

    assert antwort.status_code == 200
    assert _zeile(db)["amount_gesamt"] == 2.61


def test_formular_lehnt_unstimmige_eingabe_ab(db):
    klient = _klient()
    antwort = klient.post("/orders/1/betraege", data={
        "gesamt": "2,61", "warenwert": "1,06", "versand": "1,55",
        "gebuehren": "0,07", "auszahlung": "9,99",
    }, follow_redirects=True)

    assert "Kontrollrechnung" in antwort.get_data(as_text=True)
    assert _zeile(db)["amount_gesamt"] is None


def test_api_meldet_die_handeingabe(db, monkeypatch):
    """Die Buchhaltung soll erkennen koennen, dass jemand entschieden hat."""
    token = "t" * 40
    monkeypatch.setenv("TCG_API_TOKEN", token)
    with sqlite3.connect(db) as conn:
        betraege.speichere(conn, 1, _werte(), "melvin",
                           bestellnummer="1287799674")

    klient = _klient()
    daten = klient.get("/api/v1/orders",
                       headers={"Authorization": f"Bearer {token}"}).get_json()
    bestellung = daten["bestellungen"][0]
    assert bestellung["betraege_manuell"] is True
    assert bestellung["betraege_cent"]["gesamt"] == 261


def test_handeingabe_aendert_den_inhalts_hash(db, monkeypatch):
    """Sonst haelt die Buchhaltung die Bestellung fuer unveraendert."""
    token = "t" * 40
    monkeypatch.setenv("TCG_API_TOKEN", token)
    klient = _klient()
    kopf = {"Authorization": f"Bearer {token}"}

    vorher = klient.get("/api/v1/orders", headers=kopf).get_json()
    hash_vorher = vorher["bestellungen"][0]["inhalt_hash"]

    with sqlite3.connect(db) as conn:
        betraege.speichere(conn, 1, _werte(), "melvin")

    nachher = klient.get("/api/v1/orders", headers=kopf).get_json()
    assert nachher["bestellungen"][0]["inhalt_hash"] != hash_vorher


def test_nachlesen_ueberschreibt_die_handeingabe_nicht(db):
    """Was von Hand gesetzt wurde, darf kein spaeterer Lauf ueberschreiben."""
    from TCGInventory import nachlesen

    with sqlite3.connect(db) as conn:
        betraege.speichere(conn, 1, _werte(), "melvin",
                           bestellnummer="1287799674")

    # Vollstaendig erfasst -> das Nachlesen fasst sie gar nicht mehr an.
    with sqlite3.connect(db) as conn:
        assert nachlesen.offene_bestellungen(conn) == []

    # Und selbst wenn: gefuellt werden nur leere Felder.
    with sqlite3.connect(db) as conn:
        ergebnis = nachlesen.lese_nach(conn, service=None, schreiben=True)
    assert ergebnis.ergaenzt == 0
    assert _zeile(db)["amount_gesamt"] == 2.61
