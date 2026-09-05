"""Displays, Precons und Zubehoer beim Bestelleingang zuordnen.

Sie stehen nicht in der Scryfall-Datenbank -- fuer sie traegt der
Identitaetspfad (set_code + collector_number + language + foil) nicht. Erkannt
werden koennen sie nur ueber den Namen, den beim Anlegen des Artikels jemand
bewusst hinterlegt hat: ``cards.cardmarket_name``.

Ausdruecklich **nur** fuer Produkte. Bei Einzelkarten waere der blosse Name zu
wenig -- die Bestellmail sagt dort nichts ueber Foil, und ein Treffer auf den
Namen allein wuerde Foil und Normal verwechseln.

Und wenn gar nichts passt, gibt es die freie Suche im Bestand statt einer
Sackgasse.
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
from TCGInventory import (auth, lager_manager, order_service,     # noqa: E402
                          setup_db, web)
from TCGInventory.email_parser import parse_position_line         # noqa: E402

PRECON = ("1x Commander: Magic: The Gathering | Teenage Mutant Ninja "
          "Turtles: ... 48,50 EUR")
DISPLAY = "1x The Hobbit Play Booster Box (The Hobbit) - English 168,00 EUR"
EINZELKARTE = "1x Smaug, Wicked Worm (The Hobbit) - M - Englisch - NM 2,99 EUR"


@pytest.fixture()
def db(tmp_path):
    pfad = str(tmp_path / "p.db")
    for modul in (TCGInventory, web, auth, setup_db, order_service, lager_manager):
        modul.DB_FILE = pfad
    setup_db.initialize_database()
    return pfad


def _artikel(db, name, *, cardmarket_name=None, item_type="display",
             sprache="en", menge=3, foil=0, set_code="tho"):
    with sqlite3.connect(db) as conn:
        cur = conn.execute(
            "INSERT INTO cards (name, set_code, language, condition, price, "
            "quantity, storage_code, location_hint, status, collector_number, "
            "foil, item_type, cardmarket_name, date_added) VALUES "
            "(?, ?, ?, 'NM', 100.0, ?, '', 'Regal 2', 'verfügbar', '1', ?, ?, ?, "
            "'2026-09-01T10:00:00')",
            (name, set_code, sprache, menge, foil, item_type, cardmarket_name))
        conn.commit()
        return cur.lastrowid


def _matche(db, zeile):
    item = parse_position_line(zeile)
    dienst = order_service.OrderIngestionService()
    with sqlite3.connect(db) as conn:
        return dienst._match_item(conn.cursor(), item)


# ---------------------------------------------------------------------------
# Erkennung ueber den hinterlegten Namen
# ---------------------------------------------------------------------------
def test_ohne_hinterlegten_namen_keine_zuordnung(db):
    """Ausgangslage: im Bestand heisst der Artikel anders."""
    _artikel(db, "The Hobbit Play Booster Display")
    assert _matche(db, DISPLAY)["match_status"] != "matched"


def test_mit_hinterlegtem_namen_wird_erkannt(db):
    kid = _artikel(db, "The Hobbit Play Booster Display",
                   cardmarket_name="The Hobbit Play Booster Box")
    treffer = _matche(db, DISPLAY)
    assert treffer["match_status"] == "matched"
    assert treffer["card_id"] == kid


def test_gekuerzter_name_wird_ueber_den_anfang_erkannt(db):
    """Cardmarket kuerzt lange Namen — der Anfang genuegt."""
    voll = ("Commander: Magic: The Gathering | Teenage Mutant Ninja Turtles: "
            "Mutant Mayhem")
    kid = _artikel(db, "TMNT Commander Mutant Mayhem", cardmarket_name=voll,
                   set_code="tmnt")
    assert _matche(db, PRECON)["card_id"] == kid


def test_mehrere_passende_werden_nicht_geraten(db):
    """Ein Set hat oft vier Commander-Decks mit gleichem Anfang.

    Beide passen auf den gekuerzten Text — dann darf nichts automatisch
    zugeordnet werden, sonst geht das falsche Deck raus.
    """
    anfang = "Commander: Magic: The Gathering | Teenage Mutant Ninja Turtles: "
    _artikel(db, "TMNT Mutant Mayhem", cardmarket_name=anfang + "Mutant Mayhem",
             set_code="tmnt")
    _artikel(db, "TMNT Shell Shock", cardmarket_name=anfang + "Shell Shock",
             set_code="tmnt")
    assert _matche(db, PRECON)["match_status"] != "matched"


def test_sprache_wird_beachtet(db):
    """Die englische Box ist nicht die deutsche."""
    _artikel(db, "The Hobbit Play Booster Display", sprache="de",
             cardmarket_name="The Hobbit Play Booster Box")
    assert _matche(db, DISPLAY)["match_status"] != "matched"


def test_ausverkauftes_produkt_wird_nicht_zugeordnet(db):
    kid = _artikel(db, "The Hobbit Play Booster Display", menge=0,
                   cardmarket_name="The Hobbit Play Booster Box")
    with sqlite3.connect(db) as conn:
        conn.execute("UPDATE cards SET status = 'verkauft' WHERE id = ?", (kid,))
        conn.commit()
    assert _matche(db, DISPLAY)["match_status"] != "matched"


# ---------------------------------------------------------------------------
# Einzelkarten bleiben aussen vor
# ---------------------------------------------------------------------------
def test_einzelkarte_wird_nicht_ueber_den_namen_zugeordnet(db):
    """Die Mail sagt nichts ueber Foil — der Name allein genuegt hier nicht.

    Genau der Einwand aus dem Betrieb: eine Zuordnung ueber den Mailtext
    wuerde Foil und Normal verwechseln. Deshalb greift der Produktweg nur bei
    item_type <> 'card'.
    """
    _artikel(db, "Smaug, Wicked Worm", item_type="card", foil=1,
             cardmarket_name="Smaug, Wicked Worm")
    dienst = order_service.OrderIngestionService()
    item = parse_position_line(EINZELKARTE)
    with sqlite3.connect(db) as conn:
        assert dienst._als_produkt(conn.cursor(), item) is None


def test_produktweg_beruehrt_den_kartenweg_nicht(db, monkeypatch):
    """Der bestehende Identitaetspfad muss unveraendert funktionieren."""
    monkeypatch.setattr(order_service, "resolve_set_code",
                        lambda _n: ("tho", "high"))
    _artikel(db, "Smaug, Wicked Worm", item_type="card", foil=0,
             set_code="tho", sprache="en")
    assert _matche(db, EINZELKARTE)["match_status"] == "matched"


# ---------------------------------------------------------------------------
# Kein Lernen mehr
# ---------------------------------------------------------------------------
def test_es_gibt_kein_merken_mehr():
    """Die gelernte Zuordnung wurde auf Zuruf entfernt.

    Sie las den Mailtext als Schluessel — der sagt aber nichts ueber Foil und
    Zustand, und ein Haken beim Zuordnen ist die falsche Stelle fuer eine
    dauerhafte Entscheidung.
    """
    wurzel = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    assert not os.path.exists(os.path.join(wurzel, "produkt_alias.py"))
    with open(os.path.join(wurzel, "templates", "orders.html"),
              encoding="utf-8") as datei:
        assert 'name="merken"' not in datei.read()


# ---------------------------------------------------------------------------
# Freie Suche als Rettungsweg
# ---------------------------------------------------------------------------
def _bestellung_mit_position(db, name):
    with sqlite3.connect(db) as conn:
        cur = conn.execute(
            "INSERT INTO orders (buyer_name, email_message_id, date_received, "
            "status, order_number) VALUES ('Sharqy', 'msg-1', "
            "'2026-09-01T10:00:00', 'open', '1299999999')")
        bid = cur.lastrowid
        cur = conn.execute(
            "INSERT INTO order_items (order_id, card_name, quantity) "
            "VALUES (?, ?, 1)", (bid, name))
        conn.commit()
        return cur.lastrowid


def _klient():
    web.app.config["TESTING"] = True
    klient = web.app.test_client()
    with klient.session_transaction() as s:
        s["user"] = "melvin"
    return klient


def test_suchseite_findet_ueber_den_eigenen_namen(db):
    _artikel(db, "The Hobbit Play Booster Display",
             cardmarket_name="The Hobbit Play Booster Box")
    item_id = _bestellung_mit_position(db, "The Hobbit Play Booster Box")

    text = _klient().get(
        f"/orders/items/{item_id}/zuordnen?q=Hobbit").get_data(as_text=True)
    assert "The Hobbit Play Booster Display" in text


def test_suchseite_findet_ueber_den_cardmarket_namen(db):
    """Der interne Name ist ein ganz anderer — dann muss der Mailname greifen."""
    _artikel(db, "Kartons Regal 2 Nr. 7",
             cardmarket_name="The Hobbit Play Booster Box")
    item_id = _bestellung_mit_position(db, "The Hobbit Play Booster Box")

    text = _klient().get(
        f"/orders/items/{item_id}/zuordnen?q=Play Booster").get_data(as_text=True)
    assert "Kartons Regal 2 Nr. 7" in text


def test_suchseite_ohne_suche_zeigt_die_position(db):
    item_id = _bestellung_mit_position(db, "Irgendein Produkt")
    text = _klient().get(f"/orders/items/{item_id}/zuordnen").get_data(as_text=True)
    assert "Irgendein Produkt" in text
    assert "1299999999" in text


def test_suchseite_weist_auf_die_kuerzung_hin(db):
    item_id = _bestellung_mit_position(
        db, "Commander: Magic: The Gathering | Teenage Mutant Ninja Turtles: ...")
    text = _klient().get(f"/orders/items/{item_id}/zuordnen").get_data(as_text=True)
    assert "gekürzt" in text


def test_suchseite_aendert_nichts(db):
    """Rein lesend — zugeordnet wird ueber die bestehende Route."""
    _artikel(db, "The Hobbit Play Booster Display",
             cardmarket_name="The Hobbit Play Booster Box")
    item_id = _bestellung_mit_position(db, "The Hobbit Play Booster Box")

    def _abbild():
        with sqlite3.connect(db) as conn:
            return (conn.execute("SELECT * FROM cards").fetchall(),
                    conn.execute("SELECT * FROM order_items").fetchall())

    vorher = _abbild()
    _klient().get(f"/orders/items/{item_id}/zuordnen?q=Hobbit")
    assert _abbild() == vorher


def test_kandidaten_finden_ueber_den_gekuerzten_anfang(db):
    """Mit den Punkten im Suchtext fand die Kandidatenliste frueher nichts."""
    _artikel(db, "Commander: Magic: The Gathering | Teenage Mutant Ninja "
                 "Turtles: Mutant Mayhem", set_code="tmnt")
    with sqlite3.connect(db) as conn:
        conn.row_factory = sqlite3.Row      # wie im echten Aufruf
        kandidaten = web._order_item_candidates(
            conn.cursor(),
            "Commander: Magic: The Gathering | Teenage Mutant Ninja Turtles: ...")
    assert len(kandidaten) == 1
    assert "Mutant Mayhem" in kandidaten[0]["name"]
