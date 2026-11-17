"""System → Bestand prüfen: die Folgen alter Fehler in vorhandenen Daten.

Die Ursachen für „mehr im System als im Regal" sind behoben. Was sie in den
vorhandenen Daten hinterlassen haben, zeigt diese Seite — und bereinigt es nur
auf ausdrücklichen Klick. Dazu kommt das Aufräumen der Lagerplätze beim Start.
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
from TCGInventory import (auth, bestandspruefung,                 # noqa: E402
                          build_card_db as bcd, lager_manager,
                          order_service, setup_db, web)
import TCGInventory.card_scanner as cs                            # noqa: E402

SPOCK_ID = "00000137-aaaa-bbbb-cccc-dddddddddddd"


@pytest.fixture()
def lager(tmp_path, monkeypatch):
    pfad = str(tmp_path / "lager.db")
    for modul in (TCGInventory, web, auth, setup_db, order_service, lager_manager):
        monkeypatch.setattr(modul, "DB_FILE", pfad)
    setup_db.initialize_database()
    karten_db = tmp_path / "default-cards.db"
    bcd.schreibe_datenbank([
        {"id": SPOCK_ID, "name": "Spock", "set": "trk", "set_name": "Star Trek",
         "lang": "en", "collector_number": "137", "cardmarket_id": 1},
    ], karten_db)
    cs.reset_card_database()
    monkeypatch.setattr(cs, "DEFAULT_DB_PATH", karten_db)
    fid = lager_manager.add_folder("Star Trek", 2)
    lager_manager.create_binder(fid, 2)
    yield pfad, fid
    cs.reset_card_database()


def _klient():
    web.app.config["TESTING"] = True
    klient = web.app.test_client()
    with klient.session_transaction() as s:
        s["user"] = "melvin"
    return klient


def _q(pfad, sql, *werte):
    with sqlite3.connect(pfad) as conn:
        return conn.execute(sql, werte).fetchall()


def _karte(ordner, name="Spock", set_code="trk", nummer="137", menge=3,
           scryfall_id=SPOCK_ID):
    lager_manager.add_card(name, set_code, "en", "NM", 1.0, menge, folder_id=ordner,
                           collector_number=nummer, scryfall_id=scryfall_id)
    return _q(web.DB_FILE, "SELECT MAX(id) FROM cards")[0][0]


def _verkauft(pfad, name, menge, card_id=None, abgezogen=None, status="sold",
              nummer="4711"):
    with sqlite3.connect(pfad) as conn:
        bid = conn.execute(
            "INSERT INTO orders (buyer_name, email_message_id, date_received, "
            "status, order_number) VALUES ('Kaeufer', ?, '2026-11-01', ?, ?)",
            (f"m-{nummer}-{name}-{menge}-{status}", status, nummer)).lastrowid
        item = conn.execute(
            "INSERT INTO order_items (order_id, card_name, quantity, card_id, "
            "abgezogen) VALUES (?, ?, ?, ?, ?)",
            (bid, name, menge, card_id, abgezogen)).lastrowid
        conn.commit()
    return item


def _menge(pfad, kid):
    zeile = _q(pfad, "SELECT quantity FROM cards WHERE id = ?", kid)
    return zeile[0][0] if zeile else None


# ---------------------------------------------------------------------------
# Verkauft, aber nicht abgezogen
# ---------------------------------------------------------------------------
def test_nicht_zugeordnete_verkaufte_position_nachtraeglich_ausbuchen(lager):
    pfad, ordner = lager
    kid = _karte(ordner, menge=3)
    item = _verkauft(pfad, "Spock", 2)
    klient = _klient()

    html = klient.get("/system/bestand-pruefen").get_data(as_text=True)
    assert "2× Spock" in html and "Bestellung 4711" in html
    assert f'name="card_id" value="{kid}"' in html

    html = klient.post(f"/system/bestand-pruefen/position/{item}/ausbuchen",
                       data={"card_id": str(kid)},
                       follow_redirects=True).get_data(as_text=True)
    assert "2 Exemplar(e) nachträglich abgezogen" in html
    assert _menge(pfad, kid) == 1
    assert _q(pfad, "SELECT card_id, abgezogen, bestand_geklaert FROM order_items "
                    "WHERE id = ?", item) == [(kid, 2, 1)]
    assert "2× Spock" not in klient.get("/system/bestand-pruefen").get_data(as_text=True)
    assert _q(pfad, "SELECT COUNT(*) FROM audit_log WHERE action = 'nachtrag-verkauf'") == [(1,)]


def test_teilweise_abgezogen_rest_ausbuchen(lager):
    pfad, ordner = lager
    kid = _karte(ordner, menge=5)
    item = _verkauft(pfad, "Spock", 3, card_id=kid, abgezogen=1)
    html = _klient().get("/system/bestand-pruefen").get_data(as_text=True)
    assert "2× Spock" in html and "(1 von 3 schon abgezogen)" in html
    assert "Rest ausbuchen" in html

    _klient().post(f"/system/bestand-pruefen/position/{item}/ausbuchen",
                   data={"card_id": str(kid)})
    assert _menge(pfad, kid) == 3
    assert _q(pfad, "SELECT abgezogen, bestand_geklaert FROM order_items "
                    "WHERE id = ?", item) == [(3, 1)]


def test_reicht_der_bestand_nicht_bleibt_der_rest_stehen(lager):
    pfad, ordner = lager
    kid = _karte(ordner, menge=1)
    item = _verkauft(pfad, "Spock", 3)
    html = _klient().post(f"/system/bestand-pruefen/position/{item}/ausbuchen",
                          data={"card_id": str(kid)},
                          follow_redirects=True).get_data(as_text=True)
    assert "2 konnte(n) nicht abgezogen werden" in html
    assert _menge(pfad, kid) is None                  # letztes Exemplar weg
    assert _q(pfad, "SELECT abgezogen, bestand_geklaert FROM order_items") == [(1, 0)]


def test_andere_karte_als_zugeordnet_wird_abgelehnt(lager):
    pfad, ordner = lager
    zugeordnet = _karte(ordner, menge=2)
    andere = _karte(ordner, name="Kirk", nummer="1", menge=2, scryfall_id="")
    item = _verkauft(pfad, "Spock", 2, card_id=zugeordnet, abgezogen=1)
    html = _klient().post(f"/system/bestand-pruefen/position/{item}/ausbuchen",
                          data={"card_id": str(andere)},
                          follow_redirects=True).get_data(as_text=True)
    assert "eine andere Karte zugeordnet" in html
    assert _menge(pfad, andere) == 2


def test_erledigt_laesst_den_bestand(lager):
    pfad, ordner = lager
    kid = _karte(ordner, menge=3)
    item = _verkauft(pfad, "Spock", 1)
    _klient().post(f"/system/bestand-pruefen/position/{item}/erledigt")
    assert _menge(pfad, kid) == 3
    assert _q(pfad, "SELECT bestand_geklaert FROM order_items") == [(1,)]
    assert _q(pfad, "SELECT COUNT(*) FROM audit_log WHERE action = 'bestand-geklaert'") == [(1,)]
    # Ein zweiter Klick ändert nichts mehr.
    html = _klient().post(f"/system/bestand-pruefen/position/{item}/erledigt",
                          follow_redirects=True).get_data(as_text=True)
    assert "bereits alles erledigt" in html


def test_alte_abgeschlossene_positionen_mit_karte_werden_nicht_geraten(lager):
    """Vor dem Festhalten der Abzugsmenge ist nicht mehr feststellbar, ob
    abgezogen wurde — solche Positionen stehen nicht in der Liste."""
    pfad, ordner = lager
    kid = _karte(ordner)
    _verkauft(pfad, "Spock", 1, card_id=kid, abgezogen=None)
    with sqlite3.connect(pfad) as conn:
        assert bestandspruefung.nicht_abgezogen(conn) == []


def test_offene_bestellung_wird_hier_nicht_ausgebucht(lager):
    pfad, ordner = lager
    kid = _karte(ordner)
    item = _verkauft(pfad, "Spock", 1, status="open")
    html = _klient().post(f"/system/bestand-pruefen/position/{item}/ausbuchen",
                          data={"card_id": str(kid)},
                          follow_redirects=True).get_data(as_text=True)
    assert "noch offen" in html
    assert _menge(pfad, kid) == 3


# ---------------------------------------------------------------------------
# Set-Code
# ---------------------------------------------------------------------------
def test_set_code_aus_der_scryfall_id_wiederherstellen(lager):
    pfad, ordner = lager
    kid = _karte(ordner, set_code="Star Trek")
    klient = _klient()
    html = klient.get("/system/bestand-pruefen").get_data(as_text=True)
    assert "Set-Code ist der Name des Ordners" in html
    assert "<code>trk</code>" in html

    klient.post(f"/system/bestand-pruefen/karte/{kid}/set-code")
    assert _q(pfad, "SELECT set_code FROM cards WHERE id = ?", kid) == [("trk",)]
    assert _q(pfad, "SELECT old_value, new_value FROM audit_log "
                    "WHERE field_name = 'set_code'") == [("Star Trek", "trk")]


def test_alle_set_codes_auf_einmal(lager):
    pfad, ordner = lager
    _karte(ordner, set_code="Star Trek")
    _karte(ordner, set_code="")
    ohne_id = _karte(ordner, name="Kirk", nummer="1", set_code="Star Trek", scryfall_id="")
    html = _klient().post("/system/bestand-pruefen/set-codes",
                          follow_redirects=True).get_data(as_text=True)
    assert "2 Set-Code(s) wiederhergestellt" in html
    assert _q(pfad, "SELECT set_code FROM cards ORDER BY id") == [
        ("trk",), ("trk",), ("Star Trek",)]
    # Ohne Scryfall-ID bleibt nur „Bearbeiten".
    assert f"/cards/{ohne_id}/edit" in html
    assert "nicht sicher bestimmbar" in html


def test_widerspruechliche_nummer_wird_nicht_korrigiert(lager):
    pfad, ordner = lager
    kid = _karte(ordner, set_code="Star Trek", nummer="99")
    with sqlite3.connect(pfad) as conn:
        eintrag = bestandspruefung.falscher_set_code(conn)[0]
        assert eintrag["korrektur"] is None
        with pytest.raises(bestandspruefung.PruefFehler):
            bestandspruefung.set_code_wiederherstellen(conn, kid, "melvin")


def test_nach_der_korrektur_findet_die_bestellung_die_karte(lager, monkeypatch):
    pfad, ordner = lager
    kid = _karte(ordner, set_code="Star Trek")
    monkeypatch.setattr(order_service, "resolve_set_code", lambda n: ("trk", "high"))
    service = order_service.OrderIngestionService()
    position = {"name": "Spock", "set_name": "Star Trek", "language": "en"}
    with sqlite3.connect(pfad) as conn:
        assert service._match_item(conn.cursor(), position)["card_id"] is None
    _klient().post(f"/system/bestand-pruefen/karte/{kid}/set-code")
    with sqlite3.connect(pfad) as conn:
        assert service._match_item(conn.cursor(), position)["card_id"] == kid


# ---------------------------------------------------------------------------
# Doppelte Einträge, Plätze
# ---------------------------------------------------------------------------
def test_doppelte_eintraege_werden_gezeigt_nicht_zusammengelegt(lager):
    pfad, ordner = lager
    _karte(ordner, set_code="TRK", menge=2)
    _karte(ordner, set_code="trk", menge=1)
    anderer = lager_manager.add_folder("Zweitordner", 1)
    lager_manager.create_binder(anderer, 1)
    _karte(anderer, menge=1)                           # anderer Ordner: kein Doppel
    html = _klient().get("/system/bestand-pruefen").get_data(as_text=True)
    with sqlite3.connect(pfad) as conn:
        gruppen = bestandspruefung.doppelte_eintraege(conn)
    assert len(gruppen) == 1 and len(gruppen[0]) == 2
    assert "Dieselbe Karte mehrfach im selben Ordner <span class=\"badge bg-secondary\">1" in html
    assert _q(pfad, "SELECT COUNT(*) FROM cards") == [(3,)]


def test_karte_auf_platz_den_es_nicht_gibt(lager):
    pfad, ordner = lager
    with sqlite3.connect(pfad) as conn:
        conn.execute("INSERT INTO cards (name, set_code, quantity, storage_code, "
                     "folder_id) VALUES ('Data', 'trk', 1, 'O01-S01-P14', ?)", (ordner,))
        conn.commit()
    html = _klient().get("/system/bestand-pruefen").get_data(as_text=True)
    assert "O01-S01-P14" in html
    assert "Karten auf Plätzen, die es nicht gibt <span class=\"badge bg-secondary\">1" in html


def test_alles_in_ordnung(lager):
    pfad, ordner = lager
    _karte(ordner)
    html = _klient().get("/system/bestand-pruefen").get_data(as_text=True)
    assert 'id="alles-in-ordnung"' in html
    assert "/system/bestand-pruefen" in _klient().get("/cards").get_data(as_text=True)


# ---------------------------------------------------------------------------
# Lagerplätze beim Start
# ---------------------------------------------------------------------------
def test_start_entfernt_freie_plaetze_ab_p10_und_gleicht_belegung_an(lager):
    pfad, ordner = lager
    kid = _karte(ordner)                               # O01-S01-P1
    with sqlite3.connect(pfad) as conn:
        conn.executemany("INSERT INTO storage_slots (code, is_occupied) VALUES (?, 0)",
                         [(f"O01-S01-P{n}",) for n in range(10, 19)])
        # Eine Karte liegt auf P12 (aus der 18er-Zeit), ihr Platz gilt als frei.
        conn.execute("INSERT INTO cards (name, set_code, quantity, storage_code, "
                     "folder_id) VALUES ('Data', 'trk', 1, 'O01-S01-P12', ?)", (ordner,))
        # Belegung auseinandergelaufen: P5 belegt ohne Karte, P1 frei mit Karte.
        conn.execute("UPDATE storage_slots SET is_occupied = 1 WHERE code = 'O01-S01-P5'")
        conn.execute("UPDATE storage_slots SET is_occupied = 0 WHERE code = 'O01-S01-P1'")
        conn.commit()

    setup_db.initialize_database()

    ab_p10 = [z[0] for z in _q(pfad, "SELECT code FROM storage_slots WHERE code GLOB '*-P1[0-9]'")]
    assert ab_p10 == ["O01-S01-P12"]                   # nur der mit Karte bleibt
    belegt = sorted(z[0] for z in _q(pfad, "SELECT code FROM storage_slots WHERE is_occupied = 1"))
    assert belegt == ["O01-S01-P1", "O01-S01-P12"]
    assert _menge(pfad, kid) == 3


def test_naechster_freier_platz_ueberspringt_belegte_und_p10(lager):
    pfad, ordner = lager
    with sqlite3.connect(pfad) as conn:
        conn.execute("INSERT INTO storage_slots (code, is_occupied) VALUES ('O01-S01-P10', 0)")
        # Auf P1 liegt eine Karte, obwohl der Platz als frei gilt.
        conn.execute("INSERT INTO cards (name, set_code, quantity, storage_code, "
                     "folder_id) VALUES ('Data', 'trk', 1, 'O01-S01-P1', ?)", (ordner,))
        conn.commit()
    assert lager_manager.get_next_free_slot("O01-") == "O01-S01-P2"
