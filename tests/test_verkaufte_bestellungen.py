"""Nachdruck bereits verkaufter Bestellungen.

Wer auf „Verkauft" drückt, verliert den Weg zum Beileger: die Übersicht zeigt
nur offene Bestellungen. Die Angaben selbst bleiben aber vollständig erhalten —
``mark_order_sold`` setzt nur den Status und zieht die Karten ab.

Geprüft wird deshalb beides: dass die Seite die verkaufte Bestellung findet,
**und** dass sich dabei am Bestand nichts ändert.
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

import TCGInventory                                                  # noqa: E402
from TCGInventory import auth, lager_manager, order_service, setup_db, web  # noqa: E402


def _client(tmp_path):
    db = str(tmp_path / "verkauft.db")
    for modul in (TCGInventory, web, auth, setup_db, order_service, lager_manager):
        modul.DB_FILE = db
    setup_db.initialize_database()
    web.app.config["TESTING"] = True
    klient = web.app.test_client()
    with klient.session_transaction() as s:
        s["user"] = "tester"
    return db, klient


def _bestellung(db, *, nummer, kaeufer, status, adresse_bestaetigt=True,
                abgeschlossen="2026-08-10T12:00:00", positionen=None):
    """Eine Bestellung samt Positionen anlegen und ihre ID zurückgeben."""
    positionen = positionen or [("Sol Ring", 1, "Commander Masters", "NM", 2.50)]
    with sqlite3.connect(db) as conn:
        c = conn.cursor()
        c.execute(
            "INSERT INTO orders (buyer_name, email_message_id, date_received, status, "
            "date_completed, order_number, address, address_confirmed, amount_versand) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (kaeufer, f"msg-{nummer}", "2026-08-09T10:00:00", status, abgeschlossen,
             nummer, "Max Mustermann\nMusterweg 1\n12345 Musterstadt",
             1 if adresse_bestaetigt else 0, 1.00),
        )
        bestellung_id = c.lastrowid
        for name, menge, set_name, zustand, preis in positionen:
            c.execute(
                "INSERT INTO order_items (order_id, card_name, quantity, set_name, "
                "condition, unit_price, foil) VALUES (?, ?, ?, ?, ?, ?, 0)",
                (bestellung_id, name, menge, set_name, zustand, preis),
            )
        conn.commit()
    return bestellung_id


def _bestand(db):
    """Fingerabdruck des Bestands — muss über die Seite hinweg gleich bleiben."""
    with sqlite3.connect(db) as conn:
        return sorted(conn.execute(
            "SELECT id, quantity, status, storage_code FROM cards").fetchall())


# ---------------------------------------------------------------------------
# Die verkaufte Bestellung ist wieder erreichbar
# ---------------------------------------------------------------------------
def test_verkaufte_bestellung_erscheint_im_archiv(tmp_path):
    db, klient = _client(tmp_path)
    _bestellung(db, nummer="1294428289", kaeufer="KohlkopfKlaus", status="sold")

    seite = klient.get("/orders/verkauft")
    assert seite.status_code == 200
    text = seite.get_data(as_text=True)
    assert "1294428289" in text
    assert "KohlkopfKlaus" in text


def test_offene_bestellung_steht_nicht_im_archiv(tmp_path):
    db, klient = _client(tmp_path)
    _bestellung(db, nummer="1111111111", kaeufer="NochOffen", status="open")

    text = klient.get("/orders/verkauft").get_data(as_text=True)
    assert "1111111111" not in text
    assert "Noch keine verkaufte Bestellung" in text


def test_beileger_laesst_sich_nachtraeglich_drucken(tmp_path):
    """Der eigentliche Zweck: nach dem Verkauf noch drucken können."""
    db, klient = _client(tmp_path)
    bid = _bestellung(db, nummer="1294428289", kaeufer="KohlkopfKlaus", status="sold")

    antwort = klient.get(f"/orders/{bid}/shipping_note")
    assert antwort.status_code == 200
    assert antwort.mimetype == "application/pdf"
    assert antwort.data[:4] == b"%PDF"


def test_quittung_laesst_sich_nachtraeglich_drucken(tmp_path):
    db, klient = _client(tmp_path)
    bid = _bestellung(db, nummer="DV-2026-0001", kaeufer="Barverkauf", status="sold")

    antwort = klient.get(f"/orders/{bid}/quittung")
    assert antwort.status_code == 200
    assert antwort.data[:4] == b"%PDF"


# ---------------------------------------------------------------------------
# Suche und Blättern
# ---------------------------------------------------------------------------
def test_suche_findet_nach_nummer_und_kaeufer(tmp_path):
    db, klient = _client(tmp_path)
    _bestellung(db, nummer="1294428289", kaeufer="KohlkopfKlaus", status="sold")
    _bestellung(db, nummer="1300000001", kaeufer="AndereKundin", status="sold")

    nach_nummer = klient.get("/orders/verkauft?q=12944").get_data(as_text=True)
    assert "1294428289" in nach_nummer
    assert "1300000001" not in nach_nummer

    nach_name = klient.get("/orders/verkauft?q=Andere").get_data(as_text=True)
    assert "1300000001" in nach_name
    assert "1294428289" not in nach_name


def test_ohne_bestellnummer_wird_nicht_ueber_die_suche_verschluckt(tmp_path):
    """``order_number`` darf NULL sein — dann darf die Suche nicht abstürzen."""
    db, klient = _client(tmp_path)
    with sqlite3.connect(db) as conn:
        conn.execute(
            "INSERT INTO orders (buyer_name, email_message_id, date_received, status) "
            "VALUES ('OhneNummer', 'msg-x', '2026-08-09T10:00:00', 'sold')")
        conn.commit()

    antwort = klient.get("/orders/verkauft?q=OhneNummer")
    assert antwort.status_code == 200
    assert "OhneNummer" in antwort.get_data(as_text=True)


def test_blaettern_zeigt_jede_bestellung_genau_einmal(tmp_path):
    db, klient = _client(tmp_path)
    anzahl = web.VERKAUFTE_JE_SEITE + 3
    for i in range(anzahl):
        _bestellung(db, nummer=f"20000000{i:02d}", kaeufer=f"Kunde{i:02d}",
                    status="sold", abgeschlossen=f"2026-08-{(i % 28) + 1:02d}T12:00:00")

    gesehen = []
    for seite in (1, 2):
        text = klient.get(f"/orders/verkauft?seite={seite}").get_data(as_text=True)
        gesehen += [i for i in range(anzahl) if f"Kunde{i:02d}" in text]

    assert sorted(gesehen) == list(range(anzahl)), "fehlend oder doppelt"


@pytest.mark.parametrize("seite", ["0", "-5", "abc", ""])
def test_unsinnige_seitenzahl_fuehrt_nicht_zum_fehler(tmp_path, seite):
    db, klient = _client(tmp_path)
    _bestellung(db, nummer="1294428289", kaeufer="KohlkopfKlaus", status="sold")

    antwort = klient.get(f"/orders/verkauft?seite={seite}")
    assert antwort.status_code == 200
    assert "1294428289" in antwort.get_data(as_text=True)


# ---------------------------------------------------------------------------
# Die Seite fasst nichts an
# ---------------------------------------------------------------------------
def test_archiv_aendert_den_bestand_nicht(tmp_path):
    db, klient = _client(tmp_path)
    with sqlite3.connect(db) as conn:
        conn.execute(
            "INSERT INTO cards (name, set_code, language, condition, price, quantity, "
            "storage_code, status, collector_number, foil, item_type, date_added) "
            "VALUES ('Sol Ring','cmr','en','NM',2.0,3,'O01-S01-P1','verfügbar','1',0,"
            "'card','2026-07-09T10:00:00')")
        conn.commit()
    bid = _bestellung(db, nummer="1294428289", kaeufer="KohlkopfKlaus", status="sold")

    vorher = _bestand(db)
    klient.get("/orders/verkauft")
    klient.get(f"/orders/{bid}/shipping_note")
    klient.get(f"/orders/{bid}/quittung")
    assert _bestand(db) == vorher


def test_archiv_bietet_kein_verkaufen_und_kein_loeschen(tmp_path):
    """Ein zweiter Abzug oder ein Löschen wäre hier ein Unfall."""
    db, klient = _client(tmp_path)
    bid = _bestellung(db, nummer="1294428289", kaeufer="KohlkopfKlaus", status="sold")

    text = klient.get("/orders/verkauft").get_data(as_text=True)
    assert f"/orders/{bid}/mark_sold" not in text
    assert f"/orders/{bid}/delete" not in text


# ---------------------------------------------------------------------------
# Adresse: ohne Bestätigung kein Beileger — auch hier nicht
# ---------------------------------------------------------------------------
def test_ohne_bestaetigte_adresse_kein_beileger(tmp_path):
    db, klient = _client(tmp_path)
    bid = _bestellung(db, nummer="1294428289", kaeufer="KohlkopfKlaus",
                      status="sold", adresse_bestaetigt=False)

    antwort = klient.get(f"/orders/{bid}/shipping_note")
    assert antwort.status_code == 302          # zurück zur Liste, kein PDF

    text = klient.get("/orders/verkauft").get_data(as_text=True)
    assert "nicht bestätigt" in text


def test_adresse_bestaetigen_fuehrt_zurueck_ins_archiv(tmp_path):
    db, klient = _client(tmp_path)
    bid = _bestellung(db, nummer="1294428289", kaeufer="KohlkopfKlaus",
                      status="sold", adresse_bestaetigt=False)

    antwort = klient.post(
        f"/orders/{bid}/address",
        data={"address": "Max Mustermann\nMusterweg 1\n12345 Musterstadt",
              "zurueck": "verkauft", "q": "12944", "seite": "1"},
    )
    assert antwort.status_code == 302
    assert "/orders/verkauft" in antwort.headers["Location"]
    assert "q=12944" in antwort.headers["Location"]

    # …und jetzt lässt sich der Beileger drucken.
    assert klient.get(f"/orders/{bid}/shipping_note").data[:4] == b"%PDF"


def test_adresse_ohne_ziel_fuehrt_weiter_zur_offenen_liste(tmp_path):
    """Der bisherige Weg aus der offenen Liste darf sich nicht ändern."""
    db, klient = _client(tmp_path)
    bid = _bestellung(db, nummer="1294428289", kaeufer="KohlkopfKlaus", status="open")

    antwort = klient.post(f"/orders/{bid}/address", data={"address": "Neu\n12345 Ort"})
    assert antwort.status_code == 302
    assert antwort.headers["Location"].endswith("/orders")


def test_weiterleitung_laesst_sich_nicht_auf_fremde_adresse_biegen(tmp_path):
    """Das Ziel kommt aus einem festen Wert, nicht aus einer freien URL."""
    db, klient = _client(tmp_path)
    bid = _bestellung(db, nummer="1294428289", kaeufer="KohlkopfKlaus", status="sold")

    antwort = klient.post(
        f"/orders/{bid}/address",
        data={"address": "Neu\n12345 Ort", "zurueck": "https://example.invalid/"},
    )
    assert antwort.status_code == 302
    assert "example.invalid" not in antwort.headers["Location"]


# ---------------------------------------------------------------------------
# Anzeige der Beträge
# ---------------------------------------------------------------------------
def test_fehlender_preis_wird_nicht_als_null_verrechnet(tmp_path):
    """Aus einer Position ohne Preis darf keine über 0,00 € werden."""
    db, klient = _client(tmp_path)
    _bestellung(db, nummer="1294428289", kaeufer="KohlkopfKlaus", status="sold",
                positionen=[("Sol Ring", 1, "CMR", "NM", 2.50),
                            ("Black Lotus", 1, "LEA", "NM", None)])

    text = klient.get("/orders/verkauft").get_data(as_text=True)
    assert "2.50" in text or "2,50" in text
    assert "unvollständig" in text
