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


# =========================================================================
# Weboberfläche
# =========================================================================

@pytest.fixture()
def client(db, monkeypatch):
    from TCGInventory import web
    monkeypatch.setattr(web, "DB_FILE", db)
    web.app.config["TESTING"] = True
    c = web.app.test_client()
    with c.session_transaction() as s:
        s["user"] = "tester"
    return c


def test_formular_wird_angezeigt(client):
    seite = client.get("/orders/neu").get_data(as_text=True)
    assert "Direktverkauf erfassen" in seite
    assert "Flohmarkt" in seite
    assert "nicht zusätzlich" in seite          # Warnung wegen Tagesabschluss


def test_formular_uebernimmt_die_karte_aus_der_uebersicht(client, db):
    karte = _karte(db, "Sol Ring")
    seite = client.get(f"/orders/neu?card_id={karte[0]}").get_data(as_text=True)
    assert "Sol Ring" in seite
    assert "wird beim Speichern ausgebucht" in seite


def test_verkauf_ueber_das_formular(client, db):
    karte = _karte(db, "Sol Ring")
    antwort = client.post("/orders/neu", data={
        "kanal": "flohmarkt", "kaeufer": "Laufkundschaft",
        "card_id": str(karte[0]), "card_name": "Sol Ring",
        "quantity": "1", "unit_price": "3,50",
    }, follow_redirects=True)

    seite = antwort.get_data(as_text=True)
    assert antwort.status_code == 200
    assert "angelegt" in seite                  # Belegseite
    assert "Quittung" in seite and "Beileger" in seite
    assert _karte(db, "Sol Ring") is None       # ausgebucht


def test_leere_zeilen_im_formular_stoeren_nicht(client, db):
    """Das Formular schickt so viele Zeilen mit, wie angelegt wurden."""
    antwort = client.post("/orders/neu", data={
        "card_id": ["", ""], "card_name": ["Wühlkiste", ""],
        "quantity": ["1", "1"], "unit_price": ["2,00", ""],
    }, follow_redirects=True)
    assert "angelegt" in antwort.get_data(as_text=True)

    conn = sqlite3.connect(db)
    anzahl = conn.execute("SELECT COUNT(*) FROM order_items").fetchone()[0]
    conn.close()
    assert anzahl == 1                          # nur die gefüllte Zeile


def test_fehler_bleibt_im_formular(client, db):
    antwort = client.post("/orders/neu", data={
        "card_name": "X", "quantity": "1", "unit_price": "teuer",
    }, follow_redirects=True)
    seite = antwort.get_data(as_text=True)
    assert "kein Preis" in seite
    assert "Direktverkauf erfassen" in seite     # Formular wieder da

    conn = sqlite3.connect(db)
    assert conn.execute("SELECT COUNT(*) FROM orders").fetchone()[0] == 0
    conn.close()


def test_quittung_braucht_keine_adresse(client, db):
    ergebnis = erstelle_bestellung(
        positionen=[{"card_name": "Wühlkiste", "quantity": 1, "unit_price": "2,00"}],
        kanal="flohmarkt", db_file=db)

    antwort = client.get(f"/orders/{ergebnis['order_id']}/quittung")
    assert antwort.status_code == 200
    assert antwort.mimetype == "application/pdf"
    assert antwort.data[:5] == b"%PDF-"


def test_quittung_zeigt_die_positionen(client, db):
    pypdf = pytest.importorskip("pypdf")
    import io
    ergebnis = erstelle_bestellung(
        positionen=[{"card_name": "Sammlung gemischt", "quantity": 3,
                     "unit_price": "5,00"}],
        kanal="flohmarkt", kaeufer="Laufkundschaft", db_file=db)

    antwort = client.get(f"/orders/{ergebnis['order_id']}/quittung")
    text = pypdf.PdfReader(io.BytesIO(antwort.data)).pages[0].extract_text()
    assert "Quittung" in text
    assert ergebnis["order_number"] in text
    assert "Sammlung gemischt" in text
    assert "15,00" in text                      # 3 × 5,00
    assert "Flohmarkt" in text
    # Keine Adresse, keine Anrede — das ist der Unterschied zum Beileger.
    assert "Hallo" not in text


def test_bestellliste_bietet_den_direktverkauf_an(client):
    seite = client.get("/orders").get_data(as_text=True)
    assert "Direktverkauf" in seite              # der neue Knopf oben


def test_direktverkauf_steht_nicht_unter_offenen_bestellungen(client, db):
    """Er ist bereits abgeschlossen — sonst ließe er sich doppelt ausbuchen."""
    ergebnis = erstelle_bestellung(
        positionen=[{"card_name": "Wühlkiste", "quantity": 1, "unit_price": "2,00"}],
        kanal="flohmarkt", db_file=db)

    conn = sqlite3.connect(db)
    status, fertig = conn.execute(
        "SELECT status, date_completed FROM orders WHERE id = ?",
        (ergebnis["order_id"],)).fetchone()
    conn.close()
    assert status == "sold" and fertig

    assert ergebnis["order_number"] not in client.get("/orders").get_data(as_text=True)


