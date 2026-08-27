"""Displays, Precons und Zubehoer aus einer Cardmarket-Bestellung erkennen.

Die Zeilen stammen aus echten Bestellmails:

    1x Commander: Magic: The Gathering | Teenage Mutant Ninja Turtles: ... 48,50 EUR
    1x The Hobbit Play Booster Box (The Hobbit) - English 168,00 EUR

Zwei Dinge fielen daran auf:

1. Bei versiegelter Ware steht hinter dem Namen nur **ein** Teil (die Sprache),
   bei Einzelkarten sind es drei (Seltenheit, Sprache, Zustand). Nach der
   Stelle gelesen wurde aus "English" die Seltenheit und die Sprache blieb
   leer -- eine englische und eine deutsche Booster Box waeren damit nicht
   auseinanderzuhalten.

2. Den Precon-Namen kuerzt **Cardmarket selbst**. Der volle Name steht nicht in
   der Mail und laesst sich nicht rekonstruieren. Erkannt werden kann so etwas
   nur ueber eine einmal getroffene und gemerkte Zuordnung -- und auch das nur,
   solange der Anfang im Bestand eindeutig ist.
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
                          produkt_alias, setup_db, web)
from TCGInventory.email_parser import parse_position_line         # noqa: E402

PRECON = ("1x Commander: Magic: The Gathering | Teenage Mutant Ninja "
          "Turtles: ... 48,50 EUR")
DISPLAY = "1x The Hobbit Play Booster Box (The Hobbit) - English 168,00 EUR"
EINZELKARTE = "1x Smaug, Wicked Worm (The Hobbit) - M - Englisch - NM 2,99 EUR"


# ---------------------------------------------------------------------------
# Der Parser
# ---------------------------------------------------------------------------
def test_display_sprache_landet_nicht_in_der_seltenheit():
    """Der gemeldete Fehler: aus "- English" wurde rarity='English'."""
    ergebnis = parse_position_line(DISPLAY)
    assert ergebnis["name"] == "The Hobbit Play Booster Box"
    assert ergebnis["set_name"] == "The Hobbit"
    assert ergebnis["language"] == "en"
    assert ergebnis["rarity"] is None
    assert ergebnis["condition"] is None
    assert ergebnis["unit_price"] == 168.0


def test_einzelkarte_bleibt_unveraendert():
    """Die Reihenfolge Seltenheit/Sprache/Zustand darf nicht kaputtgehen."""
    ergebnis = parse_position_line(EINZELKARTE)
    assert ergebnis["name"] == "Smaug, Wicked Worm"
    assert ergebnis["rarity"] == "M"
    assert ergebnis["language"] == "en"
    assert ergebnis["condition"] == "NM"


def test_precon_wird_als_unsicher_erkannt():
    """Cardmarket hat gekuerzt — daraus darf nichts geraten werden."""
    ergebnis = parse_position_line(PRECON)
    assert ergebnis["uncertain"] is True
    assert ergebnis["set_name"] is None
    assert ergebnis["unit_price"] == 48.5
    assert ergebnis["name"].endswith("...")


@pytest.mark.parametrize("suffix, sprache, zustand, seltenheit", [
    ("- English", "en", None, None),
    ("- Deutsch", "de", None, None),
    ("- M - Englisch - NM", "en", "NM", "M"),
    ("- NM - Englisch", "en", "NM", None),          # andere Reihenfolge
    ("- Englisch - NM - M", "en", "NM", "M"),       # und noch eine
    ("- Near Mint", None, "NM", None),
])
def test_reihenfolge_der_teile_ist_egal(suffix, sprache, zustand, seltenheit):
    """Eingeordnet wird nach Inhalt, nicht nach Stelle."""
    zeile = f"1x Irgendwas (The Hobbit) {suffix} 1,00 EUR"
    ergebnis = parse_position_line(zeile)
    assert ergebnis["language"] == sprache
    assert ergebnis["condition"] == zustand
    assert ergebnis["rarity"] == seltenheit


# ---------------------------------------------------------------------------
# Gelernte Zuordnung
# ---------------------------------------------------------------------------
@pytest.fixture()
def db(tmp_path):
    pfad = str(tmp_path / "p.db")
    for modul in (TCGInventory, web, auth, setup_db, order_service, lager_manager):
        modul.DB_FILE = pfad
    setup_db.initialize_database()
    return pfad


def _produkt(db, name, set_code="tho", sprache="en", menge=3,
             item_type="display"):
    with sqlite3.connect(db) as conn:
        conn.execute(
            "INSERT INTO cards (name, set_code, language, condition, price, "
            "quantity, storage_code, location_hint, status, collector_number, "
            "foil, item_type, date_added) VALUES (?, ?, ?, '', 100.0, ?, '', "
            "'Regal 2', 'verfügbar', '', 0, ?, '2026-08-01T10:00:00')",
            (name, set_code, sprache, menge, item_type))
        conn.commit()


def _zuordnen(db, mail_text, mail_set, ziel_name, ziel_set="tho", ziel_sprache="en"):
    with sqlite3.connect(db) as conn:
        produkt_alias.merke(conn, mail_text, mail_set, ziel_name, ziel_set,
                            ziel_sprache, benutzer="melvin")
        conn.commit()


def _matche(db, zeile):
    item = parse_position_line(zeile)
    dienst = order_service.OrderIngestionService()
    with sqlite3.connect(db) as conn:
        return dienst._match_item(conn.cursor(), item)


def test_display_ohne_zuordnung_geht_zur_handauswahl(db):
    """Ausgangslage: der Name im Bestand ist ein anderer als in der Mail."""
    _produkt(db, "The Hobbit Play Booster Display")
    assert _matche(db, DISPLAY)["match_status"] != "matched"


def test_display_mit_zuordnung_wird_erkannt(db):
    _produkt(db, "The Hobbit Play Booster Display")
    _zuordnen(db, "The Hobbit Play Booster Box", "The Hobbit",
              "The Hobbit Play Booster Display")

    treffer = _matche(db, DISPLAY)
    assert treffer["match_status"] == "matched"
    assert treffer["card_id"]


def test_precon_mit_zuordnung_wird_trotz_kuerzung_erkannt(db):
    """Der gekuerzte Text ist stabil — er kommt jedes Mal gleich."""
    _produkt(db, "TMNT Commander: Mutant Mayhem", set_code="tmnt",
             item_type="display")
    _zuordnen(db, parse_position_line(PRECON)["name"], None,
              "TMNT Commander: Mutant Mayhem", ziel_set="tmnt")

    assert _matche(db, PRECON)["match_status"] == "matched"


def test_zwei_produkte_mit_gleichem_anfang_werden_nicht_geraten(db):
    """Der Fallstrick: ein Set hat oft vier Commander-Decks.

    Alle vier kuerzt Cardmarket auf denselben Text. Eine gelernte Zuordnung
    duerfte dann ab dem zweiten still das falsche Produkt ausbuchen -- also
    greift sie gar nicht und es wird wieder gefragt.
    """
    anfang = "Commander: Magic: The Gathering | Teenage Mutant Ninja Turtles"
    _produkt(db, f"{anfang}: Mutant Mayhem", set_code="tmnt")
    _zuordnen(db, parse_position_line(PRECON)["name"], None,
              f"{anfang}: Mutant Mayhem", ziel_set="tmnt")
    assert _matche(db, PRECON)["match_status"] == "matched"

    # Jetzt kommt ein zweites Deck derselben Reihe in den Bestand.
    _produkt(db, f"{anfang}: Shell Shock", set_code="tmnt")
    assert _matche(db, PRECON)["match_status"] != "matched", (
        "bei mehrdeutigem Anfang darf nicht automatisch zugeordnet werden")


def test_zuordnung_zeigt_auf_die_identitaet_nicht_auf_die_zeile(db):
    """Ein neuer Karton ist eine neue Zeile — die Zuordnung muss halten."""
    _produkt(db, "The Hobbit Play Booster Display", menge=1)
    _zuordnen(db, "The Hobbit Play Booster Box", "The Hobbit",
              "The Hobbit Play Booster Display")
    erste = _matche(db, DISPLAY)["card_id"]

    # Ausverkauft, und spaeter kommt neue Ware als eigene Zeile.
    with sqlite3.connect(db) as conn:
        conn.execute("UPDATE cards SET quantity = 0, status = 'verkauft' "
                     "WHERE id = ?", (erste,))
        conn.commit()
    assert _matche(db, DISPLAY)["match_status"] != "matched"

    _produkt(db, "The Hobbit Play Booster Display", menge=6)
    zweite = _matche(db, DISPLAY)
    assert zweite["match_status"] == "matched"
    assert zweite["card_id"] != erste


def test_falsche_sprache_wird_nicht_zugeordnet(db):
    """Die englische Box ist nicht die deutsche."""
    _produkt(db, "The Hobbit Play Booster Display", sprache="de")
    _zuordnen(db, "The Hobbit Play Booster Box", "The Hobbit",
              "The Hobbit Play Booster Display", ziel_sprache="en")
    assert _matche(db, DISPLAY)["match_status"] != "matched"


def test_einzelkarten_laufen_weiter_ueber_den_normalen_weg(db, monkeypatch):
    """Der bestehende Weg darf sich nicht aendern.

    Die Set-Aufloesung kommt sonst aus der lokalen Scryfall-Datenbank; die
    liegt in der Testumgebung nicht vor und ist hier auch nicht der Punkt.
    """
    monkeypatch.setattr(order_service, "resolve_set_code",
                        lambda _n: ("tho", "high"))
    with sqlite3.connect(db) as conn:
        conn.execute(
            "INSERT INTO cards (name, set_code, language, condition, price, "
            "quantity, storage_code, status, collector_number, foil, "
            "item_type, date_added) VALUES ('Smaug, Wicked Worm', 'tho', 'en', "
            "'NM', 2.99, 1, 'O01-S01-P1', 'verfügbar', '1', 0, 'card', "
            "'2026-08-01T10:00:00')")
        conn.commit()
    with sqlite3.connect(db) as conn:
        assert produkt_alias.alle(conn) == []
    treffer = _matche(db, EINZELKARTE)
    assert treffer["match_status"] == "matched"


# ---------------------------------------------------------------------------
# Verwaltung der Zuordnungen
# ---------------------------------------------------------------------------
def test_merken_ersetzt_eine_fruehere_wahl(db):
    _zuordnen(db, "Box", None, "Erstes Produkt")
    _zuordnen(db, "Box", None, "Zweites Produkt")
    with sqlite3.connect(db) as conn:
        eintraege = produkt_alias.alle(conn)
    assert len(eintraege) == 1
    assert eintraege[0]["ziel_name"] == "Zweites Produkt"


def test_zuordnung_laesst_sich_loeschen(db):
    _produkt(db, "The Hobbit Play Booster Display")
    _zuordnen(db, "The Hobbit Play Booster Box", "The Hobbit",
              "The Hobbit Play Booster Display")
    assert _matche(db, DISPLAY)["match_status"] == "matched"

    with sqlite3.connect(db) as conn:
        eintrag = produkt_alias.alle(conn)[0]
        assert produkt_alias.entferne(conn, eintrag["id"]) is True
        conn.commit()
    assert _matche(db, DISPLAY)["match_status"] != "matched"


def test_seite_zeigt_die_zuordnungen(db):
    _zuordnen(db, "The Hobbit Play Booster Box", "The Hobbit",
              "The Hobbit Play Booster Display")
    web.app.config["TESTING"] = True
    klient = web.app.test_client()
    with klient.session_transaction() as s:
        s["user"] = "melvin"

    text = klient.get("/zuordnungen").get_data(as_text=True)
    assert "The Hobbit Play Booster Box" in text
    assert "The Hobbit Play Booster Display" in text
