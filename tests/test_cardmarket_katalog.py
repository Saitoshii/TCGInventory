"""Cardmarket-Produktkatalog: lokal vorhalten, aktuell halten, vorschlagen.

Einzelkarten rasten über die lokalen Scryfall-Daten ein. Für Displays, Precons
und Zubehör gibt es jetzt dasselbe: Cardmarkets öffentlicher Produktkatalog
liegt lokal vor und wird im Produktformular vorgeschlagen.

Kein Test geht ins Netz. Die Antworten von Cardmarket werden nachgestellt —
samt der Fälle, in denen etwas Unbrauchbares zurückkommt. Dann muss der
bisherige Katalog unangetastet bleiben.
"""

import json
import os
import sqlite3
import sys
import types
from datetime import datetime, timedelta

import pytest

sys.modules.setdefault("cv2", types.SimpleNamespace())
_pyz = types.ModuleType("pyzbar")
_pyz.pyzbar = types.SimpleNamespace(decode=lambda *a, **k: [])
sys.modules.setdefault("pyzbar", _pyz)
sys.modules.setdefault("pyzbar.pyzbar", _pyz.pyzbar)

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(__file__))))

import TCGInventory                                               # noqa: E402
from TCGInventory import (auth, cardmarket_katalog as katalog,    # noqa: E402
                          lager_manager, order_service, setup_db, web)
from TCGInventory.email_parser import parse_position_line         # noqa: E402
from TCGInventory.scripts import backup                           # noqa: E402

NONSINGLES, ACCESSORIES = katalog.QUELLEN


# ---------------------------------------------------------------------------
# Nachgestellter Katalog
# ---------------------------------------------------------------------------
def _eintrag(nummer, name, kategorie, datum="2020-01-01"):
    return {"idProduct": nummer, "name": name, "idCategory": 1,
            "categoryName": kategorie, "idExpansion": 7, "idMetacard": 0,
            "dateAdded": f"{datum} 00:00:00"}


def _datei(eintraege, stand="2026-11-08T13:01:06+0100"):
    return json.dumps({"version": 1, "createdAt": stand,
                       "products": eintraege}).encode("utf-8")


def _fuellung(start, kategorie, anzahl=120):
    """Genug Einträge, damit die Datei als vollständiger Katalog gilt."""
    return [_eintrag(start + i, f"Füllprodukt {start + i}", kategorie)
            for i in range(anzahl)]


VERSIEGELT = _fuellung(100000, "MtG Set") + [
    _eintrag(885551, "The Hobbit Play Booster Box", "Magic Display", "2026-05-02"),
    _eintrag(885552, "The Hobbit Collector Booster Box", "Magic Display", "2026-05-02"),
    _eintrag(885549, "The Hobbit Play Booster", "Magic Booster", "2026-05-02"),
    _eintrag(851272, "Lorwyn Eclipsed Play Booster Box", "Magic Display", "2025-09-29"),
    _eintrag(853738, 'Commander: Magic: The Gathering | Teenage Mutant Ninja '
                     'Turtles: ""Turtle Power!"" Commander Deck', "Magic Intropack",
             "2025-10-13"),
    _eintrag(853739, 'Commander: Magic: The Gathering | Teenage Mutant Ninja '
                     'Turtles: ""Shell Shock"" Commander Deck', "Magic Intropack",
             "2025-10-13"),
    _eintrag(680815, "Secret Lair: The Lands", "MtG Set", "2022-10-01"),
    _eintrag(680825, "Secret Lair: The Lands", "MtG Set", "2022-10-01"),
    _eintrag(200029, "German Magic 1 Startgeld", "Magic Event Tickets"),
    _eintrag(300001, "  Leerzeichen  am   Rand ", "MtG Set"),
]
ZUBEHOER = _fuellung(500000, "Sleeves") + [
    # Neuer als das Booster-Display und ebenfalls mit „Box" im Namen — so wie
    # im echten Katalog, wo Deckboxen die Displays aus der Liste drängten.
    _eintrag(866455, 'Lorwyn Eclipsed: ""Bitterbloom Bearer"" Xenoskin Deck Box',
             "DeckBoxes", "2025-12-01"),
    _eintrag(866456, 'Lorwyn Eclipsed: ""Dawnhand Eulogist"" Xenoskin Deck Box',
             "DeckBoxes", "2025-12-01"),
    _eintrag(747481, "100 Dragon Shield Sleeves - Matte Black (Non-Glare) (Version 2)",
             "Sleeves", "2023-12-01"),
    _eintrag(780843, 'Bloomburrow: ""Dawn\'s Truce"" Stitched Playmat', "Playmats",
             "2024-07-29"),
    _eintrag(600001, "Sleeves 100% Matte", "Sleeves"),
    _eintrag(600002, "Sleeves 100 Stück", "Sleeves"),
]


