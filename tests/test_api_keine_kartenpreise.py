"""Die API gibt keine Kartenpreise heraus.

Es gibt keinen Cardmarket-API-Zugang, und die Preisspalte an einer Karte wird
nicht gepflegt: dort steht ein Einkaufs- oder Marktpreis von irgendwann. Fuer
die Buchhaltung ist allein verbindlich, was Cardmarket in der Bestellmail
schreibt.

Damit das nicht durch eine bequeme Ergaenzung aufweicht, wird hier festgehalten:
aus der Tabelle ``cards`` verlaesst kein Betrag dieses System. Die Betraege der
Schnittstelle stammen ausschliesslich aus den beim Mail-Import gespeicherten
Feldern.
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
from TCGInventory import api_v1, auth, lager_manager, order_service, setup_db, web  # noqa: E402

TOKEN = "t" * 40


@pytest.fixture()
def klient(tmp_path, monkeypatch):
    db = str(tmp_path / "api.db")
    for modul in (TCGInventory, web, auth, setup_db, order_service, lager_manager,
                  api_v1):
        modul.DB_FILE = db
    setup_db.initialize_database()
    monkeypatch.setenv("TCG_API_TOKEN", TOKEN)
    web.app.config["TESTING"] = True
    return db, web.app.test_client()


def _bestellung_mit_karte(db):
    """Eine verkaufte Bestellung, deren Karte einen ganz anderen Preis traegt."""
    with sqlite3.connect(db) as conn:
        c = conn.cursor()
        c.execute(
            "INSERT INTO cards (name, set_code, language, condition, price, "
            "quantity, storage_code, status, collector_number, foil, item_type, "
            "date_added) VALUES ('Force of Negation','mh1','en','NM',77.77,1,"
            "'O01-S01-P1','verfügbar','1',0,'card','2026-07-09T10:00:00')")
        c.execute(
            "INSERT INTO orders (buyer_name, email_message_id, date_received, "
            "status, date_completed, order_number, amount_gesamt, "
            "amount_gesamtwert, amount_versand, amount_gebuehren, "
            "amount_auszahlung) VALUES ('Sharqy','msg-1','2026-06-10T10:00:00',"
            "'sold','2026-06-11T10:00:00','1292577138',2.61,1.06,1.55,0.07,2.54)")
        bid = c.lastrowid
        c.execute(
            "INSERT INTO order_items (order_id, card_name, quantity, unit_price) "
            "VALUES (?, 'Force of Negation', 1, 1.06)", (bid,))
        conn.commit()
    return bid


def _hole(klient, pfad):
    return klient.get(pfad, headers={"Authorization": f"Bearer {TOKEN}"})


def test_bestellung_liefert_nur_betraege_aus_der_mail(klient):
    db, c = klient
    _bestellung_mit_karte(db)

    antwort = _hole(c, "/api/v1/orders")
    assert antwort.status_code == 200
    bestellung = antwort.get_json()["bestellungen"][0]

    # Genau die Werte aus der Mail, in Cent.
    assert bestellung["betraege_cent"] == {
        "gesamt": 261, "warenwert": 106, "versand": 155,
        "gebuehren": 7, "auszahlung": 254,
    }
    # Der Preis an der Karte (77,77 €) taucht nirgends auf.
    assert "7777" not in antwort.get_data(as_text=True)
    assert "77.77" not in antwort.get_data(as_text=True)


def test_positionspreis_stammt_aus_der_bestellung_nicht_aus_dem_bestand(klient):
    db, c = klient
    _bestellung_mit_karte(db)

    daten = _hole(c, "/api/v1/orders").get_json()
    bestellung = daten["bestellungen"][0]
    position = bestellung["positionen"][0]
    assert position["einzelpreis_cent"] == 106       # aus order_items, nicht cards


def test_bestandskennzahlen_enthalten_keinen_wert(klient):
    """Die Bestandszahlen sind Stueck und Positionen — kein Geldbetrag."""
    db, c = klient
    _bestellung_mit_karte(db)

    daten = _hole(c, "/api/v1/stock/summary").get_json()
    text = str(daten)
    assert "77" not in text, f"ein Kartenpreis ist durchgerutscht: {daten}"
    for bereich in daten.values():
        assert set(bereich) == {"positionen", "stueck"}, bereich


def test_keine_abfrage_liest_preise_aus_cards():
    """Statische Gegenprobe am Quelltext.

    Falls jemand spaeter eine Abfrage ergaenzt, die einen Betrag aus ``cards``
    holt, faellt es hier auf — auch ohne dass ein Endpunkt dafuer existiert.
    """
    quelle = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                          "api_v1.py")
    with open(quelle, encoding="utf-8") as datei:
        text = datei.read().lower()
    for verdaechtig in ("cards.price", "c.price", "sum(price", "select price"):
        assert verdaechtig not in text, (
            f"„{verdaechtig}" + "\" in api_v1.py — Kartenpreise gehoeren nicht "
            "in die Buchhaltung")
