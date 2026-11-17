"""Ordner-Ansicht und Inventur: jede Seite wie im Ordner, Karte für Karte prüfen."""

import os
import re
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
                          ordneransicht, setup_db, web)


@pytest.fixture()
def lager(tmp_path, monkeypatch):
    pfad = str(tmp_path / "lager.db")
    for modul in (TCGInventory, web, auth, setup_db, order_service, lager_manager):
        monkeypatch.setattr(modul, "DB_FILE", pfad)
    setup_db.initialize_database()
    fid = lager_manager.add_folder("Star Trek", 3)
    lager_manager.create_binder(fid, 3)
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


def _karte(ordner, name, platz, menge=1, nummer="1", foil=False, bild="https://bild/x.jpg"):
    lager_manager.add_card(name, "trk", "en", "NM", 1.0, menge, storage_code=platz,
                           folder_id=ordner, collector_number=nummer,
                           image_url=bild, foil=foil)
    return _q(web.DB_FILE, "SELECT MAX(id) FROM cards")[0][0]


def _faecher(html):
    """Die Fächer der Seite in Reihenfolge, je mit den Kartennamen darin."""
    raster = html.split('id="seite-raster"')[1].split("</div>\n    \n")[0]
    faecher = re.findall(r'id="(O\d+-S\d+-P\d+)"(.*?)(?=<div class="binder-fach|\Z)', raster, re.S)
    return [(code, re.findall(r'class="binder-karte__name"[^>]*>([^<]+)<', inhalt))
            for code, inhalt in faecher]


# ---------------------------------------------------------------------------
# Anzeige
# ---------------------------------------------------------------------------
def test_seite_zeigt_neun_faecher_in_ordnerreihenfolge(lager):
    _, ordner = lager
    _karte(ordner, "Spock", "O01-S01-P1")
    _karte(ordner, "Kirk", "O01-S01-P5", nummer="2")
    _karte(ordner, "Data", "O01-S01-P5", nummer="3")       # zwei Karten in einem Fach
    _karte(ordner, "Worf", "O01-S02-P9", nummer="4")        # andere Seite
    html = _klient().get(f"/ordner/{ordner}").get_data(as_text=True)

    faecher = _faecher(html)
    assert [code for code, _ in faecher] == [f"O01-S01-P{n}" for n in range(1, 10)]
    assert faecher[0][1] == ["Spock"]
    assert sorted(faecher[4][1]) == ["Data", "Kirk"]
    assert all(not namen for i, (_, namen) in enumerate(faecher) if i not in (0, 4))
    assert "Worf" not in html.split('id="seite-raster"')[1].split('id="seiten-leiste"')[0]
    assert html.count("binder-fach--leer") == 7
    assert "Seite 1 von 3" in html


def test_blaettern_und_ungueltige_seitenangaben(lager):
    _, ordner = lager
    _karte(ordner, "Worf", "O01-S02-P9")
    klient = _klient()
    assert "Seite 2 von 3" in klient.get(f"/ordner/{ordner}?seite=2").get_data(as_text=True)
    assert "Seite 3 von 3" in klient.get(f"/ordner/{ordner}?seite=99").get_data(as_text=True)
    assert "Seite 1 von 3" in klient.get(f"/ordner/{ordner}?seite=abc").get_data(as_text=True)


def test_karte_hinter_der_letzten_seite_verlaengert_den_ordner(lager):
    _, ordner = lager
    with sqlite3.connect(web.DB_FILE) as conn:
        conn.execute("INSERT INTO cards (name, set_code, quantity, storage_code, folder_id, "
                     "item_type) VALUES ('Q', 'trk', 1, 'O01-S05-P2', ?, 'card')", (ordner,))
        conn.commit()
    html = _klient().get(f"/ordner/{ordner}?seite=5").get_data(as_text=True)
    assert "Seite 5 von 5" in html and ">Q<" in html


