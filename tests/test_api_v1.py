"""Lese-API v1 für die Buchhaltungssoftware.

Geprüft wird vor allem, was die Buchhaltung darauf aufbauen muss: Token-Schutz,
stabile Beträge in Cent, ein Änderungs-Fingerabdruck und Datensparsamkeit.
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

import TCGInventory                       # noqa: E402
from TCGInventory import setup_db, web    # noqa: E402
from TCGInventory import api_v1 as api    # noqa: E402

TOKEN = "test-token-1234567890"


@pytest.fixture()
def client(tmp_path, monkeypatch):
    db = str(tmp_path / "inv.db")
    for mod in (TCGInventory, setup_db, web, api):
        mod.DB_FILE = db
    setup_db.initialize_database()
    with sqlite3.connect(db) as c:
        c.execute(
            "INSERT INTO orders (id,buyer_name,email_message_id,date_received,email_date,"
            "date_completed,status,order_number,address,address_confirmed,"
            "amount_gesamt,amount_gesamtwert,amount_versand,amount_gebuehren,"
            "amount_auszahlung) VALUES (1,'Sharqy','m1','2026-06-10','2026-06-10',"
            "'2026-06-11','sold','1292577138','Geheime Strasse 1\n12345 Ort',1,"
            "2.61,1.06,1.55,0.07,2.54)")
        c.execute("INSERT INTO order_items (order_id,card_name,quantity,unit_price,"
                  "set_code,language,condition,foil,match_status) VALUES "
                  "(1,'Force of Negation',1,1.06,'tla','en','NM',0,'matched')")
        c.execute("INSERT INTO orders (id,buyer_name,email_message_id,date_received,"
                  "status,order_number) VALUES (2,'Offen','m2','2026-06-20','open','x')")
        c.commit()
    monkeypatch.setenv("TCG_API_TOKEN", TOKEN)
    web.app.config["TESTING"] = True
    return web.app.test_client()


def _auth():
    return {"Authorization": f"Bearer {TOKEN}"}


# =========================================================================
# Zugriffsschutz
# =========================================================================

def test_ohne_token_kein_zugriff(client):
    assert client.get("/api/v1/orders").status_code == 401
    assert client.get("/api/v1/orders", headers={"Authorization": "Bearer falsch"}
                      ).status_code == 401


def test_ohne_konfigurierten_token_ist_die_api_aus(client, monkeypatch):
    """Lieber keine Schnittstelle als eine offene."""
    monkeypatch.delenv("TCG_API_TOKEN", raising=False)
    antwort = client.get("/api/v1/orders", headers=_auth())
    assert antwort.status_code == 503


def test_health_braucht_keinen_token(client):
    antwort = client.get("/api/v1/health")
    assert antwort.status_code == 200
    assert antwort.get_json()["status"] == "ok"


# =========================================================================
# Bestellungen
# =========================================================================

def test_liefert_nur_versendete_bestellungen(client):
    daten = client.get("/api/v1/orders", headers=_auth()).get_json()
    nummern = [b["bestellnummer"] for b in daten["bestellungen"]]
    assert nummern == ["1292577138"]          # die offene Bestellung fehlt


def test_betraege_kommen_als_cent(client):
    """Ganzzahlige Cent – die Buchhaltung soll nie mit Gleitkomma hantieren."""
    b = client.get("/api/v1/orders", headers=_auth()).get_json()["bestellungen"][0]
    assert b["betraege_cent"] == {
        "gesamt": 261, "warenwert": 106, "versand": 155,
        "gebuehren": 7, "auszahlung": 254,
    }
    assert all(isinstance(w, int) for w in b["betraege_cent"].values())
    assert b["positionen"][0]["einzelpreis_cent"] == 106


def test_kontrollrechnung_geht_auf(client):
    """Warenwert + Versand − Gebühr muss der Auszahlung entsprechen."""
    b = client.get("/api/v1/orders", headers=_auth()).get_json()["bestellungen"][0]
    w = b["betraege_cent"]
    assert w["gesamt"] - w["versand"] + w["versand"] - w["gebuehren"] == w["auszahlung"]


def test_keine_kundenadresse_in_der_antwort(client):
    """Datensparsamkeit: die EÜR braucht keine personenbezogenen Kundendaten."""
    roh = client.get("/api/v1/orders", headers=_auth()).get_data(as_text=True)
    assert "Geheime Strasse" not in roh
    assert "12345 Ort" not in roh
    b = client.get("/api/v1/orders", headers=_auth()).get_json()["bestellungen"][0]
    assert "adresse" not in b and "address" not in b


def test_stichtag_filtert(client):
    daten = client.get("/api/v1/orders?ab=2026-07-01", headers=_auth()).get_json()
    assert daten["anzahl"] == 0
    daten = client.get("/api/v1/orders?ab=2026-06-01", headers=_auth()).get_json()
    assert daten["anzahl"] == 1


def test_detail_und_unbekannte_bestellung(client):
    assert client.get("/api/v1/orders/1", headers=_auth()).status_code == 200
    assert client.get("/api/v1/orders/999", headers=_auth()).status_code == 404


# =========================================================================
# Änderungserkennung
# =========================================================================

def test_hash_bleibt_bei_unveraenderten_daten_gleich(client):
    erst = client.get("/api/v1/orders/1", headers=_auth()).get_json()["inhalt_hash"]
    zweit = client.get("/api/v1/orders/1", headers=_auth()).get_json()["inhalt_hash"]
    assert erst == zweit and len(erst) == 64


def test_hash_aendert_sich_bei_geaendertem_betrag(client):
    """Damit erkennt die Buchhaltung eine nachträglich veränderte Bestellung."""
    vorher = client.get("/api/v1/orders/1", headers=_auth()).get_json()["inhalt_hash"]
    with sqlite3.connect(api.DB_FILE) as c:
        c.execute("UPDATE orders SET amount_gebuehren = 0.20 WHERE id = 1")
        c.commit()
    nachher = client.get("/api/v1/orders/1", headers=_auth()).get_json()["inhalt_hash"]
    assert vorher != nachher


def test_hash_ignoriert_nicht_finanzielle_aenderungen(client):
    """Eine korrigierte Adresse ist keine buchhalterische Änderung."""
    vorher = client.get("/api/v1/orders/1", headers=_auth()).get_json()["inhalt_hash"]
    with sqlite3.connect(api.DB_FILE) as c:
        c.execute("UPDATE orders SET address = 'Neue Strasse 9' WHERE id = 1")
        c.commit()
    assert client.get("/api/v1/orders/1", headers=_auth()).get_json()["inhalt_hash"] == vorher


def test_bestand_summary(client):
    daten = client.get("/api/v1/stock/summary", headers=_auth()).get_json()
    assert "karten" in daten and "produkte" in daten
