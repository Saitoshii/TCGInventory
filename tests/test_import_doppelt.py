"""Import: keine Karte kommt doppelt in den Bestand.

Bei der Inventur stand mehr im System als im Regal. Beim Einlesen gab es
mehrere Wege, auf denen dieselben Karten zweimal in die Warteschlange kamen —
und „Alle übernehmen" zählte sie dann doppelt:

* „Erneut auflösen" lief nach einem Kartendaten-Update im Hintergrund und
  zugleich per Knopf „Alle erneut versuchen". Beide nahmen dieselben Einträge
  in die Warteschlange.
* Ein zweiter Klick auf „Hochladen" startete einen zweiten, gleichzeitigen
  Import derselben Datei.
* Dieselbe Datei ein zweites Mal hochgeladen: alles, was schon im Bestand
  war, wurde ohne Hinweis noch einmal gezählt.

Dazu kamen Knöpfe, die nur auf die Stelle in der Liste zeigten: hatte sich die
Liste inzwischen geändert, traf der Klick einen anderen Eintrag.
"""

import io
import os
import sqlite3
import sys
import threading
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


DATEI = (
    "sep=,\n"
    "Folder Name,Quantity,Trade Quantity,Card Name,Set Code,Set Name,"
    "Card Number,Condition,Printing,Language,Price Bought,Date Bought,LOW,MID,MARKET\n"
    "Star Trek,2,0,Spock,TRK,Star Trek,137,NearMint,Normal,English,0.20,2026-11-10,0,0,0\n"
    "Star Trek,1,0,Data,TRK,Star Trek,55,NearMint,Foil,English,0.50,2026-11-10,0,0,0\n"
    "Star Trek,3,0,Worf,TRK,Star Trek,12,NearMint,Normal,English,0.10,2026-11-10,0,0,0\n"
).encode("utf-8")


def _scryfall(set_code, nummer, sprache=None):
    """Ersatz für die Kartendaten: jede Karte ist bekannt."""
    return {"name": {"137": "Spock", "55": "Data", "12": "Worf"}.get(str(nummer), "Karte"),
            "set_code": set_code, "collector_number": str(nummer),
            "language": sprache or "en", "scryfall_id": f"sf-{nummer}",
            "cardmarket_id": f"cm-{nummer}", "image_url": ""}


class _Sofort:
    """Ersatz für ``threading.Thread``: läuft beim Start gleich durch."""

    def __init__(self, target=None, args=(), kwargs=None, daemon=None):
        self._ziel, self._args = target, args

    def start(self):
        self._ziel(*self._args)


def _leeren():
    web.UPLOAD_QUEUE.clear()
    web.NEEDS_REVIEW.clear()
    web.BULK_PROGRESS, web.BULK_DONE, web.BULK_MESSAGE = 0, False, None
    web.BULK_LAEUFT = False


@pytest.fixture()
def lager(tmp_path, monkeypatch):
    pfad = str(tmp_path / "lager.db")
    for modul in (TCGInventory, web, auth, setup_db, order_service, lager_manager):
        monkeypatch.setattr(modul, "DB_FILE", pfad)
    setup_db.initialize_database()
    monkeypatch.setattr(web, "find_by_identity", _scryfall)
    monkeypatch.setattr(web, "_starte_kartendaten_aktualisierung",
                        lambda *a, **k: False)
    _leeren()
    fid = lager_manager.add_folder("Star Trek", 3)
    lager_manager.create_binder(fid, 3)
    yield pfad, fid
    _leeren()


@pytest.fixture()
def sofort(monkeypatch):
    monkeypatch.setattr(web.threading, "Thread", _Sofort)


def _klient():
    web.app.config["TESTING"] = True
    klient = web.app.test_client()
    with klient.session_transaction() as s:
        s["user"] = "melvin"
    return klient


def _lade_hoch(ordner, inhalt=DATEI, erneut=False, name="export.csv"):
    daten = {"folder_id": str(ordner), "cards": "",
             "csv_file": (io.BytesIO(inhalt), name)}
    if erneut:
        daten["erneut"] = "1"
    return _klient().post("/cards/bulk_add", data=daten,
                          content_type="multipart/form-data")


def _bestand(pfad):
    with sqlite3.connect(pfad) as conn:
        return dict(conn.execute(
            "SELECT name, SUM(quantity) FROM cards GROUP BY name").fetchall())