def test_ohne_platz_und_plaetze_die_es_nicht_gibt(lager):
    pfad, ordner = lager
    with sqlite3.connect(pfad) as conn:
        conn.execute("INSERT INTO cards (name, set_code, quantity, storage_code, folder_id, "
                     "item_type) VALUES ('Ohne', 'trk', 1, '', ?, 'card')", (ordner,))
        conn.execute("INSERT INTO cards (name, set_code, quantity, storage_code, folder_id, "
                     "item_type) VALUES ('Zwoelf', 'trk', 1, 'O01-S01-P12', ?, 'card')", (ordner,))
        conn.commit()
    html = _klient().get(f"/ordner/{ordner}").get_data(as_text=True)
    assert "Ohne Platz in diesem Ordner" in html and "Ohne" in html
    assert "Auf Plätzen, die es nicht gibt" in html and "O01-S01-P12" in html


def test_seitenleiste_zeigt_belegte_faecher(lager):
    _, ordner = lager
    _karte(ordner, "Spock", "O01-S02-P1")
    _karte(ordner, "Kirk", "O01-S02-P9", nummer="2")
    with sqlite3.connect(web.DB_FILE) as conn:
        seiten = ordneransicht.uebersicht(conn, ordner)
    assert [s["belegte_plaetze"] for s in seiten] == [[], [1, 9], []]


def test_suche_fuehrt_zum_fach(lager):
    _, ordner = lager
    _karte(ordner, "Spock, Logical Officer", "O01-S03-P7")
    html = _klient().get(f"/ordner/{ordner}?q=spock").get_data(as_text=True)
    assert "Seite 3, Platz 7" in html
    assert "seite=3" in html and "#O01-S03-P7" in html


def test_unbekannter_ordner(lager):
    html = _klient().get("/ordner/99", follow_redirects=True).get_data(as_text=True)
    assert "Diesen Ordner gibt es nicht" in html


def test_nur_mit_anmeldung(lager):
    _, ordner = lager
    web.app.config["TESTING"] = True
    assert web.app.test_client().get(f"/ordner/{ordner}").status_code == 302


# ---------------------------------------------------------------------------
# Inventur
# ---------------------------------------------------------------------------
def _aktion(ordner, kid, aktion, seite=1, platz="O01-S01-P1"):
    return _klient().post(f"/ordner/{ordner}/karte/{kid}/{aktion}",
                          data={"seite": str(seite), "platz": platz})


def test_menge_berichtigen_mit_protokoll(lager):
    pfad, ordner = lager
    kid = _karte(ordner, "Spock", "O01-S01-P1", menge=2)
    antwort = _aktion(ordner, kid, "plus")
    assert antwort.headers["Location"].endswith(
        f"/ordner/{ordner}?seite=1&inventur=1#O01-S01-P1")
    _aktion(ordner, kid, "plus")
    _aktion(ordner, kid, "minus")
    assert _q(pfad, "SELECT quantity FROM cards") == [(3,)]
    assert _q(pfad, "SELECT old_value, new_value FROM audit_log WHERE action = 'inventur' "
                    "ORDER BY id") == [("2", "3"), ("3", "4"), ("4", "3")]
    assert _q(pfad, "SELECT gezaehlt_am IS NOT NULL FROM cards") == [(1,)]


def test_minus_geht_nicht_unter_eins(lager):
    pfad, ordner = lager
    kid = _karte(ordner, "Spock", "O01-S01-P1", menge=1)
    _aktion(ordner, kid, "minus")
    assert _q(pfad, "SELECT quantity FROM cards") == [(1,)]
    html = _klient().get(f"/ordner/{ordner}?inventur=1").get_data(as_text=True)
    minus = html.split('title="Eine weniger"')[1].split(">")[0]
    assert "disabled" in minus


