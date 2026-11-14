"""Mappenblätter haben zwei Seiten: 18 Plätze je Blatt.

Der Fall aus dem Betrieb: Ordner „Star Trek" mit 30 Seiten, 250 Karten — das
System meldete „225 frei, es fehlen 3 Seiten". Ein Blatt hat aber Vorder- und
Rückseite mit je 9 Fächern; fest eingebaut waren 9 Plätze je Seite. Dazu kam:
Beim Erhöhen der Seitenzahl wurden die neuen Seiten gar nicht angelegt.
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
                          setup_db, web)


@pytest.fixture()
def db(tmp_path, monkeypatch):
    pfad = str(tmp_path / "o.db")
    for modul in (TCGInventory, web, auth, setup_db, order_service, lager_manager):
        monkeypatch.setattr(modul, "DB_FILE", pfad)
    setup_db.initialize_database()
    return pfad


def _klient():
    web.app.config["TESTING"] = True
    klient = web.app.test_client()
    with klient.session_transaction() as s:
        s["user"] = "melvin"
    return klient


def _plaetze(db, ordner=1):
    with sqlite3.connect(db) as conn:
        return [z[0] for z in conn.execute(
            "SELECT code FROM storage_slots WHERE code LIKE ?", (f"O{ordner:02d}-%",))]


def _karte(nr):
    lager_manager.add_card(f"Karte {nr}", "trk", "en", "NM", 0.2, 1,
                           folder_id=1, collector_number=str(nr))


# ---------------------------------------------------------------------------
# Neuer Ordner
# ---------------------------------------------------------------------------

def test_neuer_ordner_hat_18_plaetze_je_seite(db):
    html = _klient().get("/folders/add").get_data(as_text=True)
    assert '<option value="18" selected>' in html
    _klient().post("/folders/add", data={"name": "Star Trek", "pages": "30",
                                         "plaetze_je_seite": "18"})
    assert len(_plaetze(db)) == 540
    assert lager_manager.plaetze_je_seite(1) == 18


def test_neun_plaetze_bleiben_waehlbar(db):
    _klient().post("/folders/add", data={"name": "Alt", "pages": "2",
                                         "plaetze_je_seite": "9"})
    assert len(_plaetze(db)) == 18


def test_unsinniger_wert_wird_nicht_uebernommen(db):
    _klient().post("/folders/add", data={"name": "X", "pages": "1",
                                         "plaetze_je_seite": "12"})
    assert lager_manager.plaetze_je_seite(1) == 18


def test_erst_vorderseite_dann_rueckseite_dann_naechstes_blatt(db):
    """Natürliche Reihenfolge: P2 vor P10 — so wird die Mappe befüllt."""
    lager_manager.add_folder("Star Trek", 2, 18)
    lager_manager.create_binder(1, 2)
    for nr in range(1, 21):
        _karte(nr)
    with sqlite3.connect(db) as conn:
        plaetze = [z[0] for z in conn.execute(
            "SELECT storage_code FROM cards ORDER BY id")]
    assert plaetze[:3] == ["O01-S01-P1", "O01-S01-P2", "O01-S01-P3"]
    assert plaetze[8:11] == ["O01-S01-P9", "O01-S01-P10", "O01-S01-P11"]
    assert plaetze[17:20] == ["O01-S01-P18", "O01-S02-P1", "O01-S02-P2"]


# ---------------------------------------------------------------------------
# Vorhandener Ordner
# ---------------------------------------------------------------------------

def test_vorhandene_ordner_behalten_neun_plaetze(db):
    """Wie die Karten physisch liegen, weiß nur der Mensch."""
    with sqlite3.connect(db) as conn:
        conn.execute("INSERT INTO folders (id, name, pages) VALUES (7, 'Alt', 30)")
        conn.commit()
    assert lager_manager.plaetze_je_seite(7) == 9
    html = _klient().get("/folders/edit/7").get_data(as_text=True)
    assert '<option value="9" selected>' in html
    assert "Beim Umstellen auf 18" in html


def test_umstellen_auf_18_ergaenzt_die_rueckseiten(db):
    """Der Ordner aus dem Betrieb: 30 Seiten, 45 Plätze schon belegt."""
    lager_manager.add_folder("Star Trek", 30, 9)
    lager_manager.create_binder(1, 30)
    for nr in range(1, 46):
        _karte(nr)
    vorher = {k: v for k, v in sqlite3.connect(db).execute(
        "SELECT id, storage_code FROM cards")}

    antwort = _klient().post("/folders/edit/1", data={
        "name": "Star Trek", "id": "1", "pages": "30", "plaetze_je_seite": "18"},
        follow_redirects=True)
    assert "540 Plätze, davon 495 frei" in antwort.get_data(as_text=True)
    nachher = {k: v for k, v in sqlite3.connect(db).execute(
        "SELECT id, storage_code FROM cards")}
    assert nachher == vorher, "keine Karte verliert ihren Platz"
    # Die nächste Karte kommt auf die Rückseite von Blatt 1.
    _karte(999)
    assert sqlite3.connect(db).execute(
        "SELECT storage_code FROM cards WHERE collector_number = '999'"
    ).fetchone()[0] == "O01-S01-P10"


def test_zurueck_auf_9_entfernt_nur_freie_rueckseiten(db):
    lager_manager.add_folder("M", 1, 18)
    lager_manager.create_binder(1, 1)
    for nr in range(1, 11):                          # P1..P10 belegt
        _karte(nr)
    lager_manager.edit_folder(1, "M", 1, None, 9)
    plaetze = sorted(_plaetze(db))
    assert "O01-S01-P10" in plaetze, "belegter Platz bleibt"
    assert "O01-S01-P11" not in plaetze and "O01-S01-P18" not in plaetze


def test_mehr_seiten_legen_wirklich_neue_seiten_an(db):
    """Früher: 25 → 30 legte die Seiten 1–5 noch einmal an statt 26–30."""
    lager_manager.add_folder("M", 25, 9)
    lager_manager.create_binder(1, 25)
    assert len(_plaetze(db)) == 225
    lager_manager.edit_folder(1, "M", 30)
    plaetze = _plaetze(db)
    assert len(plaetze) == 270
    assert "O01-S30-P9" in plaetze


# ---------------------------------------------------------------------------
# Anzeige
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("code,text", [
    ("O07-S03-P4", "Ordner 07 · Seite 3 · Platz 4"),
    ("O07-S03-P12", "Ordner 07 · Seite 3 · Rückseite · Platz 3"),
    ("O07-S03-P18", "Ordner 07 · Seite 3 · Rückseite · Platz 9"),
    ("Regal 2", "Regal 2"),
    ("", ""),
])
def test_platz_lesbar(code, text):
    assert web.platz_lesbar(code) == text


def test_warteschlange_rechnet_fehlende_seiten_mit_18(db):
    lager_manager.add_folder("Star Trek", 1, 18)
    lager_manager.create_binder(1, 1)
    web.UPLOAD_QUEUE[:] = [{"name": f"K{i}", "set_code": "trk",
                            "collector_number": str(i), "language": "en",
                            "foil": False, "folder_id": "1", "item_type": "card"}
                           for i in range(1, 41)]                 # 40 Plätze
    try:
        html = _klient().get("/cards/upload_queue").get_data(as_text=True)
    finally:
        web.UPLOAD_QUEUE.clear()
    assert "40 Plätze gebraucht, 18 frei" in html
    assert "Es fehlen 2 Seiten (je 18 Plätze)" in html
