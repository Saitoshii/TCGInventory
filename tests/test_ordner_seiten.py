"""Ordner vergrößern: die neuen Seiten müssen wirklich Plätze bekommen.

Der Fall aus dem Betrieb: Ordner „Star Trek" mit 30 Seiten (9 Plätze je
Seite = 270), die Warteschlange meldete 225 freie Plätze — 225 = 25 × 9. Der
Ordner war von 25 auf 30 Seiten erhöht worden, und ``edit_folder`` legte dabei
``create_binder(ordner, 30 - 25)`` an: die Seiten 1–5, die es schon gab,
statt 26–30. Es kam kein einziger Platz dazu.
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


def _plaetze(db, ordner=7):
    with sqlite3.connect(db) as conn:
        return {code: belegt for code, belegt in conn.execute(
            "SELECT code, is_occupied FROM storage_slots WHERE code LIKE ?",
            (f"O{ordner:02d}-%",))}


def _klient():
    web.app.config["TESTING"] = True
    klient = web.app.test_client()
    with klient.session_transaction() as s:
        s["user"] = "melvin"
    return klient


def _ordner_mit_25_seiten(db):
    with sqlite3.connect(db) as conn:
        conn.execute("INSERT INTO folders (id, name, pages) VALUES (7, 'Star Trek', 25)")
        conn.commit()
    lager_manager.create_binder(7, 25)


def test_neun_plaetze_je_seite(db):
    _ordner_mit_25_seiten(db)
    plaetze = _plaetze(db)
    assert len(plaetze) == 225
    assert "O07-S01-P9" in plaetze and "O07-S01-P10" not in plaetze


def test_von_25_auf_30_seiten_kommen_45_plaetze_dazu(db):
    _ordner_mit_25_seiten(db)
    _klient().post("/folders/edit/7", data={"name": "Star Trek", "id": "7",
                                            "pages": "30"})
    plaetze = _plaetze(db)
    assert len(plaetze) == 270
    assert "O07-S26-P1" in plaetze and "O07-S30-P9" in plaetze


def test_vergroessern_laesst_belegte_plaetze_belegt(db):
    _ordner_mit_25_seiten(db)
    lager_manager.add_card("Academy Instructor", "trk", "en", "NM", 0.2, 1,
                           folder_id=7, collector_number="137")
    assert _plaetze(db)["O07-S01-P1"] == 1
    lager_manager.edit_folder(7, "Star Trek", 30)
    assert _plaetze(db)["O07-S01-P1"] == 1


def test_start_repariert_ordner_mit_fehlenden_plaetzen(db):
    """Ordner, die schon mit dem alten Fehler vergrößert wurden, bekommen ihre
    Plätze beim nächsten Start — ohne dass jemand etwas tun muss."""
    _ordner_mit_25_seiten(db)
    with sqlite3.connect(db) as conn:                  # so steht es auf dem Pi
        conn.execute("UPDATE folders SET pages = 30 WHERE id = 7")
        conn.commit()
    assert len(_plaetze(db)) == 225

    setup_db.initialize_database()
    plaetze = _plaetze(db)
    assert len(plaetze) == 270
    assert sum(plaetze.values()) == 0


def test_reparatur_markiert_schon_vergebene_plaetze_als_belegt(db):
    """Eine Karte, der von Hand ein Platz auf Seite 28 gegeben wurde, obwohl
    es ihn noch nicht gab: nach der Reparatur ist er belegt, nicht frei."""
    _ordner_mit_25_seiten(db)
    with sqlite3.connect(db) as conn:
        conn.execute("UPDATE folders SET pages = 30 WHERE id = 7")
        conn.execute("INSERT INTO cards (name, set_code, quantity, storage_code, "
                     "folder_id) VALUES ('X', 'trk', 1, 'O07-S28-P4', 7)")
        conn.commit()
    setup_db.initialize_database()
    assert _plaetze(db)["O07-S28-P4"] == 1


def test_reparatur_ist_wiederholbar_und_entfernt_nichts(db):
    _ordner_mit_25_seiten(db)
    with sqlite3.connect(db) as conn:
        conn.execute("UPDATE folders SET pages = 20 WHERE id = 7")   # verkleinert
        conn.commit()
    setup_db.initialize_database()
    setup_db.initialize_database()
    assert len(_plaetze(db)) == 225, "Plätze verkleinerter Ordner bleiben"