class Cardmarket:
    """Stellt die beiden Dateien bereit und merkt sich, was abgerufen wurde."""

    def __init__(self):
        self.dateien = {NONSINGLES.datei: (_datei(VERSIEGELT), '"v1"'),
                        ACCESSORIES.datei: (_datei(ZUBEHOER), '"z1"')}
        self.abrufe = []

    def __call__(self, url, etag=None):
        datei = url.rsplit("/", 1)[1]
        daten, aktuell = self.dateien[datei]
        self.abrufe.append((datei, etag))
        if etag is not None and etag == aktuell:
            return 304, None, aktuell
        return 200, daten, aktuell

    def geladen(self):
        """Wie oft wirklich Daten flossen (nicht nur nachgefragt wurde)."""
        return sum(1 for _, etag in self.abrufe if etag is None)


@pytest.fixture()
def cm():
    return Cardmarket()


@pytest.fixture()
def pfad(tmp_path):
    return tmp_path / "data" / "cardmarket-products.db"


@pytest.fixture()
def gefuellt(pfad, cm):
    katalog.aktualisiere(pfad, hole=cm)
    return pfad


# ---------------------------------------------------------------------------
# Namen und Artikelart
# ---------------------------------------------------------------------------
def test_doppelte_anfuehrungszeichen_werden_einfache():
    """Cardmarkets Export schreibt ``""Turtle Power!""`` — gemeint ist eines."""
    assert katalog.bereinige_name('X: ""Turtle Power!"" Deck') == 'X: "Turtle Power!" Deck'
    assert katalog.bereinige_name("  Leerzeichen  am   Rand ") == "Leerzeichen am Rand"
    assert katalog.bereinige_name(None) == ""


def test_artikelart_richtet_sich_nach_datei_und_kategorie():
    assert katalog.artikelart(NONSINGLES, "Magic Display") == "display"
    assert katalog.artikelart(NONSINGLES, "Magic Intropack") == "display"
    assert katalog.artikelart(NONSINGLES, "Magic Event Tickets") == "sonstiges"
    assert katalog.artikelart(ACCESSORIES, "Sleeves") == "zubehoer"
    for art in ("display", "zubehoer", "sonstiges"):
        assert art in lager_manager.ITEM_TYPES


# ---------------------------------------------------------------------------
# Lesen einer Katalogdatei
# ---------------------------------------------------------------------------
def test_datei_wird_gelesen_und_bereinigt():
    stand, zeilen = katalog.lies_produkte(_datei(VERSIEGELT), NONSINGLES)
    assert stand == "2026-11-08T13:01:06+0100"
    nach_nummer = {z[0]: z for z in zeilen}
    assert nach_nummer[885551][1] == "The Hobbit Play Booster Box"
    assert nach_nummer[885551][5] == "2026-05-02"
    assert nach_nummer[853738][1].endswith('"Turtle Power!" Commander Deck')
    assert nach_nummer[300001][1] == "Leerzeichen am Rand"
    assert nach_nummer[200029][7] == "sonstiges"


def test_unbrauchbare_eintraege_werden_uebersprungen():
    eintraege = _fuellung(1, "MtG Set") + [
        {"idProduct": "keine Zahl", "name": "A"}, {"idProduct": 9, "name": "  "},
        {"name": "ohne Nummer"}, "kein Objekt", {"idProduct": True, "name": "B"},
    ]
    _, zeilen = katalog.lies_produkte(_datei(eintraege), NONSINGLES)
    assert len(zeilen) == 120


