"""Direktverkauf: Bestellungen ohne Cardmarket.

Der Kern: derselbe Weg wie bei einer Cardmarket-Bestellung — dieselben
Tabellen, dasselbe Dokument, derselbe Weg in die Buchhaltung — nur ohne Mail
und ohne Plattformgebühr.
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

from TCGInventory import direktverkauf, lager_manager, setup_db  # noqa: E402
from TCGInventory.direktverkauf import (                          # noqa: E402
    DirektverkaufFehler, erstelle_bestellung,
)


@pytest.fixture()
def db(tmp_path, monkeypatch):
    """Frische Datenbank mit zwei Karten im Bestand."""
    pfad = str(tmp_path / "t.db")
    from TCGInventory import auth
    for modul in (setup_db, auth, lager_manager, direktverkauf):
        monkeypatch.setattr(modul, "DB_FILE", pfad, raising=False)
    setup_db.initialize_database()

    conn = sqlite3.connect(pfad)
    conn.execute("INSERT INTO folders (id, name, pages) VALUES (1, 'Binder 1', 9)")
    conn.executemany(
        "INSERT INTO cards (name, set_code, language, condition, "
        "quantity, storage_code, folder_id, price) VALUES (?, ?, ?, ?, ?, ?, 1, ?)",
        [("Sol Ring", "cmr", "German", "NM", 1, "O01-S01-P1", 2.00),
         ("Lightning Bolt", "m11", "English", "EX", 3, "O01-S01-P2", 0.50)])
    conn.execute("INSERT INTO storage_slots (code, is_occupied) VALUES ('O01-S01-P1', 1)")
    conn.execute("INSERT INTO storage_slots (code, is_occupied) VALUES ('O01-S01-P2', 1)")
    conn.commit()
    conn.close()
    return pfad


def _karte(pfad, name):
    conn = sqlite3.connect(pfad)
    try:
        return conn.execute(
            "SELECT id, quantity, storage_code FROM cards WHERE name = ?",
            (name,)).fetchone()
    finally:
        conn.close()


def _bestellung(pfad, bestellung_id):
    conn = sqlite3.connect(pfad)
    conn.row_factory = sqlite3.Row
    try:
        kopf = dict(conn.execute("SELECT * FROM orders WHERE id = ?",
                                 (bestellung_id,)).fetchone())
        kopf["positionen"] = [dict(r) for r in conn.execute(
            "SELECT * FROM order_items WHERE order_id = ? ORDER BY id",
            (bestellung_id,))]
        return kopf
    finally:
        conn.close()


# =========================================================================
# Anlegen
# =========================================================================

def test_direktverkauf_legt_bestellung_an(db):
    karte = _karte(db, "Sol Ring")
    ergebnis = erstelle_bestellung(
        positionen=[{"card_id": karte[0], "quantity": 1, "unit_price": "3,50"}],
        kaeufer="Laufkundschaft", kanal="flohmarkt", db_file=db)

    b = _bestellung(db, ergebnis["order_id"])
    assert b["quelle"] == "manuell"
    assert b["verkaufskanal"] == "flohmarkt"
    assert b["order_number"].startswith("DV-")
    assert b["amount_gesamtwert"] == 3.50
    assert b["amount_gesamt"] == 3.50
    assert b["amount_gebuehren"] == 0          # keine Plattformgebühr
    assert len(b["positionen"]) == 1
    assert b["positionen"][0]["card_name"] == "Sol Ring"
    assert b["positionen"][0]["unit_price"] == 3.50


def test_nummer_ist_als_direktverkauf_erkennbar(db):
    """Auf dem gedruckten Beleg soll man die Herkunft sehen."""
    karte = _karte(db, "Sol Ring")
    erste = erstelle_bestellung(
        positionen=[{"card_id": karte[0], "quantity": 1, "unit_price": "1,00"}],
        db_file=db)
    zweite = erstelle_bestellung(
        positionen=[{"card_name": "Wühlkiste", "quantity": 1, "unit_price": "0,50"}],
        db_file=db)
    import re
    assert re.match(r"DV-\d{4}-0001$", erste["order_number"])
    assert re.match(r"DV-\d{4}-0002$", zweite["order_number"])


def test_versand_kommt_oben_drauf(db):
    karte = _karte(db, "Sol Ring")
    ergebnis = erstelle_bestellung(
        positionen=[{"card_id": karte[0], "quantity": 1, "unit_price": "10,00"}],
        versand="1,55", adresse="Max Mustermann\n24103 Kiel", db_file=db)
    assert ergebnis["warenwert"] == 10.00
    assert ergebnis["versand"] == 1.55
    assert ergebnis["gesamt"] == 11.55

    b = _bestellung(db, ergebnis["order_id"])
    assert b["amount_versand"] == 1.55
    assert b["address_confirmed"] == 1        # Adresse selbst eingetippt


def test_position_ohne_bestand_ist_erlaubt(db):
    """Nicht alles, was verkauft wird, steht im Inventar."""
    ergebnis = erstelle_bestellung(
        positionen=[{"card_name": "Sammlung gemischt", "quantity": 1,
                     "unit_price": "25,00"}], db_file=db)
    b = _bestellung(db, ergebnis["order_id"])
    assert b["positionen"][0]["card_id"] is None
    assert b["positionen"][0]["card_name"] == "Sammlung gemischt"


# =========================================================================
# Bestand
# =========================================================================

def test_letzte_karte_gibt_den_platz_frei(db):
    """Wie beim Knopf „verkauft": Zeile weg, Platz frei."""
    karte = _karte(db, "Sol Ring")
    erstelle_bestellung(
        positionen=[{"card_id": karte[0], "quantity": 1, "unit_price": "3,50"}],
        db_file=db)

    assert _karte(db, "Sol Ring") is None       # Zeile entfernt
    conn = sqlite3.connect(db)
    belegt = conn.execute(
        "SELECT is_occupied FROM storage_slots WHERE code = 'O01-S01-P1'").fetchone()
    conn.close()
    assert belegt[0] == 0                       # Platz wieder frei


