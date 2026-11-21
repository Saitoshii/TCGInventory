"""Direktverkauf: Karten aus dem Bestand finden — und wirklich ausbuchen.

Der Name war ein reines Textfeld. Mit dem Bestand verknüpft wurde eine Karte
nur, wenn man aus der Kartenliste über „Verkauft" kam. Eine eingetippte Karte
wurde verkauft, aber nicht ausgebucht — sie blieb im Bestand stehen.
"""

import os
import re
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
from TCGInventory import (auth, direktverkauf, lager_manager,     # noqa: E402
                          order_service, setup_db, web)


@pytest.fixture()
def lager(tmp_path, monkeypatch):
    pfad = str(tmp_path / "lager.db")
    for modul in (TCGInventory, web, auth, setup_db, order_service, lager_manager,
                  direktverkauf):
        monkeypatch.setattr(modul, "DB_FILE", pfad)
    setup_db.initialize_database()
    fid = lager_manager.add_folder("Star Trek", 2)
    lager_manager.create_binder(fid, 2)
    lager_manager.add_card("Spock, Logical Officer", "trk", "en", "NM", 0.2, 3,
                           folder_id=fid, collector_number="137",
                           image_url="https://bild/spock.jpg")
    lager_manager.add_card("Spock, Logical Officer", "trk", "en", "NM", 0.5, 1,
                           folder_id=fid, collector_number="137", foil=True)
    lager_manager.add_card("Data", "trk", "en", "NM", 0.2, 0, folder_id=fid,
                           collector_number="55")                    # Menge 0
    lager_manager.add_card("Hobbit Play Booster Display", "", "en", "", 100.0, 2,
                           item_type="display", location_hint="Regal A",
                           cardmarket_name="The Hobbit Play Booster Box")
    return pfad


def _klient():
    web.app.config["TESTING"] = True
    klient = web.app.test_client()
    with klient.session_transaction() as s:
        s["user"] = "melvin"
    return klient


def _q(pfad, sql, *werte):
    with sqlite3.connect(pfad) as conn:
        return conn.execute(sql, werte).fetchall()


def _id(pfad, name, foil=0):
    return _q(pfad, "SELECT id FROM cards WHERE name = ? AND foil = ?", name, foil)[0][0]


# ---------------------------------------------------------------------------
# Suche im Bestand
# ---------------------------------------------------------------------------
def test_suche_findet_karten_mit_allem_was_man_zum_finden_braucht(lager):
    treffer = _klient().get("/api/bestand?q=spock").get_json()
    assert [(t["name"], t["foil"], t["quantity"]) for t in treffer] == [
        ("Spock, Logical Officer", False, 3), ("Spock, Logical Officer", True, 1)]
    erster = treffer[0]
    assert erster["set_code"] == "TRK" and erster["collector_number"] == "137"
    assert erster["language"] == "EN" and erster["condition"] == "NM"
    assert erster["platz"].startswith("O01-S01-P")
    assert erster["image_url"] == "https://bild/spock.jpg"


def test_suche_mit_mehreren_woertern_und_nummer(lager):
    assert len(_klient().get("/api/bestand?q=logical spock").get_json()) == 2
    assert len(_klient().get("/api/bestand?q=spock 137").get_json()) == 2
    assert _klient().get("/api/bestand?q=spock kirk").get_json() == []


def test_suche_findet_produkte_auch_ueber_den_cardmarket_namen(lager):
    treffer = _klient().get("/api/bestand?q=hobbit box").get_json()
    assert [t["name"] for t in treffer] == ["Hobbit Play Booster Display"]
    assert treffer[0]["platz"] == "Regal A" and treffer[0]["item_type"] == "display"


def test_suche_zeigt_nichts_was_nicht_da_ist(lager):
    assert _klient().get("/api/bestand?q=data").get_json() == []       # Menge 0
    assert _klient().get("/api/bestand?q=s").get_json() == []          # zu kurz


def test_genauer_name_steht_vorne(lager):
    lager_manager.add_card("Spockification", "trk", "en", "NM", 0.2, 1,
                           collector_number="999")
    lager_manager.add_card("Spock", "trk", "en", "NM", 0.2, 1, collector_number="998")
    namen = [t["name"] for t in _klient().get("/api/bestand?q=Spock").get_json()]
    assert namen[0] == "Spock"


def test_suche_nur_mit_anmeldung(lager):
    web.app.config["TESTING"] = True
    assert web.app.test_client().get("/api/bestand?q=spock").status_code == 302


# ---------------------------------------------------------------------------
# Sicherheitsnetz: eingetippter Name, den es im Bestand gibt
# ---------------------------------------------------------------------------
def test_eingetippter_name_aus_dem_bestand_wird_nicht_still_verkauft(lager):
    html = _klient().post("/orders/neu", data={
        "card_id": "", "card_name": "spock, logical officer", "quantity": "1",
        "unit_price": "2,00", "ohne_bestand": "0", "kaeufer": "Laufkundschaft",
    }, follow_redirects=True).get_data(as_text=True)
    assert "liegt im Bestand (2 Eintrag/Einträge, 4 Stück)" in html
    assert "aus der Vorschlagsliste wählen" in html
    assert _q(lager, "SELECT COUNT(*) FROM orders") == [(0,)]
    # Die Eingaben bleiben stehen.
    assert 'value="spock, logical officer"' in html
    assert 'value="2,00"' in html and 'value="Laufkundschaft"' in html