# ---------------------------------------------------------------------------
# „Erneut auflösen" zweimal gleichzeitig
# ---------------------------------------------------------------------------
def test_erneut_aufloesen_gleichzeitig_nimmt_nichts_doppelt(lager, monkeypatch):
    for nummer in ("137", "55", "12"):
        web.NEEDS_REVIEW.append(web._needs_review_entry(
            {"name": "x", "set_code": "trk", "collector_number": nummer,
             "language": "en", "quantity": 1}, None, "Kein Treffer", {}))

    # Beide Läufe sollen gleichzeitig mitten im Auflösen sein. Vor der
    # Korrektur gelang das, und beide übernahmen dieselben Einträge. Jetzt
    # wartet der zweite auf den ersten — die Schranke läuft ab.
    schranke = threading.Barrier(2, timeout=0.3)

    def langsam(set_code, nummer, sprache=None):
        try:
            schranke.wait()
        except threading.BrokenBarrierError:
            pass
        return _scryfall(set_code, nummer, sprache)

    monkeypatch.setattr(web, "find_by_identity", langsam)
    ergebnisse = []
    faeden = [threading.Thread(
        target=lambda: ergebnisse.append(web._needs_review_erneut_aufloesen()))
        for _ in range(2)]
    for faden in faeden:
        faden.start()
    for faden in faeden:
        faden.join()

    assert len(web.UPLOAD_QUEUE) == 3, "Einträge doppelt in der Warteschlange"
    assert sorted(ergebnisse) == [(0, 0), (3, 0)]
    assert web.NEEDS_REVIEW == []


# ---------------------------------------------------------------------------
# Dieselbe Datei zweimal
# ---------------------------------------------------------------------------
def test_dieselbe_datei_wird_nicht_still_ein_zweites_mal_eingelesen(lager, sofort):
    pfad, ordner = lager
    _lade_hoch(ordner)
    _klient().post("/cards/upload_queue/add_all")
    assert _bestand(pfad) == {"Spock": 2, "Data": 1, "Worf": 3}

    html = _lade_hoch(ordner).get_data(as_text=True)
    assert "diese Datei wurde schon einmal eingelesen" in html
    assert "export.csv" in html and "melvin" in html
    assert 'name="erneut"' in html
    assert web.UPLOAD_QUEUE == []
    assert _bestand(pfad) == {"Spock": 2, "Data": 1, "Worf": 3}


def test_andere_zeilenenden_sind_dieselbe_datei(lager, sofort):
    _, ordner = lager
    _lade_hoch(ordner)
    html = _lade_hoch(ordner, inhalt=DATEI.replace(b"\n", b"\r\n")).get_data(as_text=True)
    assert "schon einmal eingelesen" in html


def test_mit_haekchen_geht_es_und_die_warteschlange_sagt_was_schon_da_ist(lager, sofort):
    pfad, ordner = lager
    _lade_hoch(ordner)
    _klient().post("/cards/upload_queue/add_all")
    _lade_hoch(ordner, erneut=True)
    assert len(web.UPLOAD_QUEUE) == 3

    html = _klient().get("/cards/upload_queue").get_data(as_text=True)
    assert "3 Einträge liegen schon im Bestand" in html
    assert "schon im Bestand: 2 → 4" in html            # Spock
    assert "doppelt gezählt" in html
    assert "Nur neue übernehmen (0)" in html
    # „Alle übernehmen" fragt vorher nach.
    assert "Trotzdem alle übernehmen?" in html


def test_nur_neue_uebernehmen(lager, sofort):
    pfad, ordner = lager
    lager_manager.add_or_increment_card("Spock", "trk", "en", "NM", 0.2, 2, None,
                                        "", ordner, "137", "", "", False)
    _lade_hoch(ordner)
    html = _klient().post("/cards/upload_queue/add_all", data={"nur_neue": "1"},
                          follow_redirects=True).get_data(as_text=True)

    assert _bestand(pfad) == {"Spock": 2, "Data": 1, "Worf": 3}
    assert [e["name"] for e in web.UPLOAD_QUEUE] == ["Spock"]
    assert "2 neue Einträge übernommen" in html
    assert "1 bleibt in der Warteschlange" in html


def test_doppelt_in_der_warteschlange_wird_markiert(lager, sofort):
    pfad, ordner = lager
    _lade_hoch(ordner)
    _lade_hoch(ordner, erneut=True)                     # vor dem Übernehmen
    html = _klient().get("/cards/upload_queue").get_data(as_text=True)
    assert "3 Einträge stehen doppelt in der Warteschlange" in html
    assert html.count("doppelt-in-warteschlange") == 3

    _klient().post("/cards/upload_queue/add_all", data={"nur_neue": "1"})
    assert _bestand(pfad) == {"Spock": 2, "Data": 1, "Worf": 3}
    assert len(web.UPLOAD_QUEUE) == 3                   # die Doppel bleiben


def test_set_code_in_anderer_schreibweise_ist_dieselbe_karte(lager):
    """``TRK`` und ``trk`` sind dasselbe Set — keine zweite Zeile."""
    pfad, ordner = lager
    for code in ("TRK", "trk"):
        lager_manager.add_or_increment_card("Spock", code, "en", "NM", 0.2, 1,
                                            None, "", ordner, "137", "", "", False)
    with sqlite3.connect(pfad) as conn:
        assert conn.execute("SELECT COUNT(*), SUM(quantity) FROM cards").fetchone() == (1, 2)


def test_ohne_ordner_wird_aufgestockt_statt_neu_angelegt(lager):
    """„Kein Ordner" stand mal als NULL, mal als leerer Text in der Tabelle."""
    pfad, _ = lager
    lager_manager.add_or_increment_card("Spock", "trk", "en", "NM", 0.2, 1,
                                        None, "", "", "137", "", "", False)
    lager_manager.add_or_increment_card("Spock", "trk", "en", "NM", 0.2, 1,
                                        None, "", None, "137", "", "", False)
    with sqlite3.connect(pfad) as conn:
        assert conn.execute("SELECT COUNT(*), SUM(quantity) FROM cards").fetchone() == (1, 2)