@pytest.mark.parametrize("roh", [
    b"<html>Wartungsarbeiten</html>",
    b'{"version": 1}',
    b'{"products": "keine Liste"}',
    b"[]",
    _datei([_eintrag(1, "Einziges Produkt", "MtG Set")]),
    "kein utf-8 \xff".encode("latin-1"),
])
def test_was_kein_katalog_ist_wird_abgelehnt(roh):
    with pytest.raises(katalog.KatalogFehler):
        katalog.lies_produkte(roh, NONSINGLES)


# ---------------------------------------------------------------------------
# Aktualisieren
# ---------------------------------------------------------------------------
def test_erster_lauf_legt_den_katalog_an(pfad, cm):
    ergebnis = katalog.aktualisiere(pfad, hole=cm)
    assert ergebnis["aktualisiert"] is True
    assert ergebnis["anzahl"] == len(VERSIEGELT) + len(ZUBEHOER)
    stand = katalog.info(pfad)
    assert stand["vorhanden"] and stand["anzahl"] == ergebnis["anzahl"]
    assert [q["anzahl"] for q in stand["quellen"]] == [len(VERSIEGELT), len(ZUBEHOER)]
    assert stand["stand"].startswith("2026-11-08") and stand["geprueft_am"]


def test_unveraendert_wird_nicht_noch_einmal_geladen(gefuellt, cm):
    geladen = cm.geladen()
    ergebnis = katalog.aktualisiere(gefuellt, hole=cm)
    assert ergebnis["aktualisiert"] is False
    assert cm.geladen() == geladen, "nur nachgefragt, nichts heruntergeladen"
    assert ergebnis["anzahl"] == len(VERSIEGELT) + len(ZUBEHOER)


def test_neue_fassung_wird_uebernommen(gefuellt, cm):
    neu = ZUBEHOER + [_eintrag(999001, "Ganz neue Spielmatte", "Playmats", "2026-11-09")]
    cm.dateien[ACCESSORIES.datei] = (_datei(neu, "2026-11-09T13:00:00+0100"), '"z2"')
    ergebnis = katalog.aktualisiere(gefuellt, hole=cm)
    assert ergebnis["aktualisiert"] is True
    assert [t["name"] for t in katalog.suche("ganz neue", db_path=gefuellt)] == \
        ["Ganz neue Spielmatte"]
    # Die andere Hälfte ist weiterhin da.
    assert katalog.finde_genau("The Hobbit Play Booster Box", gefuellt)


def test_erzwingen_laedt_auch_ohne_neue_fassung(gefuellt, cm):
    geladen = cm.geladen()
    assert katalog.aktualisiere(gefuellt, erzwingen=True, hole=cm)["aktualisiert"]
    assert cm.geladen() == geladen + 2


@pytest.mark.parametrize("kaputt", [
    b"<html>503 Service Unavailable</html>",
    _datei([]),
    _datei([_eintrag(1, "Nur eins", "Sleeves")]),
])
def test_kaputte_lieferung_laesst_den_alten_katalog_stehen(gefuellt, cm, kaputt):
    """Der wichtigste Fall: Cardmarket liefert Unsinn — und nichts geht verloren."""
    cm.dateien[ACCESSORIES.datei] = (kaputt, '"z-kaputt"')
    with pytest.raises(katalog.KatalogFehler):
        katalog.aktualisiere(gefuellt, hole=cm)
    assert katalog.anzahl_produkte(gefuellt) == len(VERSIEGELT) + len(ZUBEHOER)
    assert katalog.finde_genau("Sleeves 100% Matte", gefuellt)
    assert not list(gefuellt.parent.glob("*.neu")), "keine halbe Datei zurücklassen"


