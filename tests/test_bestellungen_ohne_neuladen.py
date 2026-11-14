"""Bestellübersicht: Speichern ohne Neuladen.

Zustand, Zuordnung, Adresse usw. werden im Hintergrund abgeschickt; die Seite
ersetzt nur die betroffene Bestellung. Am Server ändert sich nichts — die
Routen antworten weiter mit der Bestellseite. Geprüft wird hier, was das
Skript dafür braucht. Im Browser durchgespielt wurde: Zustand ändern,
zuordnen, Adresse speichern, Löschen abbrechen, Versendet — ohne Navigation,
Scrollposition unverändert, alles gespeichert.
"""

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


@pytest.fixture()
def seite(tmp_path, monkeypatch):
    pfad = str(tmp_path / "b.db")
    for modul in (TCGInventory, web, auth, setup_db, order_service, lager_manager):
        monkeypatch.setattr(modul, "DB_FILE", pfad)
    setup_db.initialize_database()
    with sqlite3.connect(pfad) as c:
        bid = c.execute(
            "INSERT INTO orders (buyer_name, email_message_id, date_received, status, "
            "order_number) VALUES ('K', 'm1', ?, 'open', '1')",
            (datetime.now().isoformat(timespec="seconds"),)).lastrowid
        c.execute("INSERT INTO order_items (order_id, card_name, quantity) "
                  "VALUES (?, 'Karte', 1)", (bid,))
        c.commit()
    web.app.config["TESTING"] = True
    klient = web.app.test_client()
    with klient.session_transaction() as s:
        s["user"] = "melvin"
    return klient, bid


def test_jede_bestellung_ist_einzeln_ersetzbar(seite):
    klient, bid = seite
    html = klient.get("/orders").get_data(as_text=True)
    assert f'id="bestellung-{bid}"' in html
    assert 'id="bestellungen-titel"' in html


def test_zustand_loest_ein_abfangbares_absenden_aus(seite):
    """``form.submit()`` umgeht das submit-Ereignis — dann lüde die Seite neu."""
    html = seite[0].get("/orders").get_data(as_text=True)
    assert "this.form.requestSubmit ? this.form.requestSubmit()" in html
    assert 'onchange="this.form.submit()"' not in html


def test_loeschen_fragt_weiter_nach(seite):
    """Die Rückfrage hing an jedem Formular einzeln und wäre nach dem Ersetzen
    einer Bestellung verloren — sie sitzt jetzt im gemeinsamen Handler."""
    html = seite[0].get("/orders").get_data(as_text=True)
    assert "Diese Bestellung wirklich löschen?" in html
    assert "querySelectorAll('.delete-order-form').forEach" not in html


def test_routen_antworten_weiter_mit_der_bestellseite(seite):
    """Das Skript übernimmt die Bestellung aus genau dieser Antwort."""
    klient, bid = seite
    item = sqlite3.connect(web.DB_FILE).execute(
        "SELECT id FROM order_items").fetchone()[0]
    antwort = klient.post(f"/orders/items/{item}/condition", data={"condition": "EX"},
                          headers={"X-Requested-With": "fetch"}, follow_redirects=True)
    html = antwort.get_data(as_text=True)
    assert antwort.request.path == "/orders"
    assert f'id="bestellung-{bid}"' in html
    assert '<option value="EX" selected>' in html
