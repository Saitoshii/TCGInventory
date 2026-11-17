"""Karte anlegen, bearbeiten, umziehen, entfernen — ohne Nebenwirkungen.

Was bei der Prüfung auffiel:

* Beim Speichern einer Karte wurde ihr Set-Code durch den **Namen des
  Ordners** ersetzt („trk" → „Star Trek"). Wer bei der Inventur nur die Menge
  berichtigte, machte die Karte damit für den Bestelleingang unauffindbar;
  beim nächsten Import entstand eine zweite Zeile für dieselbe Karte.
* Zog eine Karte auf einen anderen Platz um, blieb der alte Platz „belegt"
  und der neue galt als frei — die nächste Karte landete auf demselben Platz.
* Löschen ging per Link (ein wiederhergestellter Tab genügte) und
  hinterließ keine Spur.
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
def lager(tmp_path, monkeypatch):
    pfad = str(tmp_path / "lager.db")
    for modul in (TCGInventory, web, auth, setup_db, order_service, lager_manager):
        monkeypatch.setattr(modul, "DB_FILE", pfad)
    setup_db.initialize_database()
    fid = lager_manager.add_folder("Star Trek", 2)
    lager_manager.create_binder(fid, 2)
    return pfad, fid


def _klient():
    web.app.config["TESTING"] = True
    klient = web.app.test_client()
    with klient.session_transaction() as s:
        s["user"] = "melvin"
    return klient


def _q(pfad, sql, *werte):
    with sqlite3.connect(pfad) as conn:
        return conn.execute(sql, werte).fetchall()


def _spock(ordner, menge=1):
    lager_manager.add_or_increment_card("Spock", "trk", "en", "NM", 1.0, menge,
                                        None, "123", ordner, "137", "sf-137",
                                        "", False)
    return _q(web.DB_FILE, "SELECT id FROM cards WHERE name = 'Spock'")[0][0]


def _formular(ordner, **anders):
    daten = {"name": "Spock", "language": "en", "condition": "NM",
             "price": "1", "quantity": "1", "folder_id": str(ordner),
             "page": "1", "slot": "1", "collector_number": "137",
             "scryfall_id": "sf-137", "cardmarket_id": "123",
             "item_type": "card"}
    daten.update(anders)
    return daten


# ---------------------------------------------------------------------------
# Set-Code
# ---------------------------------------------------------------------------
def test_bearbeiten_laesst_den_set_code_stehen(lager):
    """Ein Formular ohne Set-Code-Feld (ältere Seite) ändert ihn nicht."""
    pfad, ordner = lager
    kid = _spock(ordner)
    _klient().post(f"/cards/{kid}/edit", data=_formular(ordner, quantity="3"))
    assert _q(pfad, "SELECT set_code, quantity FROM cards") == [("trk", 3)]


def test_bearbeiten_mit_set_code_feld(lager):
    pfad, ordner = lager
    kid = _spock(ordner)
    html = _klient().get(f"/cards/{kid}/edit").get_data(as_text=True)
    assert 'name="set_code"' in html and 'value="trk"' in html
    _klient().post(f"/cards/{kid}/edit", data=_formular(ordner, set_code=" TRK "))
    assert _q(pfad, "SELECT set_code FROM cards") == [("trk",)]


def test_bestellung_findet_die_karte_nach_dem_bearbeiten(lager, monkeypatch):
    """Der eigentliche Schaden: nach dem Speichern fand der Bestelleingang
    die Karte nicht mehr."""
    pfad, ordner = lager
    kid = _spock(ordner)
    _klient().post(f"/cards/{kid}/edit", data=_formular(ordner, quantity="2"))
    # Der Set-Name aus der Mail löst sich (über die Kartendaten) zu „trk" auf.
    monkeypatch.setattr(order_service, "resolve_set_code",
                        lambda name: ("trk", "high"))
    service = order_service.OrderIngestionService()
    with sqlite3.connect(pfad) as conn:
        treffer = service._match_item(conn.cursor(), {
            "name": "Spock", "set_name": "Star Trek", "language": "en"})
    assert treffer["match_status"] == "matched"
    assert treffer["card_id"] == kid


def test_anlegen_nimmt_den_set_code_der_karte_nicht_den_ordnernamen(lager):
    pfad, ordner = lager
    _klient().post("/cards/add", data=_formular(ordner, set_code="trk", page="", slot=""))
    assert _q(pfad, "SELECT set_code FROM cards") == [("trk",)]


def test_anlegen_ohne_set_code_warnt(lager):
    pfad, ordner = lager
    html = _klient().post("/cards/add", data=_formular(ordner, page="", slot=""),
                          follow_redirects=True).get_data(as_text=True)
    assert _q(pfad, "SELECT set_code FROM cards") == [("",)]
    assert "Ohne Set-Code kann eine Bestellung diese Karte nicht" in html


def test_ausgabe_wird_nicht_still_vorbelegt(lager):
    html = _klient().get("/cards/add").get_data(as_text=True)
    assert "bitte die Ausgabe wählen" in html
    assert "fillFields(list[0]);\n        }\n      });" not in html


# ---------------------------------------------------------------------------
# Lagerplatz
# ---------------------------------------------------------------------------
def test_umzug_gibt_den_alten_platz_frei(lager):
    pfad, ordner = lager
    kid = _spock(ordner)
    assert _q(pfad, "SELECT storage_code FROM cards") == [("O01-S01-P1",)]
    _klient().post(f"/cards/{kid}/edit", data=_formular(ordner, page="2", slot="5"))

    belegt = [z[0] for z in _q(pfad, "SELECT code FROM storage_slots WHERE is_occupied = 1")]
    assert belegt == ["O01-S02-P5"]
    # Die nächste Karte bekommt den frei gewordenen Platz, nicht O01-S02-P5.
    lager_manager.add_card("Kirk", "trk", "en", "NM", 1.0, 1, folder_id=ordner,
                           collector_number="1")
    assert _q(pfad, "SELECT storage_code FROM cards WHERE name = 'Kirk'") == [("O01-S01-P1",)]


def test_platz_den_es_nicht_gibt_wird_abgelehnt(lager):
    pfad, ordner = lager
    kid = _spock(ordner)
    html = _klient().post(f"/cards/{kid}/edit", data=_formular(ordner, slot="12", quantity="5"),
                          follow_redirects=True).get_data(as_text=True)
    assert "Den Lagerplatz O01-S01-P12 gibt es nicht" in html
    assert _q(pfad, "SELECT storage_code, quantity FROM cards") == [("O01-S01-P1", 1)]


def test_unveraenderter_platz_bleibt(lager):
    pfad, ordner = lager
    kid = _spock(ordner)
    _klient().post(f"/cards/{kid}/edit", data=_formular(ordner, page="01", slot="1"))
    assert _q(pfad, "SELECT storage_code FROM cards") == [("O01-S01-P1",)]
    assert _q(pfad, "SELECT code FROM storage_slots WHERE is_occupied = 1") == [("O01-S01-P1",)]


def test_seite_und_platz_leeren_gibt_den_platz_frei(lager):
    pfad, ordner = lager
    kid = _spock(ordner)
    _klient().post(f"/cards/{kid}/edit", data=_formular(ordner, page="", slot=""))
    assert _q(pfad, "SELECT storage_code FROM cards") == [("",)]
    assert _q(pfad, "SELECT COUNT(*) FROM storage_slots WHERE is_occupied = 1") == [(0,)]


def test_karte_gibt_es_nicht_mehr(lager):
    html = _klient().get("/cards/999/edit", follow_redirects=True).get_data(as_text=True)
    assert "Diesen Artikel gibt es nicht mehr" in html


# ---------------------------------------------------------------------------
# Entfernen und Ordner löschen
# ---------------------------------------------------------------------------
def test_entfernen_nur_per_formular_und_mit_spur(lager):
    pfad, ordner = lager
    kid = _spock(ordner, menge=3)
    klient = _klient()
    assert klient.get(f"/cards/{kid}/delete").status_code == 405
    assert _q(pfad, "SELECT COUNT(*) FROM cards") == [(1,)]

    html = klient.post(f"/cards/{kid}/delete", follow_redirects=True).get_data(as_text=True)
    assert "„Spock“ (3 Stück) aus dem Bestand entfernt" in html
    assert _q(pfad, "SELECT COUNT(*) FROM cards") == [(0,)]
    assert _q(pfad, "SELECT COUNT(*) FROM storage_slots WHERE is_occupied = 1") == [(0,)]
    assert _q(pfad, "SELECT user, action, old_value FROM audit_log "
                    "WHERE action = 'delete'") == [("melvin", "delete", "Spock (Menge 3)")]


def test_entfernen_knopf_am_artikel_mit_sicherer_rueckfrage(lager):
    pfad, ordner = lager
    lager_manager.add_card("Urza's Saga", "mh2", "en", "NM", 1.0, 1,
                           folder_id=ordner, collector_number="259")
    kid = _q(pfad, "SELECT id FROM cards")[0][0]
    html = _klient().get(f"/cards/{kid}/edit").get_data(as_text=True)
    assert f'action="/cards/{kid}/delete"' in html
    # Der Name steht maskiert im Datenattribut, nicht im Skript.
    assert "data-frage=\"„Urza&#39;s Saga“" in html
    assert "onsubmit=\"return confirm(this.dataset.frage)\"" in html


def test_ordner_loeschen_nur_per_formular(lager):
    pfad, ordner = lager
    _spock(ordner, menge=2)
    klient = _klient()
    assert klient.get(f"/folders/delete/{ordner}").status_code == 405

    html = klient.get(f"/folders/edit/{ordner}").get_data(as_text=True)
    assert "entfernt auch 1 Einträge mit 2 Karten" in html
    assert "data-frage=\"Ordner „Star Trek“ wirklich löschen?" in html

    html = klient.post(f"/folders/delete/{ordner}", follow_redirects=True).get_data(as_text=True)
    assert "Ordner „Star Trek“ gelöscht – 1 Einträge mit 2 Karten" in html
    assert _q(pfad, "SELECT COUNT(*) FROM folders") == [(0,)]
    aktionen = sorted(z[0] for z in _q(pfad, "SELECT action FROM audit_log "
                                             "WHERE action IN ('delete', 'ordner-geloescht')"))
    assert aktionen == ["delete", "ordner-geloescht"]


def test_warteschlange_bearbeiten_traegt_die_kennung(lager):
    web.UPLOAD_QUEUE.clear()
    web.UPLOAD_QUEUE.append(web._mit_kennung({"name": "Spock", "set_code": "trk"}))
    uid = web.UPLOAD_QUEUE[0]["uid"]
    try:
        html = _klient().get(f"/cards/upload_queue/edit/0?uid={uid}").get_data(as_text=True)
        assert f'name="uid" value="{uid}"' in html
        assert "Aus dem Bestand entfernen" not in html
    finally:
        web.UPLOAD_QUEUE.clear()