def test_nicht_erreichbar_laesst_den_alten_katalog_stehen(gefuellt):
    def kein_netz(url, etag=None):
        raise katalog.KatalogFehler("Cardmarket ist nicht erreichbar: Name or service not known")
    with pytest.raises(katalog.KatalogFehler):
        katalog.aktualisiere(gefuellt, erzwingen=True, hole=kein_netz)
    assert katalog.anzahl_produkte(gefuellt) == len(VERSIEGELT) + len(ZUBEHOER)


def test_zu_grosse_antwort_wird_abgelehnt(monkeypatch):
    """Schutz für den Pi: eine unerwartet riesige Datei wird nicht eingelesen."""
    import urllib.request

    class Antwort:
        status = 200
        headers = {"ETag": '"x"'}
        def read(self, n=-1):
            return b"x" * n
        def __enter__(self):
            return self
        def __exit__(self, *a):
            return False

    monkeypatch.setattr(katalog, "MAX_BYTES", 1000)
    monkeypatch.setattr(urllib.request, "urlopen", lambda *a, **k: Antwort())
    with pytest.raises(katalog.KatalogFehler, match="größer als erwartet"):
        katalog.hole_von_cardmarket("https://example.invalid/x.json")


def test_abruf_fragt_mit_etag_und_versteht_unveraendert(monkeypatch):
    import urllib.error
    import urllib.request
    gesehen = {}

    def urlopen(anfrage, timeout=0):
        gesehen["etag"] = anfrage.get_header("If-none-match")
        gesehen["kennung"] = anfrage.get_header("User-agent")
        raise urllib.error.HTTPError(anfrage.full_url, 304, "Not Modified", {}, None)

    monkeypatch.setattr(urllib.request, "urlopen", urlopen)
    assert katalog.hole_von_cardmarket("https://example.invalid/x.json", '"v1"') == \
        (304, None, '"v1"')
    assert gesehen["etag"] == '"v1"'
    assert "@" not in gesehen["kennung"], "keine persönlichen Angaben in der Kennung"


# ---------------------------------------------------------------------------
# Suchen
# ---------------------------------------------------------------------------
def test_suche_verlangt_alle_woerter(gefuellt):
    namen = [t["name"] for t in katalog.suche("hobbit play box", db_path=gefuellt)]
    assert namen == ["The Hobbit Play Booster Box"]


def test_suche_stellt_den_namensanfang_nach_vorn(gefuellt):
    namen = [t["name"] for t in katalog.suche("the hobbit play", db_path=gefuellt)]
    assert namen[:2] == ["The Hobbit Play Booster", "The Hobbit Play Booster Box"]


def test_suche_bevorzugt_neue_produkte(gefuellt):
    namen = [t["name"] for t in katalog.suche("play booster box", db_path=gefuellt)]
    assert namen == ["The Hobbit Play Booster Box", "Lorwyn Eclipsed Play Booster Box"]


def test_versiegelte_ware_steht_vor_zubehoer(gefuellt):
    """Gefunden im echten Katalog: bei „lorwyn eclipsed box" standen acht
    Deckboxen vorn und das Booster-Display fehlte ganz."""
    treffer = katalog.suche("lorwyn eclipsed box", limit=2, db_path=gefuellt)
    assert treffer[0]["name"] == "Lorwyn Eclipsed Play Booster Box"
    assert treffer[1]["kategorie"] == "DeckBoxes"


def test_zubehoer_wird_gefunden_wenn_danach_gesucht_wird(gefuellt):
    namen = [t["name"] for t in katalog.suche("lorwyn deck box", db_path=gefuellt)]
    assert len(namen) == 2 and all("Deck Box" in n for n in namen)


def test_suche_liefert_nummer_kategorie_und_artikelart(gefuellt):
    treffer = katalog.suche("dragon shield matte black", db_path=gefuellt)[0]
    assert treffer["id_product"] == 747481
    assert treffer["kategorie"] == "Sleeves" and treffer["item_type"] == "zubehoer"


def test_suche_behandelt_platzhalter_als_zeichen(gefuellt):
    assert [t["name"] for t in katalog.suche("100%", db_path=gefuellt)] == \
        ["Sleeves 100% Matte"]
    assert katalog.suche("_", db_path=gefuellt) == []