def test_ausdruecklich_nicht_im_bestand(lager):
    antwort = _klient().post("/orders/neu", data={
        "card_id": "", "card_name": "Spock, Logical Officer", "quantity": "1",
        "unit_price": "2,00", "ohne_bestand": "1"})
    assert "/beleg" in antwort.headers["Location"]
    assert _q(lager, "SELECT card_id FROM order_items") == [(None,)]
    assert _q(lager, "SELECT SUM(quantity) FROM cards WHERE name LIKE 'Spock%'") == [(4,)]


def test_produkt_ueber_den_cardmarket_namen_eingetippt(lager):
    html = _klient().post("/orders/neu", data={
        "card_name": "The Hobbit Play Booster Box", "quantity": "1",
        "unit_price": "120"}, follow_redirects=True).get_data(as_text=True)
    assert "liegt im Bestand" in html


def test_ausgewaehlte_karte_wird_ausgebucht(lager):
    kid = _id(lager, "Spock, Logical Officer")
    antwort = _klient().post("/orders/neu", data={
        "card_id": str(kid), "card_name": "Spock, Logical Officer",
        "quantity": "2", "unit_price": "2,00", "ohne_bestand": "0"})
    assert "/beleg" in antwort.headers["Location"]
    assert _q(lager, "SELECT quantity FROM cards WHERE id = ?", kid) == [(1,)]


def test_dieselbe_karte_in_zwei_zeilen_nicht_mehr_als_da_ist(lager):
    kid = _id(lager, "Spock, Logical Officer")
    html = _klient().post("/orders/neu", data={
        "card_id": [str(kid), str(kid)], "card_name": ["Spock", "Spock"],
        "quantity": ["2", "2"], "unit_price": ["1", "1"],
        "ohne_bestand": ["0", "0"]}, follow_redirects=True).get_data(as_text=True)
    assert "nur 3 Stück im Bestand, verkauft werden sollen 4" in html
    assert _q(lager, "SELECT quantity FROM cards WHERE id = ?", kid) == [(3,)]
    assert _q(lager, "SELECT COUNT(*) FROM orders") == [(0,)]


# ---------------------------------------------------------------------------
# Formular
# ---------------------------------------------------------------------------
def test_formular_hat_die_bestandssuche(lager):
    html = _klient().get("/orders/neu").get_data(as_text=True)
    assert "artikel-name" in html and "bestand-vorschlaege" in html
    assert "/api/bestand" in html
    assert 'name="ohne_bestand"' in html
    assert 'id="zeile-vorlage"' in html


def test_nach_einem_fehler_bleibt_die_gewaehlte_karte_stehen(lager):
    kid = _id(lager, "Spock, Logical Officer")
    html = _klient().post("/orders/neu", data={
        "card_id": [str(kid), ""], "card_name": ["Spock, Logical Officer", "Wühlkiste"],
        "quantity": ["1", "1"], "unit_price": ["2,00", "teuer"],
        "ohne_bestand": ["0", "0"]}, follow_redirects=True).get_data(as_text=True)
    assert "kein Preis" in html
    assert f'name="card_id" value="{kid}"' in html
    assert "wird beim Speichern ausgebucht" in html
    assert 'value="Wühlkiste"' in html and 'value="teuer"' in html


def test_nach_einem_fehler_geht_dasselbe_formular_noch_einmal(lager):
    """Die Formular-Kennung wird nur verbraucht, wenn wirklich etwas angelegt
    wurde — sonst hieße es beim zweiten Versuch „schon erfasst"."""
    klient = _klient()
    kennung = re.search(r'name="formular_id" value="([0-9a-f]+)"',
                        klient.get("/orders/neu").get_data(as_text=True)).group(1)
    daten = {"card_name": "Wühlkiste", "quantity": "1", "unit_price": "teuer",
             "ohne_bestand": "0", "formular_id": kennung}
    assert "kein Preis" in klient.post("/orders/neu", data=daten,
                                       follow_redirects=True).get_data(as_text=True)
    daten["unit_price"] = "2,00"
    antwort = klient.post("/orders/neu", data=daten)
    assert "/beleg" in antwort.headers["Location"]
    # Und ein dritter Versuch mit derselben Kennung ist ein Doppelklick.
    html = klient.post("/orders/neu", data=daten, follow_redirects=True).get_data(as_text=True)
    assert "Nicht noch einmal ausgebucht" in html
    assert _q(lager, "SELECT COUNT(*) FROM orders") == [(1,)]


def test_vorauswahl_aus_der_kartenliste(lager):
    kid = _id(lager, "Spock, Logical Officer")
    html = _klient().get(f"/orders/neu?card_id={kid}").get_data(as_text=True)
    assert f'name="card_id" value="{kid}"' in html
    assert 'value="Spock, Logical Officer"' in html
    assert "3 vorrätig — wird beim Speichern ausgebucht" in html
    assert 'max="3"' in html