def test_stimmt_markiert_als_gezaehlt(lager):
    pfad, ordner = lager
    kid = _karte(ordner, "Spock", "O01-S01-P1", menge=2)
    _aktion(ordner, kid, "stimmt")
    assert _q(pfad, "SELECT quantity, gezaehlt_am IS NOT NULL FROM cards") == [(2, 1)]
    html = _klient().get(f"/ordner/{ordner}?inventur=1").get_data(as_text=True)
    assert "binder-karte--gezaehlt" in html
    assert "<strong>1</strong> von 1 Karten gezählt" in html


def test_fehlt_entfernt_die_karte_und_gibt_den_platz_frei(lager):
    pfad, ordner = lager
    kid = _karte(ordner, "Spock", "O01-S01-P1", menge=2)
    html = _klient().post(f"/ordner/{ordner}/karte/{kid}/fehlt",
                          data={"seite": "1", "platz": "O01-S01-P1"},
                          follow_redirects=True).get_data(as_text=True)
    assert "„Spock“ (2 Stück) fehlt und wurde aus dem Bestand entfernt" in html
    assert _q(pfad, "SELECT COUNT(*) FROM cards") == [(0,)]
    assert _q(pfad, "SELECT is_occupied FROM storage_slots WHERE code = 'O01-S01-P1'") == [(0,)]
    aktionen = [z[0] for z in _q(pfad, "SELECT action FROM audit_log ORDER BY id")]
    assert "inventur-fehlt" in aktionen and "delete" in aktionen


def test_karte_aus_anderem_ordner_wird_nicht_angefasst(lager):
    pfad, ordner = lager
    anderer = lager_manager.add_folder("Zweitordner", 1)
    lager_manager.create_binder(anderer, 1)
    fremd = _karte(anderer, "Fremd", "O02-S01-P1", menge=2)
    html = _klient().post(f"/ordner/{ordner}/karte/{fremd}/fehlt",
                          data={"seite": "1"}, follow_redirects=True).get_data(as_text=True)
    assert "liegt nicht (mehr) in diesem Ordner" in html
    assert _q(pfad, "SELECT quantity FROM cards") == [(2,)]


def test_unbekannte_aktion(lager):
    pfad, ordner = lager
    kid = _karte(ordner, "Spock", "O01-S01-P1", menge=2)
    html = _klient().post(f"/ordner/{ordner}/karte/{kid}/loeschen",
                          data={"seite": "1"}, follow_redirects=True).get_data(as_text=True)
    assert "Unbekannte Aktion" in html
    assert _q(pfad, "SELECT quantity FROM cards") == [(2,)]


def test_seite_stimmt_zaehlt_alles_und_blaettert_weiter(lager):
    pfad, ordner = lager
    _karte(ordner, "Spock", "O01-S01-P1")
    _karte(ordner, "Kirk", "O01-S01-P2", nummer="2")
    _karte(ordner, "Worf", "O01-S02-P1", nummer="3")
    antwort = _klient().post(f"/ordner/{ordner}/seite/1/geprueft")
    assert "seite=2" in antwort.headers["Location"]
    assert _q(pfad, "SELECT name FROM cards WHERE gezaehlt_am IS NOT NULL ORDER BY name") == [
        ("Kirk",), ("Spock",)]
    assert _q(pfad, "SELECT ordner_id, seite FROM inventur_seiten") == [(ordner, 1)]

    html = _klient().get(f"/ordner/{ordner}?seite=2&inventur=1").get_data(as_text=True)
    assert "<strong>1</strong> von 3 Seiten geprüft" in html
    assert "<strong>2</strong> von 3 Karten gezählt" in html
    # Letzte Seite: bleibt dort.
    antwort = _klient().post(f"/ordner/{ordner}/seite/3/geprueft")
    assert "seite=3" in antwort.headers["Location"]