def test_suche_haelt_die_obergrenze_ein(gefuellt):
    assert len(katalog.suche("Füllprodukt", limit=5, db_path=gefuellt)) == 5


def test_ohne_katalog_ist_die_suche_leer_statt_kaputt(pfad):
    assert katalog.suche("hobbit", db_path=pfad) == []
    assert katalog.finde_genau("The Hobbit Play Booster Box", pfad) == []
    assert katalog.info(pfad)["vorhanden"] is False


def test_genauer_name_ohne_ruecksicht_auf_schreibweise(gefuellt):
    treffer = katalog.finde_genau("the hobbit PLAY booster box", gefuellt)
    assert [t["id_product"] for t in treffer] == [885551]


def test_gleichnamige_produkte_kommen_alle_zurueck(gefuellt):
    """Wenige Namen gibt es doppelt — der Aufrufer darf sich keinen aussuchen."""
    assert [t["id_product"] for t in
            katalog.finde_genau("Secret Lair: The Lands", gefuellt)] == [680815, 680825]


# ---------------------------------------------------------------------------
# Von selbst aktuell halten
# ---------------------------------------------------------------------------
def test_faellig_ohne_katalog_und_nach_einem_tag(gefuellt, pfad, tmp_path):
    assert katalog.faellig(tmp_path / "gibt-es-nicht.db") is True
    assert katalog.faellig(gefuellt) is False
    morgen = datetime.now() + timedelta(hours=25)
    assert katalog.faellig(gefuellt, jetzt=morgen) is True


def test_waechter_tut_nichts_solange_nichts_faellig_ist(gefuellt, monkeypatch):
    def darf_nicht(*a, **k):
        raise AssertionError("es sollte nicht nachgefragt werden")
    monkeypatch.setattr(katalog, "hole_von_cardmarket", darf_nicht)
    assert katalog.KatalogWaechter(gefuellt).durchgang() is None


def test_waechter_holt_den_katalog_wenn_er_fehlt(pfad, cm, monkeypatch):
    monkeypatch.setattr(katalog, "hole_von_cardmarket", cm)
    ergebnis = katalog.KatalogWaechter(pfad).durchgang()
    assert ergebnis["aktualisiert"] is True
    assert katalog.WAECHTER_STATUS["fehler"] is False
    assert katalog.anzahl_produkte(pfad) > 0


def test_waechter_ueberlebt_einen_fehlschlag(pfad, monkeypatch):
    """Pi ohne Netz: vermerken und weiterlaufen, nicht abstürzen."""
    def kein_netz(url, etag=None):
        raise katalog.KatalogFehler("Cardmarket ist nicht erreichbar")
    monkeypatch.setattr(katalog, "hole_von_cardmarket", kein_netz)
    assert katalog.KatalogWaechter(pfad).durchgang() is None
    assert katalog.WAECHTER_STATUS["fehler"] is True
    assert "nicht erreichbar" in katalog.WAECHTER_STATUS["meldung"]


# ---------------------------------------------------------------------------
# In der Oberfläche
# ---------------------------------------------------------------------------
@pytest.fixture()
def db(tmp_path, gefuellt, monkeypatch):
    lager = str(tmp_path / "lager.db")
    for modul in (TCGInventory, web, auth, setup_db, order_service, lager_manager):
        monkeypatch.setattr(modul, "DB_FILE", lager)
    monkeypatch.setattr(katalog, "DB_PATH", gefuellt)
    setup_db.initialize_database()
    return lager


def _klient():
    web.app.config["TESTING"] = True
    klient = web.app.test_client()
    with klient.session_transaction() as s:
        s["user"] = "melvin"
    return klient


def _produkt(db, name, cardmarket_name="", sprache="en", menge=3):
    with sqlite3.connect(db) as conn:
        cur = conn.execute(
            "INSERT INTO cards (name, set_code, language, quantity, status, "
            "item_type, location_hint, cardmarket_name) VALUES "
            "(?, '', ?, ?, 'verfügbar', 'display', 'Regal 2', ?)",
            (name, sprache, menge, cardmarket_name))
        conn.commit()
        return cur.lastrowid