def test_belegseite_bietet_beide_dokumente(client, db):
    ergebnis = erstelle_bestellung(
        positionen=[{"card_name": "Wühlkiste", "quantity": 1, "unit_price": "2,00"}],
        kanal="flohmarkt", adresse="Max Mustermann\n24103 Kiel", db_file=db)

    seite = client.get(f"/orders/{ergebnis['order_id']}/beleg").get_data(as_text=True)
    assert ergebnis["order_number"] in seite
    assert "Quittung" in seite and "Beileger" in seite
    assert "keinen zusätzlichen" in seite        # Warnung wegen Doppelbuchung


def test_ohne_adresse_kein_beileger(client, db):
    """Ein Anschreiben ohne Empfänger ergibt keinen Sinn."""
    ergebnis = erstelle_bestellung(
        positionen=[{"card_name": "Wühlkiste", "quantity": 1, "unit_price": "2,00"}],
        db_file=db)
    seite = client.get(f"/orders/{ergebnis['order_id']}/beleg").get_data(as_text=True)
    assert "braucht eine Adresse" in seite
    assert "disabled" in seite


# =========================================================================
# Weg in die Buchhaltung
# =========================================================================

def test_api_reicht_den_kanal_durch(client, db, monkeypatch):
    """Die Buchhaltung muss Cardmarket und Direktverkauf unterscheiden können.

    Vorher stand in der API-Antwort fest „cardmarket" — ein Direktverkauf wäre
    dort mit Plattformgebühr gebucht worden.
    """
    monkeypatch.setenv("TCG_API_TOKEN", "test-token")
    import TCGInventory
    monkeypatch.setattr(TCGInventory, "DB_FILE", db, raising=False)

    ergebnis = erstelle_bestellung(
        positionen=[{"card_name": "Wühlkiste", "quantity": 1, "unit_price": "2,00"}],
        kanal="flohmarkt", db_file=db)

    antwort = client.get("/api/v1/orders",
                         headers={"Authorization": "Bearer test-token"})
    assert antwort.status_code == 200
    bestellungen = antwort.get_json()["bestellungen"]
    unsere = [b for b in bestellungen
              if b["bestellnummer"] == ergebnis["order_number"]]
    assert unsere, f"Direktverkauf fehlt in der API: {bestellungen}"

    b = unsere[0]
    assert b["verkaufskanal"] == "flohmarkt"     # nicht mehr fest cardmarket
    assert b["quelle"] == "manuell"
    assert b["betraege_cent"]["gebuehren"] == 0  # keine Plattformgebühr
    assert b["betraege_cent"]["gesamt"] == 200


def test_api_bleibt_bei_cardmarket_fuer_maildaten(client, db, monkeypatch):
    monkeypatch.setenv("TCG_API_TOKEN", "test-token")
    import TCGInventory
    monkeypatch.setattr(TCGInventory, "DB_FILE", db, raising=False)

    conn = sqlite3.connect(db)
    conn.execute(
        "INSERT INTO orders (buyer_name, email_message_id, date_received, "
        "status, date_completed, order_number, amount_gesamt, amount_versand, "
        "amount_gebuehren) VALUES ('kartenfuchs', 'mail-1', '2026-08-10', "
        "'sold', '2026-08-11', '4711', 12.15, 1.55, 0.07)")
    conn.commit()
    conn.close()

    antwort = client.get("/api/v1/orders",
                         headers={"Authorization": "Bearer test-token"})
    b = [x for x in antwort.get_json()["bestellungen"]
         if x["bestellnummer"] == "4711"][0]
    assert b["verkaufskanal"] == "cardmarket"
    assert b["quelle"] == "cardmarket"
    assert b["betraege_cent"]["gebuehren"] == 7


# =========================================================================
# Wenn das Ausbuchen scheitert
# =========================================================================

def test_gescheitertes_ausbuchen_wird_gemeldet(db, monkeypatch):
    """Sonst steht ein Verkauf im System, während die Karte im Regal liegt.

    Das fällt sonst erst beim nächsten Zählen auf — und dann weiß niemand
    mehr, warum der Bestand nicht stimmt.
    """
    karte = _karte(db, "Sol Ring")
    monkeypatch.setattr(direktverkauf, "sell_card", lambda *a, **k: False)

    ergebnis = erstelle_bestellung(
        positionen=[{"card_id": karte[0], "quantity": 1, "unit_price": "3,50"}],
        db_file=db)

    assert ergebnis["nicht_ausgebucht"] == ["Sol Ring"]
    # Der Verkauf bleibt erfasst — der Beleg wurde ja schon gegeben.
    b = _bestellung(db, ergebnis["order_id"])
    assert b["amount_gesamt"] == 3.50


def test_erfolgreiches_ausbuchen_meldet_nichts(db):
    karte = _karte(db, "Sol Ring")
    ergebnis = erstelle_bestellung(
        positionen=[{"card_id": karte[0], "quantity": 1, "unit_price": "3,50"}],
        db_file=db)
    assert ergebnis["nicht_ausgebucht"] == []


def test_oberflaeche_warnt_bei_gescheitertem_ausbuchen(client, db, monkeypatch):
    karte = _karte(db, "Sol Ring")
    monkeypatch.setattr(direktverkauf, "sell_card", lambda *a, **k: False)

    seite = client.post("/orders/neu", data={
        "card_id": str(karte[0]), "card_name": "Sol Ring",
        "quantity": "1", "unit_price": "3,50",
    }, follow_redirects=True).get_data(as_text=True)

    assert "Sol Ring" in seite
    assert "nicht aus dem Bestand ausgebucht" in seite
    assert "von Hand prüfen" in seite
