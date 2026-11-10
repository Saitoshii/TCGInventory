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
import re
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


# ---------------------------------------------------------------------------
# Hilfen fuer die folgenden Abschnitte
# ---------------------------------------------------------------------------

def _produkt(db, name, *, cardmarket_name="", sprache="", menge=3, ort="Regal 2",
             item_type="display", status="verfügbar"):
    with sqlite3.connect(db) as conn:
        cur = conn.execute(
            "INSERT INTO cards (name, set_code, language, condition, price, "
            "quantity, storage_code, location_hint, status, collector_number, "
            "foil, item_type, cardmarket_name, date_added) VALUES "
            "(?, '', ?, '', 100.0, ?, '', ?, ?, '', 0, ?, ?, ?)",
            (name, sprache, menge, ort, status, item_type, cardmarket_name,
             datetime.now().isoformat(timespec="seconds")))
        conn.commit()
        return cur.lastrowid


def _position(db, zeile, *, card_id=None, nachricht="msg-1", nummer="1299999999"):
    """Eine offene Bestellung mit einer Position, wie der Parser sie liefert."""
    item = parse_position_line(zeile)
    jetzt = datetime.now().isoformat(timespec="seconds")
    with sqlite3.connect(db) as conn:
        cur = conn.execute(
            "INSERT INTO orders (buyer_name, email_message_id, date_received, "
            "status, order_number) VALUES ('Sharqy', ?, ?, 'open', ?)",
            (nachricht, jetzt, nummer))
        bid = cur.lastrowid
        cur = conn.execute(
            "INSERT INTO order_items (order_id, card_name, quantity, unit_price, "
            "language, set_name, card_id, match_status) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (bid, item["name"], item["quantity"], item["unit_price"],
             item["language"], item["set_name"], card_id,
             "matched" if card_id else "unresolved"))
        conn.commit()
        return cur.lastrowid


# ---------------------------------------------------------------------------
# Formular gleich fuer ein Produkt oeffnen
# ---------------------------------------------------------------------------

def test_formular_oeffnet_auf_wunsch_als_produkt(db):
    html = _klient().get("/cards/add?typ=display").get_data(as_text=True)
    assert '<option value="display" selected>' in html
    # Ohne Angabe bleibt es bei der Karte.
    html = _klient().get("/cards/add").get_data(as_text=True)
    assert '<option value="card" selected>' in html


def test_unbekannte_artikelart_wird_nicht_uebernommen(db):
    html = _klient().get("/cards/add?typ=<script>").get_data(as_text=True)
    assert '<option value="card" selected>' in html
    assert "<script>alert" not in html


def test_navigation_fuehrt_direkt_zum_produkt(db):
    html = _klient().get("/cards/add").get_data(as_text=True)
    assert "Produkt hinzufügen" in html
    assert "/cards/add?typ=display" in html


# ---------------------------------------------------------------------------
# Aus der Bestellposition heraus anlegen
# ---------------------------------------------------------------------------

def test_position_belegt_name_und_sprache_vor(db):
    item_id = _position(db, DISPLAY)
    html = _klient().get(f"/cards/add?position={item_id}").get_data(as_text=True)
    assert 'name="name" value="The Hobbit Play Booster Box"' in html
    assert re.search(r'name="cardmarket_name"[^>]*value="The Hobbit Play Booster Box"',
                     html), "der Name aus der Mail gehört ins Cardmarket-Feld"
    assert '<option value="en" selected>' in html
    assert '<option value="display" selected>' in html
    assert f'name="aus_position" value="{item_id}"' in html
    assert "1299999999" in html                       # woher die Vorgabe stammt


def test_menge_und_preis_werden_nicht_aus_der_bestellung_geraten(db):
    """Bestellt wurden zwei Stück zu 168 € — im Regal steht eine andere Menge,
    und 168 € ist der Verkaufspreis, nicht der Einkaufspreis."""
    item_id = _position(db, DISPLAY.replace("1x", "2x"))
    html = _klient().get(f"/cards/add?position={item_id}").get_data(as_text=True)
    assert 'name="price" value=""' in html
    assert 'name="quantity" min="1" value="1"' in html
    assert "168" not in html


def test_gekuerzter_name_wird_ohne_punkte_vorgeschlagen(db):
    item_id = _position(db, PRECON)
    html = _klient().get(f"/cards/add?position={item_id}").get_data(as_text=True)
    assert ('name="name" value="Commander: Magic: The Gathering | Teenage '
            'Mutant Ninja Turtles:"') in html
    assert "gekürzt" in html


def test_unbekannte_position_stoert_das_formular_nicht(db):
    antwort = _klient().get("/cards/add?position=4711")
    assert antwort.status_code == 200
    assert 'name="aus_position"' not in antwort.get_data(as_text=True)