def _position(db, zeile):
    item = parse_position_line(zeile)
    jetzt = datetime.now().isoformat(timespec="seconds")
    with sqlite3.connect(db) as conn:
        bid = conn.execute(
            "INSERT INTO orders (buyer_name, email_message_id, date_received, "
            "status, order_number) VALUES ('Sharqy', 'm1', ?, 'open', '1299999999')",
            (jetzt,)).lastrowid
        cur = conn.execute(
            "INSERT INTO order_items (order_id, card_name, quantity, unit_price, "
            "language, match_status) VALUES (?, ?, 1, ?, ?, 'unresolved')",
            (bid, item["name"], item["unit_price"], item["language"]))
        conn.commit()
        return cur.lastrowid


def test_formular_schlaegt_produkte_aus_dem_katalog_vor(db):
    daten = _klient().get("/api/produkte?q=hobbit play box").get_json()
    assert len(daten) == 1
    treffer = daten[0]
    assert treffer["quelle"] == "katalog"
    assert treffer["name"] == treffer["cardmarket_name"] == "The Hobbit Play Booster Box"
    assert treffer["cardmarket_id"] == 885551
    assert treffer["item_type"] == "display" and treffer["kategorie"] == "Magic Display"
    assert treffer["language"] == "", "die Sprache kennt der Katalog nicht"


def test_eigene_artikel_stehen_vor_dem_katalog(db):
    kid = _produkt(db, "Hobbit Display Regal 2", "The Hobbit Play Booster Box")
    daten = _klient().get("/api/produkte?q=hobbit").get_json()
    assert [d["quelle"] for d in daten][:2] == ["lager", "katalog"]
    assert daten[0]["id"] == kid
    assert all(d["quelle"] == "katalog" for d in daten[1:])


def test_cardmarket_feld_bekommt_nur_katalogtreffer(db):
    _produkt(db, "Hobbit Display Regal 2", "The Hobbit Play Booster Box")
    daten = _klient().get("/api/produkte?nur=katalog&q=hobbit").get_json()
    assert daten and all(d["quelle"] == "katalog" for d in daten)


def test_ohne_katalog_bleiben_die_eigenen_vorschlaege(db, tmp_path, monkeypatch):
    monkeypatch.setattr(katalog, "DB_PATH", tmp_path / "fehlt.db")
    _produkt(db, "Hobbit Display Regal 2")
    daten = _klient().get("/api/produkte?q=hobbit").get_json()
    assert [d["quelle"] for d in daten] == ["lager"]


def test_produktnummer_wird_am_artikel_gespeichert(db):
    """Was das Formular nach der Auswahl aus dem Katalog schickt."""
    _klient().post("/cards/add", data={
        "item_type": "display", "name": "The Hobbit Play Booster Box",
        "price": "120", "quantity": "2", "language": "en",
        "location_hint": "Regal 2",
        "cardmarket_name": "The Hobbit Play Booster Box",
        "cardmarket_id": "885551", "condition": "", "folder_id": "",
        "collector_number": "", "scryfall_id": "", "image_url": "",
        "page": "", "slot": "", "folder_part": ""})
    with sqlite3.connect(db) as conn:
        zeile = conn.execute(
            "SELECT cardmarket_id, cardmarket_name, language, item_type "
            "FROM cards").fetchone()
    assert zeile == ("885551", "The Hobbit Play Booster Box", "en", "display")
    # ... und die Bestellung findet ihn.
    item = parse_position_line(
        "1x The Hobbit Play Booster Box (The Hobbit) - English 168,00 EUR")
    with sqlite3.connect(db) as conn:
        treffer = order_service.OrderIngestionService()._match_item(conn.cursor(), item)
    assert treffer["match_status"] == "matched"


def test_bestellposition_bekommt_die_nummer_bei_eindeutigem_namen(db):
    item_id = _position(
        db, "1x The Hobbit Play Booster Box (The Hobbit) - English 168,00 EUR")
    html = _klient().get(f"/cards/add?position={item_id}").get_data(as_text=True)
    assert 'name="cardmarket_id" value="885551"' in html
    assert '<option value="display" selected>' in html


