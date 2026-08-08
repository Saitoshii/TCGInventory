"""Aufbau der lokalen Kartendatenbank aus den Scryfall-Bulkdaten.

Kernanforderung: der Aufbau muss auf einem Raspberry Pi mit 4 GB laufen. Die
Bulkdatei wird deshalb streamend verarbeitet — sie darf nie vollständig in den
Speicher geladen werden.
"""

import io
import json
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

from TCGInventory import build_card_db as bcd          # noqa: E402
import TCGInventory.card_scanner as cs                  # noqa: E402


def _karte(i, **extra):
    karte = {
        "id": f"{i:08d}-aaaa-bbbb-cccc-dddddddddddd",
        "name": f"Karte {i}",
        "set": "tst",
        "set_name": "Testset",
        "lang": "en",
        "collector_number": str(i),
        "cardmarket_id": 1000 + i,
    }
    karte.update(extra)
    return karte


# =========================================================================
# Streamendes Lesen
# =========================================================================

def test_iter_json_array_reads_objects_one_by_one():
    daten = json.dumps([_karte(i) for i in range(50)])
    objekte = list(bcd.iter_json_array(io.StringIO(daten)))
    assert len(objekte) == 50
    assert objekte[0]["name"] == "Karte 0"
    assert objekte[-1]["collector_number"] == "49"


def test_iter_json_array_works_with_tiny_read_blocks():
    """Auch wenn ein Objekt über mehrere Leseblöcke reicht, bleibt es intakt."""
    daten = json.dumps([_karte(i) for i in range(20)])
    objekte = list(bcd.iter_json_array(io.StringIO(daten), leseblock=7))
    assert len(objekte) == 20
    assert [o["collector_number"] for o in objekte] == [str(i) for i in range(20)]


def test_iter_json_array_handles_whitespace_and_empty_array():
    assert list(bcd.iter_json_array(io.StringIO("  [ ]  "))) == []
    schoen = "[\n  " + ",\n  ".join(json.dumps(_karte(i)) for i in range(3)) + "\n]\n"
    assert len(list(bcd.iter_json_array(io.StringIO(schoen)))) == 3


def test_iter_json_array_rejects_non_array():
    with pytest.raises(ValueError):
        list(bcd.iter_json_array(io.StringIO('{"a": 1}')))


def test_iter_json_lines_reads_one_card_per_line():
    """Scryfall liefert inzwischen JSON Lines – eine Karte je Zeile."""
    text = "\n".join(json.dumps(_karte(i)) for i in range(5)) + "\n"
    objekte = list(bcd.iter_json_lines(io.StringIO(text)))
    assert len(objekte) == 5
    assert objekte[3]["collector_number"] == "3"


def test_iter_json_lines_ignores_blank_and_bracket_lines():
    text = "[\n" + ",\n".join(json.dumps(_karte(i)) for i in range(2)) + "\n]\n\n"
    assert len(list(bcd.iter_json_lines(io.StringIO(text)))) == 2


def test_import_reads_gzipped_jsonl(tmp_path):
    """Der Weg, den der Download nutzt: .jsonl.gz."""
    import gzip
    quelle = tmp_path / "bulk.jsonl.gz"
    with gzip.open(quelle, "wt", encoding="utf-8") as f:
        for i in range(4):
            f.write(json.dumps(_karte(i)) + "\n")
    db = tmp_path / "cards.db"
    assert bcd.import_cards(quelle, db) == 4


class _ZaehlenderStrom(io.StringIO):
    """Merkt sich, wie viel am Stück gelesen wurde."""

    def __init__(self, text):
        super().__init__(text)
        self.groesster_block = 0

    def read(self, n=-1):
        block = super().read(n)
        self.groesster_block = max(self.groesster_block, len(block))
        return block


def test_parsing_does_not_read_whole_file_at_once():
    """Der Speicherbedarf haengt am Leseblock, nicht an der Dateigroesse."""
    daten = json.dumps([_karte(i) for i in range(3000)])
    strom = _ZaehlenderStrom(daten)
    anzahl = sum(1 for _ in bcd.iter_json_array(strom, leseblock=4096))
    assert anzahl == 3000
    assert strom.groesster_block <= 4096
    assert len(daten) > 100_000            # die Datei war deutlich groesser