def test_neue_inventur_beginnt_beim_fortschritt_bei_null(lager):
    pfad, ordner = lager
    kid = _karte(ordner, "Spock", "O01-S01-P1", menge=2)
    _klient().post(f"/ordner/{ordner}/seite/1/geprueft")
    with sqlite3.connect(pfad) as conn:
        # Die Prüfung liegt zeitlich vor dem neuen Beginn.
        conn.execute("UPDATE inventur_seiten SET geprueft_am = '2026-01-01T00:00:00'")
        conn.execute("UPDATE cards SET gezaehlt_am = '2026-01-01T00:00:00'")
        conn.commit()
    html = _klient().post(f"/ordner/{ordner}/inventur/neu",
                          follow_redirects=True).get_data(as_text=True)
    assert "Neue Inventur begonnen" in html
    assert "<strong>0</strong> von 3 Seiten geprüft" in html
    assert "<strong>0</strong> von 1 Karten gezählt" in html
    assert _q(pfad, "SELECT quantity FROM cards WHERE id = ?", kid) == [(2,)]


# ---------------------------------------------------------------------------
# Wege in die Ansicht
# ---------------------------------------------------------------------------
def test_lagerplatz_wird_zum_verweis(lager):
    pfad, ordner = lager
    kid = _karte(ordner, "Spock", "O01-S03-P4")
    with sqlite3.connect(pfad) as conn:
        bid = conn.execute(
            "INSERT INTO orders (buyer_name, email_message_id, date_received, status) "
            "VALUES ('K', 'm', datetime('now'), 'open')").lastrowid
        conn.execute("INSERT INTO order_items (order_id, card_name, quantity, card_id, "
                     "match_status, storage_code) VALUES (?, 'Spock', 1, ?, 'matched', "
                     "'O01-S03-P4')", (bid, kid))
        conn.commit()
    ziel = f'href="/ordner/{ordner}?seite=3#O01-S03-P4"'
    assert ziel in _klient().get("/orders").get_data(as_text=True)
    assert ziel in _klient().get("/cards").get_data(as_text=True)
    assert ziel in _klient().get("/folders").get_data(as_text=True)


def test_anderer_lagerort_bleibt_text(lager):
    with web.app.test_request_context():
        assert web.platz_url_filter("Regal A, Box 3") == ""
        assert web.platz_url_filter(None) == ""
        assert web.platz_url_filter("O07-S12-P4") == "/ordner/7?seite=12#O07-S12-P4"


def test_ordnerliste_hat_ansehen_und_inventur(lager):
    _, ordner = lager
    html = _klient().get("/folders").get_data(as_text=True)
    assert f'href="/ordner/{ordner}"' in html
    assert f'href="/ordner/{ordner}?inventur=1"' in html


def test_karte_im_leeren_fach_anlegen(lager):
    pfad, ordner = lager
    klient = _klient()
    html = klient.get(f"/ordner/{ordner}?seite=2&inventur=1").get_data(as_text=True)
    assert f"/cards/add?ordner={ordner}&amp;seite=2&amp;platz=5&amp;zurueck=ordner" in html

    formular = klient.get(f"/cards/add?ordner={ordner}&seite=2&platz=5&zurueck=ordner"
                          ).get_data(as_text=True)
    assert 'name="page" value="2"' in formular and 'name="slot" value="5"' in formular
    assert 'name="zurueck" value="ordner"' in formular
    assert f'<option value="{ordner}" selected>' in formular

    kennung = re.search(r'name="formular_id" value="([0-9a-f]+)"', formular).group(1)
    antwort = klient.post("/cards/add", data={
        "name": "Data", "set_code": "trk", "collector_number": "55", "language": "en",
        "condition": "NM", "price": "1", "quantity": "1", "folder_id": str(ordner),
        "page": "2", "slot": "5", "item_type": "card", "zurueck": "ordner",
        "formular_id": kennung})
    assert antwort.headers["Location"].endswith(
        f"/ordner/{ordner}?seite=2&inventur=1#O01-S02-P5")
    assert _q(pfad, "SELECT name, storage_code FROM cards") == [("Data", "O01-S02-P5")]
