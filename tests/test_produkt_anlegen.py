"""Displays, Precons und Zubehör ins System bringen.

Einzelkarten „rasten ein": Name tippen, Scryfall füllt den Rest, die Bestellung
findet sie über Set, Nummer, Sprache und Foil. Für Produkte gibt es das nicht —
sie stehen in keiner Kartendatenbank. Diese Tests halten fest, was das Anlegen
trotzdem verlässlich macht.

Der gemeldete Fehler dahinter: ein über das Formular angelegtes Display wurde
von der Bestellung **nie** gefunden, auch wenn der Cardmarket-Name genau
stimmte. Die Sprachauswahl stand im Block für Einzelkarten und war bei
Produkten ausgeblendet; gespeichert wurde eine leere Sprache. Der
Bestelleingang vergleicht die Sprache aber, sobald die Mail eine nennt.
"""

import os
import sqlite3
import sys
import types
from datetime import datetime

import pytest

sys.modules.setdefault("cv2", types.SimpleNamespace())
_pyz = types.ModuleType("pyzbar")
_pyz.pyzbar = types.SimpleNamespace(decode=lambda *a, **k: [])
sys.modules.setdefault("pyzbar", _pyz)
sys.modules.setdefault("pyzbar.pyzbar", _pyz.pyzbar)

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(__file__))))

import TCGInventory                                               # noqa: E402
from TCGInventory import (auth, lager_manager, order_service,     # noqa: E402
                          setup_db, web)
from TCGInventory.email_parser import parse_position_line         # noqa: E402

DISPLAY = "1x The Hobbit Play Booster Box (The Hobbit) - English 168,00 EUR"
PRECON = ("1x Commander: Magic: The Gathering | Teenage Mutant Ninja "
          "Turtles: ... 48,50 EUR")


@pytest.fixture()
def db(tmp_path):
    pfad = str(tmp_path / "p.db")
    for modul in (TCGInventory, web, auth, setup_db, order_service, lager_manager):
        modul.DB_FILE = pfad
    setup_db.initialize_database()
    return pfad


def _klient():
    web.app.config["TESTING"] = True
    klient = web.app.test_client()
    with klient.session_transaction() as s:
        s["user"] = "melvin"
    return klient


def _formular(**felder):
    """Die Felder, die der Browser beim Speichern eines Artikels schickt."""
    daten = {
        "item_type": "display", "name": "", "price": "", "quantity": "1",
        "language": "", "location_hint": "", "cardmarket_name": "",
        "condition": "", "folder_part": "", "page": "", "slot": "",
        "cardmarket_id": "", "collector_number": "", "scryfall_id": "",
        "folder_id": "", "image_url": "",
    }
    daten.update(felder)
    return daten


def _matche(db, zeile):
    item = parse_position_line(zeile)
    with sqlite3.connect(db) as conn:
        return order_service.OrderIngestionService()._match_item(
            conn.cursor(), item)


def _artikel(db):
    with sqlite3.connect(db) as conn:
        conn.row_factory = sqlite3.Row
        return [dict(z) for z in conn.execute(
            "SELECT id, name, item_type, language, quantity, price, "
            "location_hint, cardmarket_name FROM cards ORDER BY id")]


# ---------------------------------------------------------------------------
# Die Sprache gehört zu jeder Artikelart
# ---------------------------------------------------------------------------

def test_sprachauswahl_steht_ausserhalb_des_kartenblocks(db):
    """Im Kartenblock wird sie bei Produkten ausgeblendet — genau das war der
    Fehler. Sie muss davor stehen, bei den Feldern für alle Artikelarten."""
    html = _klient().get("/cards/add").get_data(as_text=True)
    sprache = html.index('name="language"')
    kartenblock = html.index('id="card-only-fields"')
    assert sprache < kartenblock


def test_ueber_das_formular_angelegtes_display_wird_von_der_bestellung_gefunden(db):
    antwort = _klient().post("/cards/add", data=_formular(
        name="The Hobbit Play Booster Display", price="120", quantity="3",
        language="en", location_hint="Regal 2",
        cardmarket_name="The Hobbit Play Booster Box"))
    assert antwort.status_code == 302

    artikel = _artikel(db)
    assert len(artikel) == 1 and artikel[0]["language"] == "en"
    treffer = _matche(db, DISPLAY)
    assert treffer["match_status"] == "matched"
    assert treffer["card_id"] == artikel[0]["id"]
    assert treffer["storage_code"] == "Regal 2"      # der Standort, kein Ordnerplatz


def test_ohne_sprache_wird_weiterhin_nicht_geraten(db):
    """Fehlt die Sprache am Artikel, bleibt es bei der Handzuordnung: ob die
    englische oder die deutsche Box im Regal steht, weiß das Programm nicht."""
    _klient().post("/cards/add", data=_formular(
        name="The Hobbit Play Booster Display", quantity="3",
        cardmarket_name="The Hobbit Play Booster Box"))
    assert _matche(db, DISPLAY)["match_status"] != "matched"


def test_sprache_laesst_sich_am_vorhandenen_produkt_nachtragen(db):
    """Bereits angelegte Produkte haben eine leere Sprache. Nach dem
    Nachtragen findet die Bestellung sie."""
    klient = _klient()
    klient.post("/cards/add", data=_formular(
        name="The Hobbit Play Booster Display", quantity="3",
        location_hint="Regal 2", cardmarket_name="The Hobbit Play Booster Box"))
    kid = _artikel(db)[0]["id"]

    html = klient.get(f"/cards/{kid}/edit").get_data(as_text=True)
    assert html.index('name="language"') < html.index('id="card-only-fields"')

    klient.post(f"/cards/{kid}/edit", data=_formular(
        name="The Hobbit Play Booster Display", quantity="3", language="en",
        location_hint="Regal 2", cardmarket_name="The Hobbit Play Booster Box"))
    assert _matche(db, DISPLAY)["card_id"] == kid