# =========================================================================
# Aufbau der Datenbank
# =========================================================================

def test_build_writes_all_columns(tmp_path):
    db = tmp_path / "cards.db"
    anzahl = bcd.schreibe_datenbank([_karte(1), _karte(2)], db)
    assert anzahl == 2

    with sqlite3.connect(str(db)) as conn:
        conn.row_factory = sqlite3.Row
        zeile = conn.execute("SELECT * FROM cards WHERE collector_number='1'").fetchone()
        spalten = [r[1] for r in conn.execute("PRAGMA table_info(cards)")]
    assert zeile["name"] == "Karte 1"
    assert zeile["set_code"] == "tst"
    assert zeile["set_name"] == "Testset"
    assert zeile["cardmarket_id"] == "1001"
    assert "image_url" not in spalten           # wird aus der ID abgeleitet


def test_digital_only_cards_are_skipped(tmp_path):
    db = tmp_path / "cards.db"
    anzahl = bcd.schreibe_datenbank(
        [_karte(1), _karte(2, digital=True), _karte(3)], db)
    assert anzahl == 2
    with sqlite3.connect(str(db)) as conn:
        nummern = [r[0] for r in conn.execute("SELECT collector_number FROM cards ORDER BY collector_number")]
    assert nummern == ["1", "3"]               # die Arena-only-Karte fehlt