def test_nach_dem_anlegen_zurueck_zur_position_und_zuordnen(db):
    """Der ganze Weg: Bestellung kommt, Artikel fehlt, anlegen, zuordnen —
    und die nächste gleiche Bestellung wird von selbst erkannt."""
    item_id = _position(db, DISPLAY)
    klient = _klient()

    antwort = klient.post("/cards/add", data=_formular(
        name="The Hobbit Play Booster Box", quantity="4", language="en",
        location_hint="Regal 2", cardmarket_name="The Hobbit Play Booster Box",
        aus_position=str(item_id)))
    assert antwort.status_code == 302
    assert f"/orders/items/{item_id}/zuordnen" in antwort.headers["Location"]

    # Angelegt, aber noch nicht zugeordnet — das bleibt ein bewusster Klick.
    kid = _artikel(db)[0]["id"]
    with sqlite3.connect(db) as conn:
        assert conn.execute("SELECT card_id FROM order_items WHERE id = ?",
                            (item_id,)).fetchone()[0] is None

    seite = klient.get(antwort.headers["Location"]).get_data(as_text=True)
    assert "The Hobbit Play Booster Box" in seite and "Zuordnen" in seite

    klient.post(f"/orders/items/{item_id}/assign", data={"card_id": str(kid)})
    with sqlite3.connect(db) as conn:
        zeile = conn.execute(
            "SELECT card_id, match_status, storage_code FROM order_items "
            "WHERE id = ?", (item_id,)).fetchone()
    assert zeile == (kid, "matched", "Regal 2")

    # Die nächste Bestellung desselben Artikels braucht keine Handarbeit mehr.
    assert _matche(db, DISPLAY)["card_id"] == kid


def test_bestelluebersicht_bietet_das_anlegen_an(db):
    item_id = _position(db, DISPLAY)
    html = _klient().get("/orders").get_data(as_text=True)
    assert f"/cards/add?position={item_id}" in html
    html = _klient().get(f"/orders/items/{item_id}/zuordnen").get_data(as_text=True)
    assert f"/cards/add?position={item_id}" in html


# ---------------------------------------------------------------------------
# Vorschlaege aus dem eigenen Bestand
# ---------------------------------------------------------------------------

def test_vorschlaege_nennen_vorhandene_produkte(db):
    kid = _produkt(db, "The Hobbit Play Booster Display", sprache="en",
                   cardmarket_name="The Hobbit Play Booster Box")
    daten = _klient().get("/api/produkte?q=hobbit").get_json()
    assert len(daten) == 1
    assert daten[0]["id"] == kid and daten[0]["vorhanden"] is True
    assert daten[0]["quantity"] == 3 and daten[0]["location_hint"] == "Regal 2"
    assert daten[0]["edit_url"].endswith(f"/cards/{kid}/edit")


def test_vorschlaege_finden_auch_ueber_den_cardmarket_namen(db):
    _produkt(db, "Kartons Regal 2 Nr. 7", cardmarket_name="The Hobbit Play Booster Box")
    assert len(_klient().get("/api/produkte?q=Play Booster").get_json()) == 1


def test_ausverkauftes_dient_als_vorlage_und_nur_einmal(db):
    """Jeder Nachkauf war bisher ein eigener Eintrag — als Vorlage genügt einer."""
    for _ in range(3):
        _produkt(db, "The Hobbit Play Booster Display", sprache="en", menge=0,
                 status="archiviert", cardmarket_name="The Hobbit Play Booster Box")
    daten = _klient().get("/api/produkte?q=hobbit").get_json()
    assert len(daten) == 1
    assert daten[0]["vorhanden"] is False
    assert daten[0]["cardmarket_name"] == "The Hobbit Play Booster Box"
    assert daten[0]["language"] == "en"


def test_vorhandenes_steht_vor_frueherem(db):
    _produkt(db, "Hobbit Display alt", menge=0, status="archiviert")
    kid = _produkt(db, "Hobbit Display neu")
    daten = _klient().get("/api/produkte?q=hobbit").get_json()
    assert [d["vorhanden"] for d in daten] == [True, False]
    assert daten[0]["id"] == kid


def test_vorschlaege_enthalten_keine_einzelkarten(db):
    with sqlite3.connect(db) as conn:
        conn.execute(
            "INSERT INTO cards (name, set_code, language, quantity, status, "
            "item_type) VALUES ('Smaug, Wicked Worm', 'tho', 'en', 1, "
            "'verfügbar', 'card')")
        conn.commit()
    assert _klient().get("/api/produkte?q=smaug").get_json() == []


def test_vorschlaege_behandeln_platzhalter_als_zeichen(db):
    _produkt(db, "Sleeves 100% Matte")
    _produkt(db, "Sleeves 100 Stück")
    daten = _klient().get("/api/produkte?q=100%").get_json()
    assert [d["name"] for d in daten] == ["Sleeves 100% Matte"]


def test_vorschlaege_brauchen_eine_anmeldung_und_zwei_zeichen(db):
    _produkt(db, "The Hobbit Play Booster Display")
    web.app.config["TESTING"] = True
    ohne = web.app.test_client().get("/api/produkte?q=hobbit")
    assert ohne.status_code in (302, 401) and not ohne.is_json
    assert _klient().get("/api/produkte?q=h").get_json() == []


# ---------------------------------------------------------------------------
# Am Artikel: wie hiess er in Bestellungen?
# ---------------------------------------------------------------------------

