"""Die alte Buchhaltung ist ausgebaut — die Altdaten bleiben.

Die Buchhaltung läuft als eigene Anwendung (Repository ZurFestung-Buchhaltung).
Im Inventarsystem gibt es dafür keine Seiten und keine Routen mehr. Was bleibt,
sind die Tabellen mit den früheren Buchungen: steuerrelevante Aufzeichnungen
werden nicht gelöscht, auch nicht beim Aufräumen von Code.
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
from TCGInventory import auth, lager_manager, order_service, setup_db, web  # noqa: E402

ALTBESTAND = ("journal", "belege", "markenart", "markenkauf",
              "markenverbrauch", "auszahlung")


@pytest.fixture()
def db(tmp_path):
    pfad = str(tmp_path / "b.db")
    for modul in (TCGInventory, web, auth, setup_db, order_service, lager_manager):
        modul.DB_FILE = pfad
    setup_db.initialize_database()
    return pfad


def _klient():
    web.app.config["TESTING"] = True
    klient = web.app.test_client()
    with klient.session_transaction() as s:
        s["user"] = "melvin"
    return klient


@pytest.mark.parametrize("pfad", [
    "/buchhaltung", "/buchhaltung/ausgabe", "/buchhaltung/briefmarken",
    "/buchhaltung/auszahlungen", "/buchhaltung/marge", "/buchhaltung/auswertung",
    "/buchhaltung/auswertung.csv",
])
def test_alte_seiten_gibt_es_nicht_mehr(db, pfad):
    assert _klient().get(pfad).status_code == 404


def test_keine_route_der_alten_buchhaltung_ist_uebrig(db):
    regeln = [str(r) for r in web.app.url_map.iter_rules()]
    assert not [r for r in regeln if r.startswith("/buchhaltung")]
    assert not [e for e in web.app.view_functions if e.startswith("bookkeeping")]


def test_altbestand_bleibt_in_der_datenbank(db):
    """Kein DROP TABLE: die Tabellen werden weiter angelegt und nicht geleert."""
    with sqlite3.connect(db) as conn:
        tabellen = {z[0] for z in conn.execute(
            "SELECT name FROM sqlite_master WHERE type = 'table'")}
    for name in ALTBESTAND:
        assert name in tabellen, f"{name} fehlt"


def test_vorhandene_buchungen_ueberstehen_den_start(db):
    with sqlite3.connect(db) as conn:
        conn.execute(
            "INSERT INTO journal (lfd_nr, erfasst_am, buchungsdatum, art, "
            "kategorie, betrag_cent) VALUES (1, '2026-07-01T10:00:00', "
            "'2026-07-01', 'ausgabe', 'Porto', 1234)")
        conn.commit()
    setup_db.initialize_database()                     # wie bei jedem Start
    with sqlite3.connect(db) as conn:
        assert conn.execute(
            "SELECT betrag_cent FROM journal").fetchall() == [(1234,)]


def test_menue_fuehrt_zur_eigenstaendigen_buchhaltung(db, monkeypatch):
    monkeypatch.delenv("BUCHHALTUNG_URL", raising=False)
    html = _klient().get("/dashboard").get_data(as_text=True)
    assert 'href="http://localhost:5001"' in html
    assert "Buchhaltung ↗" in html


def test_adresse_der_buchhaltung_ist_einstellbar(db, monkeypatch):
    monkeypatch.setenv("BUCHHALTUNG_URL", "https://buch.example/")
    html = _klient().get("/dashboard").get_data(as_text=True)
    assert 'href="https://buch.example/"' in html
