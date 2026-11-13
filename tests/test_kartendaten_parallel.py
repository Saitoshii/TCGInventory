"""Kartendaten: nie zwei Aktualisierungen gleichzeitig.

Der Fall aus dem Betrieb: Die Kartendaten auf dem Pi blieben auf dem Stand
vom 08.08., obwohl sich die Anwendung selbst um die Aktualisierung kümmern
sollte. Die Anwendung lief mit ``debug=True`` — Flask startet dann zwei
Prozesse, und der Startblock lief in beiden. Zwei Aktualisierungen schrieben
in dieselbe Zwischendatei; die zweite nahm der ersten die Datei weg.
"""

import os
import sqlite3
import sys
import threading
import types
from datetime import datetime, timedelta

import pytest

sys.modules.setdefault("cv2", types.SimpleNamespace())
_pyz = types.ModuleType("pyzbar")
_pyz.pyzbar = types.SimpleNamespace(decode=lambda *a, **k: [])
sys.modules.setdefault("pyzbar", _pyz)
sys.modules.setdefault("pyzbar.pyzbar", _pyz.pyzbar)

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(__file__))))

from TCGInventory import build_card_db as bcd                    # noqa: E402
from TCGInventory import web                                      # noqa: E402


def _karte(i):
    return {"id": f"{i:08d}-aaaa-bbbb-cccc-dddddddddddd", "name": f"Karte {i}",
            "set": "tst", "set_name": "Test", "lang": "en",
            "collector_number": str(i), "cardmarket_id": i}


def test_zweite_aktualisierung_wartet_nicht_sondern_meldet_sich(tmp_path, monkeypatch):
    """Solange eine läuft, wird keine zweite gestartet — auch nicht aus einem
    anderen Prozess (die Sperre ist eine Datei)."""
    ziel = tmp_path / "default-cards.db"
    drin, weiter = threading.Event(), threading.Event()

    def langsam(db_path, fortschritt, erzwingen, vor_tausch):
        drin.set()
        weiter.wait(5)
        return {"aktualisiert": True, "anzahl": 1, "stand": "x"}

    monkeypatch.setattr(bcd, "_aktualisiere_von_scryfall", langsam)
    erster = threading.Thread(target=bcd.aktualisiere_von_scryfall, args=(ziel,))
    erster.start()
    assert drin.wait(5)
    try:
        with pytest.raises(bcd.LaeuftBereits):
            bcd.aktualisiere_von_scryfall(ziel)
    finally:
        weiter.set()
        erster.join(5)
    # Danach ist die Sperre wieder frei.
    assert bcd.aktualisiere_von_scryfall(ziel)["aktualisiert"] is True


def test_sperre_wird_auch_bei_einem_fehler_frei(tmp_path, monkeypatch):
    ziel = tmp_path / "default-cards.db"

    def kaputt(*_):
        raise OSError("kein Netz")

    monkeypatch.setattr(bcd, "_aktualisiere_von_scryfall", kaputt)
    with pytest.raises(OSError):
        bcd.aktualisiere_von_scryfall(ziel)
    with pytest.raises(OSError, match="kein Netz"):          # nicht LaeuftBereits
        bcd.aktualisiere_von_scryfall(ziel)


def test_zwischendatei_traegt_die_prozessnummer(tmp_path, monkeypatch):
    """Zweite Sicherung: zwei Aufbauten teilen sich keine Zwischendatei mehr."""
    ziel = tmp_path / "default-cards.db"
    gesehen = []
    echt = sqlite3.connect

    def merken(pfad, *a, **k):
        gesehen.append(str(pfad))
        return echt(pfad, *a, **k)

    monkeypatch.setattr(bcd.sqlite3, "connect", merken)
    bcd.schreibe_datenbank([_karte(1)], ziel)
    assert gesehen[0].endswith(f".neu-{os.getpid()}")
    assert sorted(p.name for p in tmp_path.iterdir()) == ["default-cards.db"]


def test_aufbau_in_einem_fremden_prozess_wird_nicht_zerstoert(tmp_path):
    """Liegt die Zwischendatei eines anderen Prozesses da, bleibt sie liegen."""
    ziel = tmp_path / "default-cards.db"
    fremd = tmp_path / "default-cards.db.neu-999999"
    fremd.write_bytes(b"in Arbeit")
    bcd.schreibe_datenbank([_karte(1)], ziel)
    assert fremd.read_bytes() == b"in Arbeit"


# ---------------------------------------------------------------------------
# Hintergrunddienste nur im Serverprozess
# ---------------------------------------------------------------------------

def test_hintergrunddienste_nur_im_serverprozess(monkeypatch):
    monkeypatch.delenv("WERKZEUG_RUN_MAIN", raising=False)
    assert web._im_serverprozess(debug=False) is True
    assert web._im_serverprozess(debug=True) is False      # Neulade-Wächter
    monkeypatch.setenv("WERKZEUG_RUN_MAIN", "true")
    assert web._im_serverprozess(debug=True) is True


def test_startblock_hat_keinen_festen_debug_modus():
    quelle = open(web.__file__, encoding="utf-8").read()
    start = quelle[quelle.index('if __name__ == "__main__":'):]
    assert "debug=True" not in start
    assert "_im_serverprozess(debug)" in start


# ---------------------------------------------------------------------------
# Hängengebliebene Läufe blockieren nicht für immer
# ---------------------------------------------------------------------------

def test_haengende_aktualisierung_gibt_den_weg_frei(monkeypatch):
    monkeypatch.setitem(web.CARDDATA_STATUS, "laeuft", True)
    monkeypatch.setitem(web.CARDDATA_STATUS, "seit", datetime.now() - timedelta(hours=2))
    monkeypatch.setitem(web.CARDDATA_STATUS, "fehler", None)
    assert web._kartendaten_laeuft() is False
    assert "nicht zurück" in web.CARDDATA_STATUS["fehler"]


def test_laufende_aktualisierung_bleibt_laufend(monkeypatch):
    monkeypatch.setitem(web.CARDDATA_STATUS, "laeuft", True)
    monkeypatch.setitem(web.CARDDATA_STATUS, "seit", datetime.now() - timedelta(minutes=5))
    assert web._kartendaten_laeuft() is True


def test_status_ist_als_json_lesbar(monkeypatch):
    monkeypatch.setitem(web.CARDDATA_STATUS, "seit", datetime(2026, 11, 13, 23, 30))
    web.app.config["TESTING"] = True
    klient = web.app.test_client()
    with klient.session_transaction() as s:
        s["user"] = "melvin"
    daten = klient.get("/system/kartendaten/status").get_json()
    assert daten["seit"] == "2026-11-13T23:30:00"


def test_laeuft_bereits_ist_kein_fehler(monkeypatch):
    def besetzt(**_):
        raise bcd.LaeuftBereits("Eine andere Aktualisierung der Kartendaten läuft bereits.")
    monkeypatch.setattr(web.build_card_db, "aktualisiere_von_scryfall", besetzt)
    for k, v in {"laeuft": True, "fehler": None, "fertig": False}.items():
        monkeypatch.setitem(web.CARDDATA_STATUS, k, v)
    web._aktualisiere_kartendaten(False)
    assert web.CARDDATA_STATUS["fehler"] is None
    assert "läuft bereits" in web.CARDDATA_STATUS["meldung"]