def test_artikel_zeigt_den_namen_aus_zugeordneten_bestellungen(db):
    kid = _produkt(db, "Kartons Regal 2 Nr. 7")           # ohne Cardmarket-Namen
    _position(db, DISPLAY, card_id=kid)
    html = _klient().get(f"/cards/{kid}/edit").get_data(as_text=True)
    assert "So stand dieser Artikel in Bestellungen" in html
    assert 'data-name="The Hobbit Play Booster Box" data-language="en"' in html


def test_artikel_meldet_wenn_er_schon_erkannt_wird(db):
    kid = _produkt(db, "Hobbit Display", sprache="en",
                   cardmarket_name="The Hobbit Play Booster Box")
    _position(db, DISPLAY, card_id=kid)
    html = _klient().get(f"/cards/{kid}/edit").get_data(as_text=True)
    assert "wird von selbst erkannt" in html
    assert "mailname-uebernehmen\"" not in html


def test_vorschlag_am_artikel_speichert_nichts_von_selbst(db):
    """Die Seite zeigt nur an. Erst „Speichern" schreibt."""
    kid = _produkt(db, "Kartons Regal 2 Nr. 7")
    _position(db, DISPLAY, card_id=kid)
    vorher = _artikel(db)
    _klient().get(f"/cards/{kid}/edit")
    assert _artikel(db) == vorher


def test_einzelkarten_bekommen_keinen_namensvorschlag(db):
    with sqlite3.connect(db) as conn:
        cur = conn.execute(
            "INSERT INTO cards (name, set_code, language, quantity, status, "
            "item_type, collector_number) VALUES ('Smaug, Wicked Worm', 'tho', "
            "'en', 1, 'verfügbar', 'card', '1')")
        conn.commit()
        kid = cur.lastrowid
    _position(db, "1x Smaug, Wicked Worm (The Hobbit) - M - Englisch - NM 2,99 EUR",
              card_id=kid)
    html = _klient().get(f"/cards/{kid}/edit").get_data(as_text=True)
    assert "So stand dieser Artikel in Bestellungen" not in html


# ---------------------------------------------------------------------------
# Bestelluebersicht: wird es naechstes Mal erkannt?
# ---------------------------------------------------------------------------

def test_hinweis_bei_von_hand_zugeordnetem_produkt(db):
    kid = _produkt(db, "Kartons Regal 2 Nr. 7")
    _position(db, DISPLAY, card_id=kid)
    html = _klient().get("/orders").get_data(as_text=True)
    assert "Nächstes Mal von selbst erkennen" in html
    assert f"/cards/{kid}/edit" in html


def test_kein_hinweis_wenn_der_artikel_erkannt_wird(db):
    kid = _produkt(db, "Hobbit Display", sprache="en",
                   cardmarket_name="The Hobbit Play Booster Box")
    _position(db, DISPLAY, card_id=kid)
    assert "Nächstes Mal von selbst erkennen" not in \
        _klient().get("/orders").get_data(as_text=True)


def test_kein_hinweis_bei_einzelkarten(db):
    with sqlite3.connect(db) as conn:
        cur = conn.execute(
            "INSERT INTO cards (name, set_code, language, quantity, status, "
            "item_type, collector_number) VALUES ('Smaug, Wicked Worm', 'tho', "
            "'en', 1, 'verfügbar', 'card', '1')")
        conn.commit()
    _position(db, "1x Smaug, Wicked Worm (The Hobbit) - M - Englisch - NM 2,99 EUR",
              card_id=cur.lastrowid)
    assert "Nächstes Mal von selbst erkennen" not in \
        _klient().get("/orders").get_data(as_text=True)


# ---------------------------------------------------------------------------
# produkt_passt() und der Bestelleingang muessen dasselbe sagen
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("cardmarket_name,sprache,zeile", [
    ("The Hobbit Play Booster Box", "en", DISPLAY),         # passt
    ("The Hobbit Play Booster Box", "de", DISPLAY),         # falsche Sprache
    ("The Hobbit Play Booster Box", "", DISPLAY),           # Sprache fehlt
    ("the hobbit play booster box", "en", DISPLAY),         # Schreibweise
    ("The Hobbit Collector Booster Box", "en", DISPLAY),    # anderer Artikel
    ("", "en", DISPLAY),                                    # nichts hinterlegt
    ("Commander: Magic: The Gathering | Teenage Mutant Ninja Turtles: "
     "Mutant Mayhem", "", PRECON),                          # gekuerzt, passt
    ("Commander: Magic: The Gathering | Bloomburrow: Animated Army", "",
     PRECON),                                               # gekuerzt, anderer
])
def test_hinweis_und_bestelleingang_sind_sich_einig(db, cardmarket_name, sprache,
                                                    zeile):
    kid = _produkt(db, "Eigener Name", cardmarket_name=cardmarket_name,
                   sprache=sprache)
    item = parse_position_line(zeile)
    erkannt = _matche(db, zeile)["card_id"] == kid
    assert order_service.produkt_passt(
        cardmarket_name, sprache, item["name"], item["language"]) is erkannt
