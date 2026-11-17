"""„Versendet / Verkauft": der Bestand muss danach stimmen.

Bei der Inventur stand mehr im System als im Regal. Mehrere Wege führten
dorthin, und alle hatten mit dem Abschließen einer Bestellung zu tun:

* Positionen ohne zugeordnete Karte wurden nicht abgezogen. Das stand erst in
  der Meldung danach — in demselben blauen Kasten wie jede Bestätigung, und da
  war die Bestellung schon aus der Liste verschwunden.
* Gab der Bestand weniger her als bestellt, hieß es trotzdem „abgezogen".
* Offene Bestellungen, die älter als 30 Tage waren, standen nirgends mehr —
  samt dem Knopf, der ihre Karten abgezogen hätte.
* Wer eine verschickte Bestellung löschte statt sie abzuschließen, behielt
  die Karten im Bestand, ohne dass irgendetwas darauf hinwies.

In die andere Richtung ging der Doppelklick: zwei gleichzeitige Anfragen
zogen dieselbe Bestellung zweimal ab.
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

import TCGInventory                                               # noqa: E402
from TCGInventory import (auth, lager_manager, order_service,     # noqa: E402
                          setup_db, web)


@pytest.fixture()
def db(tmp_path, monkeypatch):
    pfad = str(tmp_path / "b.db")
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


def _q(db, sql, *werte):
    with sqlite3.connect(db) as conn:
        return conn.execute(sql, werte).fetchall()


def _karte(db, name="Spock", menge=1, platz="O01-S01-P1"):
    with sqlite3.connect(db) as conn:
        kid = conn.execute(
            "INSERT INTO cards (name, set_code, language, condition, price, quantity, "
            "storage_code, status, collector_number, foil, item_type) "
            "VALUES (?, 'trk', 'en', 'NM', 1.0, ?, ?, 'verfügbar', '137', 0, 'card')",
            (name, menge, platz)).lastrowid
        conn.commit()
    return kid


def _bestellung(db, positionen, tage_alt=0, status="open", nummer="1001"):
    """positionen: Liste von (Name, Menge, card_id oder None)."""
    datum = (datetime.now() - timedelta(days=tage_alt)).isoformat(timespec="seconds")
    with sqlite3.connect(db) as conn:
        bid = conn.execute(
            "INSERT INTO orders (buyer_name, email_message_id, date_received, "
            "email_date, status, order_number) VALUES ('Kaeufer', ?, ?, ?, ?, ?)",
            (f"m-{nummer}-{tage_alt}", datum, datum, status, nummer)).lastrowid
        for name, menge, card_id in positionen:
            conn.execute(
                "INSERT INTO order_items (order_id, card_name, quantity, card_id, "
                "match_status) VALUES (?, ?, ?, ?, ?)",
                (bid, name, menge, card_id,
                 "matched" if card_id else "unresolved"))
        conn.commit()
    return bid


def _menge(db, kid):
    zeile = _q(db, "SELECT quantity FROM cards WHERE id = ?", kid)
    return zeile[0][0] if zeile else None


# ---------------------------------------------------------------------------
# Ein Klick, ein Abzug
# ---------------------------------------------------------------------------
def test_doppelklick_zieht_nur_einmal_ab(db, monkeypatch):
    kid = _karte(db, menge=10)
    bid = _bestellung(db, [("Spock", 1, kid)])

    # Beide Anfragen sollen gleichzeitig beim Abziehen ankommen. Vor der
    # Korrektur gelang das: erst wurde abgezogen, dann der Status gesetzt.
    # Jetzt wartet die zweite Anfrage, bis die erste fertig ist — die Schranke
    # läuft ab, weil nur noch eine dort ankommt.
    schranke = threading.Barrier(2, timeout=0.5)
    echt = web.buche_aus

    def langsam(cursor, card_id, menge, user="system"):
        try:
            schranke.wait()
        except threading.BrokenBarrierError:
            pass
        return echt(cursor, card_id, menge, user)

    monkeypatch.setattr(web, "buche_aus", langsam)

    faeden = [threading.Thread(
        target=lambda: _klient().post(f"/orders/{bid}/mark_sold"))
        for _ in range(2)]
    for faden in faeden:
        faden.start()
    for faden in faeden:
        faden.join()

    assert _menge(db, kid) == 9, "die Bestellung wurde zweimal abgezogen"
    assert _q(db, "SELECT status FROM orders WHERE id = ?", bid) == [("sold",)]


def test_zweiter_klick_zieht_nicht_noch_einmal_ab(db):
    kid = _karte(db, menge=5)
    bid = _bestellung(db, [("Spock", 2, kid)])
    klient = _klient()
    klient.post(f"/orders/{bid}/mark_sold")
    text = klient.post(f"/orders/{bid}/mark_sold",
                       follow_redirects=True).get_data(as_text=True)
    assert _menge(db, kid) == 3
    assert "kein erneuter Abzug" in text


def test_bricht_etwas_ab_ist_nichts_geschrieben(db, monkeypatch):
    """Halb abgezogen und trotzdem offen wäre die schlechteste Zwischenstufe."""
    erste = _karte(db, "Spock", 3, "O01-S01-P1")
    zweite = _karte(db, "Kirk", 3, "O01-S01-P2")
    bid = _bestellung(db, [("Kirk", 1, zweite), ("Spock", 1, erste)])
    echt = web.buche_aus
    aufrufe = []

    def bricht_ab(cursor, card_id, menge, user="system"):
        aufrufe.append(card_id)
        if len(aufrufe) == 2:
            raise RuntimeError("Stecker gezogen")
        return echt(cursor, card_id, menge, user)

    monkeypatch.setattr(web, "buche_aus", bricht_ab)
    with pytest.raises(RuntimeError):
        _klient().post(f"/orders/{bid}/mark_sold")

    assert _menge(db, erste) == 3 and _menge(db, zweite) == 3
    assert _q(db, "SELECT status FROM orders WHERE id = ?", bid) == [("open",)]


# ---------------------------------------------------------------------------
# Was nicht abgezogen wird, steht da — vorher und nachher
# ---------------------------------------------------------------------------
def test_fehlbestand_wird_gemeldet_und_festgehalten(db):
    kid = _karte(db, menge=1)
    bid = _bestellung(db, [("Spock", 3, kid)])
    text = _klient().post(f"/orders/{bid}/mark_sold",
                          follow_redirects=True).get_data(as_text=True)

    assert "1 Karte(n) abgezogen" in text
    assert "Im Bestand stand weniger als bestellt" in text
    assert "Spock (1 von 3)" in text
    assert _menge(db, kid) is None                       # letztes Exemplar weg
    assert _q(db, "SELECT abgezogen FROM order_items WHERE order_id = ?",
              bid) == [(1,)]


def test_menge_null_gibt_nichts_her(db):
    """Eine Zeile mit Menge 0 ist kein Exemplar — nichts abziehen, nichts
    als abgezogen melden."""
    kid = _karte(db, menge=0)
    bid = _bestellung(db, [("Spock", 1, kid)])
    text = _klient().post(f"/orders/{bid}/mark_sold",
                          follow_redirects=True).get_data(as_text=True)
    assert "Spock (0 von 1)" in text
    assert _q(db, "SELECT abgezogen FROM order_items WHERE order_id = ?",
              bid) == [(0,)]


def test_nicht_zugeordnet_steht_vor_dem_abschliessen_da(db):
    _karte(db)
    bid = _bestellung(db, [("Data", 2, None)])
    html = _klient().get("/orders").get_data(as_text=True)

    assert "keiner Karte im Bestand zugeordnet" in html
    assert "2× Data" in html
    # Ohne ausdrückliches Häkchen schickt der Browser das Formular nicht ab.
    assert 'name="ohne_zuordnung" value="1"' in html
    kaestchen = html.split('name="ohne_zuordnung"')[1].split(">")[0]
    assert "required" in kaestchen
    assert _q(db, "SELECT status FROM orders WHERE id = ?", bid) == [("open",)]


def test_alles_zugeordnet_braucht_kein_haekchen(db):
    kid = _karte(db)
    _bestellung(db, [("Spock", 1, kid)])
    html = _klient().get("/orders").get_data(as_text=True)
    assert 'name="ohne_zuordnung"' not in html


def test_herausgenommene_position_braucht_kein_haekchen(db):
    """Was aus der Bestellung genommen wurde, wird ohnehin nicht abgezogen."""
    bid = _bestellung(db, [("Data", 1, None)])
    with sqlite3.connect(db) as conn:
        conn.execute("UPDATE order_items SET entfernt = 1, entfernt_grund = 'fehlt' "
                     "WHERE order_id = ?", (bid,))
        conn.commit()
    html = _klient().get("/orders").get_data(as_text=True)
    assert 'name="ohne_zuordnung"' not in html


def test_nicht_zugeordnet_wird_festgehalten_und_als_warnung_gemeldet(db):
    kid = _karte(db, menge=4)
    bid = _bestellung(db, [("Data", 2, None), ("Spock", 1, kid)])
    html = _klient().post(f"/orders/{bid}/mark_sold",
                          data={"ohne_zuordnung": "1"},
                          follow_redirects=True).get_data(as_text=True)

    assert "NICHT abgezogen: Data (2x)" in html
    # Als Warnung, nicht als blaue Routine-Meldung.
    kasten = html.split("NICHT abgezogen")[0].rsplit('<div class="alert', 1)[1]
    assert "alert-warning" in kasten
    assert _menge(db, kid) == 3
    assert sorted(_q(db, "SELECT card_name, abgezogen FROM order_items "
                         "WHERE order_id = ?", bid)) == [("Data", 0), ("Spock", 1)]


def test_zugeordnete_karte_gibt_es_nicht_mehr(db):
    """Die Karte wurde inzwischen verkauft oder gelöscht: das muss vor dem
    Abschließen auffallen, und die Position braucht eine neue Zuordnung."""
    weg = _karte(db, "Spock", 1, "O01-S01-P1")
    ersatz = _karte(db, "Spock", 1, "O02-S01-P1")
    bid = _bestellung(db, [("Spock", 1, weg)])
    lager_manager.delete_card(weg)

    klient = _klient()
    html = klient.get("/orders").get_data(as_text=True)
    assert "Nicht mehr im Bestand" in html
    assert 'name="ohne_zuordnung"' in html
    # Die Kandidaten werden wieder angeboten.
    assert f'name="card_id" value="{ersatz}"' in html

    item = _q(db, "SELECT id FROM order_items WHERE order_id = ?", bid)[0][0]
    klient.post(f"/orders/items/{item}/assign", data={"card_id": str(ersatz)})
    html = klient.get("/orders").get_data(as_text=True)
    assert "Nicht mehr im Bestand" not in html
    assert 'name="ohne_zuordnung"' not in html


def test_zwei_bestellungen_wollen_dasselbe_letzte_exemplar(db):
    kid = _karte(db, menge=1)
    _bestellung(db, [("Spock", 1, kid)], nummer="1")
    _bestellung(db, [("Spock", 1, kid)], nummer="2")
    html = _klient().get("/orders").get_data(as_text=True)
    assert html.count("Bestellungen verlangt: 2") == 2


def test_bestand_reicht_nicht_steht_an_der_position(db):
    kid = _karte(db, menge=1)
    _bestellung(db, [("Spock", 3, kid)])
    html = _klient().get("/orders").get_data(as_text=True)
    assert "Im Bestand nur 1 von 3" in html


# ---------------------------------------------------------------------------
# Ältere offene Bestellungen verschwinden nicht mehr
# ---------------------------------------------------------------------------
def test_aeltere_offene_bestellungen_werden_genannt(db):
    kid = _karte(db, menge=2)
    alt = _bestellung(db, [("Spock", 1, kid)], tage_alt=45, nummer="9")
    neu = _bestellung(db, [("Spock", 1, kid)], nummer="10")
    klient = _klient()

    html = klient.get("/orders").get_data(as_text=True)
    assert f'id="bestellung-{neu}"' in html
    assert f'id="bestellung-{alt}"' not in html
    assert "1 offene Bestellung ist älter als 30 Tage" in html
    assert "nicht vom Bestand abgezogen" in html
    assert "/orders?alle=1" in html

    html = klient.get("/orders?alle=1").get_data(as_text=True)
    assert f'id="bestellung-{alt}"' in html and f'id="bestellung-{neu}"' in html
    assert "älter als 30 Tage</span>" in html

    # Und sie lässt sich von dort abschließen.
    klient.post(f"/orders/{alt}/mark_sold")
    assert _menge(db, kid) == 1
    html = klient.get("/orders").get_data(as_text=True)
    assert "älter als 30 Tage" not in html


def test_ohne_aeltere_bestellungen_kein_hinweis(db):
    _bestellung(db, [("Data", 1, None)])
    html = _klient().get("/orders").get_data(as_text=True)
    assert "aeltere-hinweis" not in html


# ---------------------------------------------------------------------------
# Löschen
# ---------------------------------------------------------------------------
def test_loeschen_laesst_den_bestand_und_sagt_das(db):
    kid = _karte(db, menge=2)
    bid = _bestellung(db, [("Spock", 1, kid)], nummer="4711")
    html = _klient().post(f"/orders/{bid}/delete",
                          follow_redirects=True).get_data(as_text=True)

    assert "Der Bestand wurde dabei nicht verändert" in html
    assert _menge(db, kid) == 2
    assert _q(db, "SELECT COUNT(*) FROM orders") == [(0,)]
    assert _q(db, "SELECT COUNT(*) FROM order_items") == [(0,)]
    # Das Löschen hinterlässt eine Spur.
    spur = _q(db, "SELECT user, old_value, new_value FROM audit_log "
                  "WHERE action = 'bestellung-geloescht'")
    assert spur == [("melvin", "4711 (Kaeufer)",
                     "1 Position(en), 1 Stück – Bestand unverändert")]


def test_verkaufte_bestellung_laesst_sich_nicht_loeschen(db):
    bid = _bestellung(db, [("Data", 1, None)], status="sold")
    html = _klient().post(f"/orders/{bid}/delete",
                          follow_redirects=True).get_data(as_text=True)
    assert "lässt sich nicht löschen" in html
    assert _q(db, "SELECT COUNT(*) FROM orders") == [(1,)]


def test_loeschdialog_sagt_was_loeschen_bedeutet(db):
    _bestellung(db, [("Data", 1, None)])
    html = _klient().get("/orders").get_data(as_text=True)
    assert "der Bestand wird dabei NICHT verändert" in html
    assert "stattdessen" in html


# ---------------------------------------------------------------------------
# Meldungen haben die Farbe ihrer Art
# ---------------------------------------------------------------------------
@pytest.mark.parametrize("art, klasse", [
    ("error", "alert-danger"), ("warning", "alert-warning"),
    ("success", "alert-success"), ("message", "alert-info"),
])
def test_meldung_in_der_farbe_ihrer_art(db, art, klasse):
    klient = _klient()
    with klient.session_transaction() as s:
        s["_flashes"] = [(art, "Probe-Meldung")]
    html = klient.get("/orders").get_data(as_text=True)
    kasten = html.split("Probe-Meldung")[0].rsplit("<div", 1)[1]
    assert klasse in kasten and "flash-meldung" in kasten


def test_skript_erkennt_abgelaufene_anmeldung(db):
    """Nach abgelaufener Anmeldung antwortet der Server mit der Anmeldeseite.
    Das Skript darf dann nicht so tun, als sei gespeichert."""
    _bestellung(db, [("Data", 1, None)])
    html = _klient().get("/orders").get_data(as_text=True)
    assert "die Anmeldung ist abgelaufen" in html
    assert "ziel.pathname === '/login'" in html