def test_indexes_are_created(tmp_path):
    db = tmp_path / "cards.db"
    bcd.schreibe_datenbank([_karte(1)], db)
    with sqlite3.connect(str(db)) as conn:
        namen = {r[0] for r in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='index'")}
    assert {"idx_name", "idx_identity", "idx_set_name"} <= namen


def test_import_from_file(tmp_path):
    quelle = tmp_path / "bulk.json"
    quelle.write_text(json.dumps([_karte(i) for i in range(5)]), encoding="utf-8")
    db = tmp_path / "cards.db"
    assert bcd.import_cards(quelle, db) == 5


def test_missing_source_file_raises(tmp_path):
    with pytest.raises(FileNotFoundError):
        bcd.import_cards(tmp_path / "gibtsnicht.json", tmp_path / "x.db")


# =========================================================================
# Atomarer Tausch — die alte Datenbank darf nie kaputtgehen
# =========================================================================

def test_existing_database_survives_a_failed_run(tmp_path):
    db = tmp_path / "cards.db"
    bcd.schreibe_datenbank([_karte(1), _karte(2)], db)
    vorher = db.read_bytes()

    def kaputte_quelle():
        yield _karte(3)
        raise RuntimeError("Verbindung abgebrochen")

    with pytest.raises(RuntimeError):
        bcd.schreibe_datenbank(kaputte_quelle(), db)

    assert db.read_bytes() == vorher           # alte Datei unveraendert
    assert not (tmp_path / "cards.db.neu").exists()   # Zwischendatei aufgeraeumt


def test_empty_result_does_not_replace_database(tmp_path):
    db = tmp_path / "cards.db"
    bcd.schreibe_datenbank([_karte(1)], db)
    vorher = db.read_bytes()

    with pytest.raises(ValueError):
        bcd.schreibe_datenbank([], db)         # z. B. leere Antwort vom Server

    assert db.read_bytes() == vorher


def test_replacement_is_atomic(tmp_path):
    db = tmp_path / "cards.db"
    bcd.schreibe_datenbank([_karte(1)], db)
    bcd.schreibe_datenbank([_karte(7), _karte(8)], db)
    with sqlite3.connect(str(db)) as conn:
        assert conn.execute("SELECT COUNT(*) FROM cards").fetchone()[0] == 2


# =========================================================================
# Bildadresse aus der ID
# =========================================================================

def test_image_url_derivation():
    kennung = "1d7dba1c-a702-43c0-8fca-e47bbad4a00f"
    assert cs.image_url_for(kennung) == (
        f"https://cards.scryfall.io/normal/front/1/d/{kennung}.jpg")
    assert cs.image_url_for(kennung, "small").startswith(
        "https://cards.scryfall.io/small/front/1/d/")
    assert cs.image_url_for("") == ""          # kein Absturz ohne ID


def test_lookup_returns_derived_image_url(tmp_path):
    db = tmp_path / "cards.db"
    bcd.schreibe_datenbank([_karte(1)], db)
    cs.reset_card_database()
    cs.DEFAULT_DB_PATH = db

    treffer = cs.fetch_card_info_by_name("Karte 1")
    assert treffer["image_url"] == cs.image_url_for(treffer["scryfall_id"])
    assert treffer["image_url"].startswith("https://cards.scryfall.io/normal/front/")


def test_reset_closes_cached_connection(tmp_path):
    db = tmp_path / "cards.db"
    bcd.schreibe_datenbank([_karte(1)], db)
    cs.reset_card_database()
    cs.DEFAULT_DB_PATH = db
    cs.fetch_card_info_by_name("Karte 1")
    assert cs._DB_CONN is not None

    cs.reset_card_database()
    assert cs._DB_CONN is None                 # nach dem Tausch neu oeffnen


def test_swapped_database_is_seen_after_reset(tmp_path):
    """Nach dem atomaren Tausch muss die Anwendung die neuen Daten sehen."""
    db = tmp_path / "cards.db"
    bcd.schreibe_datenbank([_karte(1)], db)
    cs.reset_card_database()
    cs.DEFAULT_DB_PATH = db
    assert cs.fetch_card_info_by_name("Karte 1") is not None

    # Die offene Leseverbindung wird unmittelbar vor dem Tausch geschlossen.
    bcd.schreibe_datenbank([_karte(2)], db, vor_tausch=cs.reset_card_database)
    assert cs.fetch_card_info_by_name("Karte 2") is not None
    assert cs.fetch_card_info_by_name("Karte 1") is None


# =========================================================================
# Weboberflaeche
# =========================================================================

def test_card_data_page_renders(tmp_path, monkeypatch):
    from TCGInventory import web
    web.app.config["TESTING"] = True
    client = web.app.test_client()
    with client.session_transaction() as s:
        s["user"] = "tester"

    seite = client.get("/system/kartendaten").get_data(as_text=True)
    assert "Kartendaten" in seite

    status = client.get("/system/kartendaten/status").get_json()
    assert "laeuft" in status and "meldung" in status


def test_update_route_starts_background_run(tmp_path, monkeypatch):
    """Die Route stoesst den Lauf an und blockiert die Oberflaeche nicht."""
    from TCGInventory import web
    aufgerufen = {}

    def falscher_lauf(fortschritt=None, erzwingen=False, **kw):
        aufgerufen["erzwingen"] = erzwingen
        if fortschritt:
            fortschritt(2, "Test")
        return {"aktualisiert": True, "anzahl": 2, "stand": "2026-01-01"}

    monkeypatch.setattr(web.build_card_db, "aktualisiere_von_scryfall", falscher_lauf)
    web.app.config["TESTING"] = True
    client = web.app.test_client()
    with client.session_transaction() as s:
        s["user"] = "tester"

    antwort = client.post("/system/kartendaten/aktualisieren", data={"erzwingen": "1"})
    assert antwort.status_code == 302
    for _ in range(50):                        # kurz auf den Hintergrundlauf warten
        if web.CARDDATA_STATUS["fertig"]:
            break
        import time
        time.sleep(0.05)
    assert web.CARDDATA_STATUS["fertig"] is True
    assert web.CARDDATA_STATUS["fehler"] is None
    assert aufgerufen["erzwingen"] is True


def test_update_route_reports_failure(monkeypatch):
    """Ein Fehlschlag wird gemeldet, nicht verschluckt."""
    from TCGInventory import web

    def kaputt(**kw):
        raise RuntimeError("Netzwerk weg")

    monkeypatch.setattr(web.build_card_db, "aktualisiere_von_scryfall", kaputt)
    web.CARDDATA_STATUS.update({"laeuft": False, "fertig": False, "fehler": None})
    web.app.config["TESTING"] = True
    client = web.app.test_client()
    with client.session_transaction() as s:
        s["user"] = "tester"

    client.post("/system/kartendaten/aktualisieren")
    for _ in range(50):
        if web.CARDDATA_STATUS["fertig"]:
            break
        import time
        time.sleep(0.05)
    assert "Netzwerk weg" in (web.CARDDATA_STATUS["fehler"] or "")
