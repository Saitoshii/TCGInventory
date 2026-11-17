"""Bestelleingang und Anmeldung: keine Bestellung geht unbemerkt verloren.

* Eine Bestellmail, aus der keine Positionen erkannt wurden, wurde als
  erledigt markiert und verworfen — die Bestellung tauchte nirgends auf.
* Mails, deren Bestellung schon existierte, deren Markierung in Gmail aber
  scheiterte, kamen bei jeder Abfrage wieder.
* Nach abgelaufener Anmeldung führte „Versendet / Verkauft" nach dem
  Anmelden auf „Method Not Allowed" — die Bestellung war nicht abgeschlossen,
  und niemand sagte es.
"""

import io
import os
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

MAIL = """Bestellnummer: 1250416803
Käufer: KohlkopfKlaus
Status: Bezahlt

Max Mustermann
Musterstraße 12
12345 Musterstadt
Deutschland

Sendungsverfolgung:

1x Rumble Arena (Avatar: The Last Airbender) - C - Englisch - NM 0,03 EUR
"""


@pytest.fixture()
def db(tmp_path, monkeypatch):
    pfad = str(tmp_path / "b.db")
    for modul in (TCGInventory, web, auth, setup_db, order_service, lager_manager):
        monkeypatch.setattr(modul, "DB_FILE", pfad)
    setup_db.initialize_database()
    return pfad


def _q(pfad, sql, *werte):
    with sqlite3.connect(pfad) as conn:
        return conn.execute(sql, werte).fetchall()


@pytest.fixture()
def postfach(monkeypatch):
    """Ein Gmail-Ersatz: Mails darin, und was markiert wurde."""
    mails = {}
    markiert = []
    monkeypatch.setattr(order_service, "get_gmail_service", lambda: object())
    monkeypatch.setattr(order_service, "fetch_cardmarket_emails",
                        lambda service, schon: [{"id": i} for i in mails if i not in schon])
    monkeypatch.setattr(order_service, "get_email_body", lambda m: mails[m["id"]][1])
    monkeypatch.setattr(order_service, "get_email_subject",
                        lambda m: mails[m["id"]][0])
    monkeypatch.setattr(order_service, "get_email_date",
                        lambda m: datetime.now().isoformat(timespec="seconds"))

    def markieren(service, message_id):
        markiert.append(message_id)
        return True

    monkeypatch.setattr(order_service, "mark_message_processed", markieren)
    return mails, markiert


def test_mail_ohne_erkannte_positionen_wird_nicht_verworfen(db, postfach):
    mails, markiert = postfach
    mails["m1"] = ("Bestellung 99 für Neu: Bitte versenden",
                   "Ganz neues Format\nArtikel: Spock (Star Trek)\n")
    ok, meldung, anzahl = order_service.OrderIngestionService().sync_orders()

    assert ok and anzahl == 1
    assert "keine Positionen erkannt" in meldung
    assert _q(db, "SELECT buyer_name, status FROM orders") == [("Neu", "open")]
    assert markiert == ["m1"]

    web.app.config["TESTING"] = True
    klient = web.app.test_client()
    with klient.session_transaction() as s:
        s["user"] = "melvin"
    html = klient.get("/orders").get_data(as_text=True)
    assert "Aus der Bestellmail wurden keine Positionen erkannt" in html
    kaestchen = html.split('name="ohne_positionen"')[1].split(">")[0]
    assert "required" in kaestchen


def test_mail_ohne_lesbaren_text_wird_trotzdem_angelegt(db, postfach):
    mails, _ = postfach
    mails["m2"] = ("Bestellung 98 für Leer: Bitte versenden", "")
    order_service.OrderIngestionService().sync_orders()
    assert _q(db, "SELECT buyer_name FROM orders") == [("Leer",)]


def test_vorhandene_bestellung_wird_nur_noch_markiert(db, postfach):
    mails, markiert = postfach
    mails["m3"] = ("Bestellung 1250416803 für KohlkopfKlaus: Bitte versenden", MAIL)
    dienst = order_service.OrderIngestionService()
    dienst.sync_orders()
    assert markiert == ["m3"]

    # Ein Neustart vergisst, was schon bearbeitet ist; Gmail konnte nicht
    # markieren — die Mail kommt wieder.
    neu = order_service.OrderIngestionService()
    ok, meldung, anzahl = neu.sync_orders()
    assert anzahl == 0
    assert markiert == ["m3", "m3"]
    assert "m3" in neu.processed_message_ids
    assert _q(db, "SELECT COUNT(*) FROM orders") == [(1,)]
    assert _q(db, "SELECT COUNT(*) FROM order_items") == [(1,)]