def test_teilmenge_verringert_nur_die_anzahl(db):
    karte = _karte(db, "Lightning Bolt")        # 3 Stück
    erstelle_bestellung(
        positionen=[{"card_id": karte[0], "quantity": 2, "unit_price": "0,80"}],
        db_file=db)

    rest = _karte(db, "Lightning Bolt")
    assert rest[1] == 1                         # 3 − 2
    conn = sqlite3.connect(db)
    belegt = conn.execute(
        "SELECT is_occupied FROM storage_slots WHERE code = 'O01-S01-P2'").fetchone()
    conn.close()
    assert belegt[0] == 1                       # Platz bleibt belegt


def test_mehr_verkaufen_als_da_ist_wird_abgelehnt(db):
    karte = _karte(db, "Sol Ring")              # 1 Stück
    with pytest.raises(DirektverkaufFehler, match="nur 1 Stück"):
        erstelle_bestellung(
            positionen=[{"card_id": karte[0], "quantity": 2, "unit_price": "3,50"}],
            db_file=db)

    # Nichts angelegt, nichts ausgebucht.
    conn = sqlite3.connect(db)
    assert conn.execute("SELECT COUNT(*) FROM orders").fetchone()[0] == 0
    conn.close()
    assert _karte(db, "Sol Ring")[1] == 1


def test_fehler_in_einer_position_bucht_keine_andere_aus(db):
    """Alles oder nichts — sonst fehlt eine Karte ohne Beleg."""
    gut, schlecht = _karte(db, "Lightning Bolt"), _karte(db, "Sol Ring")
    with pytest.raises(DirektverkaufFehler):
        erstelle_bestellung(positionen=[
            {"card_id": gut[0], "quantity": 1, "unit_price": "0,80"},
            {"card_id": schlecht[0], "quantity": 9, "unit_price": "3,50"},
        ], db_file=db)

    assert _karte(db, "Lightning Bolt")[1] == 3     # unangetastet
    assert _karte(db, "Sol Ring")[1] == 1


# =========================================================================
# Eingaben prüfen
# =========================================================================

def test_preis_ohne_angabe_wird_nicht_geraten(db):
    """Der Einkaufspreis ist nicht der Verkaufspreis."""
    karte = _karte(db, "Sol Ring")
    with pytest.raises(DirektverkaufFehler, match="Preis angeben"):
        erstelle_bestellung(
            positionen=[{"card_id": karte[0], "quantity": 1}], db_file=db)


def test_preis_versteht_komma_und_punkt(db):
    for eingabe, erwartet in [("3,50", 3.50), ("3.50", 3.50), ("1.234,56", 1234.56),
                              ("2", 2.00), ("0,05 €", 0.05)]:
        ergebnis = erstelle_bestellung(
            positionen=[{"card_name": "Test", "quantity": 1, "unit_price": eingabe}],
            db_file=db)
        assert ergebnis["warenwert"] == erwartet, eingabe


def test_unsinnige_eingaben(db):
    with pytest.raises(DirektverkaufFehler, match="kein Preis"):
        erstelle_bestellung(
            positionen=[{"card_name": "X", "unit_price": "teuer"}], db_file=db)
    with pytest.raises(DirektverkaufFehler, match="nicht negativ"):
        erstelle_bestellung(
            positionen=[{"card_name": "X", "unit_price": "-1,00"}], db_file=db)
    with pytest.raises(DirektverkaufFehler, match="nichts zu verkaufen"):
        erstelle_bestellung(positionen=[], db_file=db)
    with pytest.raises(DirektverkaufFehler, match="mindestens 1"):
        erstelle_bestellung(
            positionen=[{"card_name": "X", "quantity": 0, "unit_price": "1,00"}],
            db_file=db)
    with pytest.raises(DirektverkaufFehler, match="Bezeichnung eintragen"):
        erstelle_bestellung(
            positionen=[{"quantity": 1, "unit_price": "1,00"}], db_file=db)


def test_unbekannter_kanal(db):
    with pytest.raises(DirektverkaufFehler, match="Unbekannter Verkaufskanal"):
        erstelle_bestellung(
            positionen=[{"card_name": "X", "unit_price": "1,00"}],
            kanal="ebay", db_file=db)


def test_cardmarket_bestellungen_bleiben_unberuehrt(db):
    """Vorhandene Zeilen bekommen die richtigen Vorgabewerte."""
    conn = sqlite3.connect(db)
    conn.execute(
        "INSERT INTO orders (buyer_name, email_message_id, date_received, "
        "status, order_number) VALUES ('kartenfuchs', 'mail-1', '2026-06-10', "
        "'sold', '4711')")
    conn.commit()
    zeile = conn.execute(
        "SELECT quelle, verkaufskanal FROM orders WHERE order_number = '4711'"
    ).fetchone()
    conn.close()
    assert zeile == ("cardmarket", "cardmarket")