def test_bestellposition_raet_nicht_bei_gleichnamigen_produkten(db):
    item_id = _position(db, "1x Secret Lair: The Lands 30,00 EUR")
    html = _klient().get(f"/cards/add?position={item_id}").get_data(as_text=True)
    assert 'name="cardmarket_id" value=""' in html


def test_bestellposition_raet_nicht_bei_gekuerztem_namen(db):
    """Zwei Commander-Decks beginnen gleich — welches es ist, sagt die Mail nicht."""
    item_id = _position(db, "1x Commander: Magic: The Gathering | Teenage Mutant "
                            "Ninja Turtles: ... 48,50 EUR")
    html = _klient().get(f"/cards/add?position={item_id}").get_data(as_text=True)
    assert 'name="cardmarket_id" value=""' in html
    # Die Kandidaten kommen über die Vorschlagsliste.
    daten = _klient().get(
        "/api/produkte?q=Commander: Magic: The Gathering | Teenage Mutant "
        "Ninja Turtles:").get_json()
    assert sorted(d["cardmarket_id"] for d in daten) == [853738, 853739]


def test_katalogname_passt_zur_gekuerzten_bestellzeile(db):
    """Der volle Name aus dem Katalog am Artikel — die gekürzte Mail findet ihn."""
    name = ('Commander: Magic: The Gathering | Teenage Mutant Ninja Turtles: '
            '"Turtle Power!" Commander Deck')
    assert katalog.finde_genau(name, katalog.DB_PATH)
    kid = _produkt(db, "TMNT Turtle Power", name, sprache="")
    item = parse_position_line("1x Commander: Magic: The Gathering | Teenage Mutant "
                               "Ninja Turtles: ... 48,50 EUR")
    with sqlite3.connect(db) as conn:
        treffer = order_service.OrderIngestionService()._match_item(conn.cursor(), item)
    assert treffer["card_id"] == kid


def test_seite_kartendaten_zeigt_den_katalog(db):
    html = _klient().get("/system/kartendaten").get_data(as_text=True)
    assert "Cardmarket-Produktkatalog" in html
    assert "2026-11-08" in html                       # Stand bei Cardmarket
    assert "Versiegelte Magic-Produkte" in html and "Zubehör" in html


def test_seite_kartendaten_ohne_katalog(db, tmp_path, monkeypatch):
    monkeypatch.setattr(katalog, "DB_PATH", tmp_path / "fehlt.db")
    html = _klient().get("/system/kartendaten").get_data(as_text=True)
    assert "noch nicht geladen" in html


def test_knopf_aktualisiert_den_katalog(db, cm, monkeypatch):
    monkeypatch.setattr(katalog, "hole_von_cardmarket", cm)
    antwort = _klient().post("/system/kartendaten/cardmarket",
                             data={"erzwingen": "1"}, follow_redirects=True)
    assert "Cardmarket-Katalog aktualisiert" in antwort.get_data(as_text=True)
    antwort = _klient().post("/system/kartendaten/cardmarket", follow_redirects=True)
    assert "bereits aktuell" in antwort.get_data(as_text=True)


def test_knopf_meldet_einen_fehlschlag_und_behaelt_den_stand(db, monkeypatch):
    def kein_netz(url, etag=None):
        raise katalog.KatalogFehler("Cardmarket ist nicht erreichbar")
    monkeypatch.setattr(katalog, "hole_von_cardmarket", kein_netz)
    vorher = katalog.anzahl_produkte(katalog.DB_PATH)
    antwort = _klient().post("/system/kartendaten/cardmarket",
                             data={"erzwingen": "1"}, follow_redirects=True)
    text = antwort.get_data(as_text=True)
    assert "nicht aktualisiert" in text and "bleibt in Betrieb" in text
    assert katalog.anzahl_produkte(katalog.DB_PATH) == vorher


def test_katalog_gehoert_nicht_in_die_sicherung():
    """Jederzeit neu ladbar — wie die Scryfall-Daten."""
    assert katalog.DB_PATH.name in backup.DB_AUSGESCHLOSSEN