# ---------------------------------------------------------------------------
# Anmeldung abgelaufen
# ---------------------------------------------------------------------------
def _bestellung(db):
    with sqlite3.connect(db) as conn:
        bid = conn.execute(
            "INSERT INTO orders (buyer_name, email_message_id, date_received, "
            "status) VALUES ('K', 'x', ?, 'open')",
            (datetime.now().isoformat(),)).lastrowid
        conn.commit()
    return bid


def test_abgelaufene_anmeldung_fuehrt_zurueck_und_sagt_es(db):
    auth.register_user("melvin", "geheim")
    bid = _bestellung(db)
    web.app.config["TESTING"] = True
    klient = web.app.test_client()               # nicht angemeldet

    antwort = klient.post(f"/orders/{bid}/mark_sold",
                          headers={"Referer": "http://localhost/orders"})
    assert antwort.status_code == 302
    assert antwort.headers["Location"].endswith("/login?next=/orders")
    assert _q(db, "SELECT status FROM orders") == [("open",)]

    html = klient.get(antwort.headers["Location"]).get_data(as_text=True)
    assert "die letzte Aktion wurde NICHT ausgeführt" in html

    nach = klient.post("/login?next=/orders",
                       data={"username": "melvin", "password": "geheim"})
    assert nach.headers["Location"].endswith("/orders")
    assert klient.get(nach.headers["Location"]).status_code == 200


def test_get_nach_abgelaufener_anmeldung_kommt_mit_suche_zurueck(db):
    auth.register_user("melvin", "geheim")
    web.app.config["TESTING"] = True
    antwort = web.app.test_client().get("/orders?alle=1")
    assert antwort.headers["Location"].endswith("/login?next=/orders?alle%3D1")


@pytest.mark.parametrize("ziel", ["https://boese.example/", "//boese.example/x",
                                  "javascript:alert(1)"])
def test_nach_dem_anmelden_nur_eigene_seiten(db, ziel):
    auth.register_user("melvin", "geheim")
    web.app.config["TESTING"] = True
    antwort = web.app.test_client().post(
        "/login", query_string={"next": ziel},
        data={"username": "melvin", "password": "geheim"})
    assert antwort.headers["Location"] in ("/", "/dashboard",
                                           "http://localhost/", "http://localhost/dashboard")


# ---------------------------------------------------------------------------
# Kleinigkeiten, die den Betrieb stören
# ---------------------------------------------------------------------------
def test_startseite_mit_karte_ohne_preis(db):
    with sqlite3.connect(db) as conn:
        conn.execute("INSERT INTO cards (name, set_code, quantity, status) "
                     "VALUES ('Data', 'trk', 1, 'verfügbar')")
        conn.commit()
    web.app.config["TESTING"] = True
    klient = web.app.test_client()
    with klient.session_transaction() as s:
        s["user"] = "melvin"
    assert klient.get("/dashboard").status_code == 200


def test_hochgeladene_kartendaten_werden_sofort_benutzt(db, tmp_path, monkeypatch):
    """Die Anwendung las bis zum Neustart weiter die alte Datei."""
    monkeypatch.setattr(web, "__file__", str(tmp_path / "web.py"))
    zurueckgesetzt = []
    monkeypatch.setattr(web.card_scanner, "reset_card_database",
                        lambda: zurueckgesetzt.append(1))
    datei = tmp_path / "neu.db"
    with sqlite3.connect(datei) as conn:
        conn.execute("CREATE TABLE cards (id TEXT PRIMARY KEY, name TEXT)")
        conn.commit()
    web.app.config["TESTING"] = True
    klient = web.app.test_client()
    with klient.session_transaction() as s:
        s["user"] = "melvin"
    klient.post("/upload_database", data={
        "db_file": (io.BytesIO(datei.read_bytes()), "default-cards.db"),
        "confirm": "1"}, content_type="multipart/form-data")
    assert (tmp_path / "data" / "default-cards.db").exists()
    assert zurueckgesetzt == [1]