# ---------------------------------------------------------------------------
# Zweiter Klick auf „Hochladen", Abbruch mitten im Import
# ---------------------------------------------------------------------------
def test_zweiter_import_waehrend_einer_laeuft_startet_nicht(lager, monkeypatch):
    _, ordner = lager
    gestartet = []

    class _Merken:
        def __init__(self, target=None, args=(), kwargs=None, daemon=None):
            pass

        def start(self):
            gestartet.append(1)          # läuft „im Hintergrund" weiter

    monkeypatch.setattr(web.threading, "Thread", _Merken)
    _lade_hoch(ordner)
    html = _lade_hoch(ordner, inhalt=DATEI + b"\n").get_data(as_text=True)
    assert gestartet == [1]
    assert "Es läuft bereits ein Import" in html


def test_bricht_der_import_ab_steht_nichts_halb_in_der_warteschlange(lager, sofort,
                                                                     monkeypatch):
    _, ordner = lager
    aufrufe = []

    def bricht_ab(set_code, nummer, sprache=None):
        aufrufe.append(nummer)
        if len(aufrufe) == 3:
            raise RuntimeError("Speicherkarte voll")
        return _scryfall(set_code, nummer, sprache)

    monkeypatch.setattr(web, "find_by_identity", bricht_ab)
    _lade_hoch(ordner)
    assert web.UPLOAD_QUEUE == [] and web.NEEDS_REVIEW == []
    assert "nichts in die Warteschlange übernommen" in web.BULK_MESSAGE
    assert web.BULK_LAEUFT is False
    # Die Datei gilt nicht als eingelesen — ein neuer Versuch geht ohne Häkchen.
    monkeypatch.setattr(web, "find_by_identity", _scryfall)
    _lade_hoch(ordner)
    assert len(web.UPLOAD_QUEUE) == 3


# ---------------------------------------------------------------------------
# Knöpfe treffen den gemeinten Eintrag
# ---------------------------------------------------------------------------
def test_uebernehmen_trifft_den_gemeinten_eintrag(lager, sofort):
    pfad, ordner = lager
    _lade_hoch(ordner)
    worf = web.UPLOAD_QUEUE[2]
    # Inzwischen hat sich die Liste geändert: der erste Eintrag ist weg.
    web.UPLOAD_QUEUE.pop(0)
    _klient().post("/cards/upload_queue/add/2", data={"uid": worf["uid"]})
    assert _bestand(pfad) == {"Worf": 3}


def test_eintrag_nicht_mehr_da_heisst_nichts_tun(lager, sofort):
    pfad, ordner = lager
    _lade_hoch(ordner)
    html = _klient().post("/cards/upload_queue/add/0", data={"uid": "gibt-es-nicht"},
                          follow_redirects=True).get_data(as_text=True)
    assert "steht nicht mehr in der Warteschlange" in html
    assert _bestand(pfad) == {}
    assert len(web.UPLOAD_QUEUE) == 3


def test_needs_review_knopf_trifft_den_gemeinten_eintrag(lager):
    for nummer in ("1", "2"):
        web.NEEDS_REVIEW.append(web._needs_review_entry(
            {"name": f"K{nummer}", "set_code": "", "collector_number": "",
             "language": "en", "quantity": 1}, None, "Set fehlt", {}))
    zweiter = web.NEEDS_REVIEW[1]["uid"]
    web.NEEDS_REVIEW.pop(0)
    # Die Seite zeigte den zweiten noch an Stelle 1.
    _klient().post("/cards/needs_review/discard/1", data={"uid": zweiter})
    assert web.NEEDS_REVIEW == []


@pytest.mark.parametrize("adresse", [
    "/cards/upload_queue/add/0", "/cards/upload_queue/add_all",
    "/cards/upload_queue/clear",
])
def test_aendern_nur_per_formular(lager, adresse):
    """Als Link reichte ein wiederhergestellter Tab oder die Zurück-Taste."""
    web.UPLOAD_QUEUE.append({"name": "Spock", "set_code": "trk"})
    assert _klient().get(adresse).status_code == 405
    assert len(web.UPLOAD_QUEUE) == 1


def test_foil_laesst_sich_auch_abwaehlen(lager, sofort):
    """Das Formular schickt ein verstecktes „0" vor dem Häkchen — das wurde
    als „ja" gelesen, Foil ließ sich nie abwählen."""
    _, ordner = lager
    _lade_hoch(ordner)
    data = web.UPLOAD_QUEUE[1]
    assert data["foil"] is True
    _klient().post("/cards/upload_queue/foil/1", data={"uid": data["uid"], "foil": "0"})
    assert data["foil"] is False
    _klient().post("/cards/upload_queue/foil/1",
                   data={"uid": data["uid"], "foil": ["0", "1"]})
    assert data["foil"] is True
