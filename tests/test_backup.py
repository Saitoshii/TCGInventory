"""WP4a: verschlüsseltes Backup nach OneDrive — Logik, Retention, Restore.

rclone selbst wird hier **nicht** gegen OneDrive getestet. Der Upload ist über
``RemoteStore`` gekapselt; die Tests verwenden ein lokales Ziel, damit Kopie,
Prüfsumme, Retention, Status und Wiederherstellung vollständig prüfbar bleiben.
"""

import os
import sqlite3
import sys
import tarfile
import types
from datetime import datetime, timedelta
from pathlib import Path

import pytest

sys.modules.setdefault("cv2", types.SimpleNamespace())
_pyz = types.ModuleType("pyzbar")
_pyz.pyzbar = types.SimpleNamespace(decode=lambda *a, **k: [])
sys.modules.setdefault("pyzbar", _pyz)
sys.modules.setdefault("pyzbar.pyzbar", _pyz.pyzbar)

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(__file__))))

from TCGInventory.scripts import backup, restore  # noqa: E402
from TCGInventory import backup_status            # noqa: E402

REPO = Path(__file__).resolve().parent.parent


def _lege_db_an(pfad: Path, zeilen: int = 3) -> None:
    pfad.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(pfad))
    conn.execute("CREATE TABLE IF NOT EXISTS cards (id INTEGER PRIMARY KEY, name TEXT)")
    conn.executemany("INSERT INTO cards (name) VALUES (?)",
                     [(f"Karte {i}",) for i in range(zeilen)])
    conn.commit()
    conn.close()


def _umgebung(tmp_path: Path) -> backup.Config:
    """Vollständig isolierte Konfiguration: Quelle, Arbeitsordner und Ziel."""
    root = tmp_path / "app"
    data = root / "data"
    ziel = tmp_path / "remote"
    data.mkdir(parents=True, exist_ok=True)
    _lege_db_an(root / "mtg_lager.db")
    (data / "belege").mkdir(parents=True, exist_ok=True)
    (data / "belege" / "rechnung.pdf").write_bytes(b"%PDF-1.4 Beleg")
    return backup.Config(
        root=root, data_dir=data,
        work_dir=tmp_path / "work",
        status_file=tmp_path / "status.json",
        log_file=tmp_path / "backup.log",
        remote=str(ziel),
    )


def _mit_buchhaltung(tmp_path: Path) -> backup.Config:
    """Konfiguration mit dem Datenverzeichnis der eigenstaendigen Buchhaltung."""
    cfg = _umgebung(tmp_path)
    buch = tmp_path / "buchhaltung-daten"
    (buch / "belege" / "2026" / "06").mkdir(parents=True, exist_ok=True)
    (buch / "belege" / "2026" / "06" / "20260612_kartons.pdf").write_bytes(
        b"%PDF-1.4 Buchhaltungsbeleg")
    conn = sqlite3.connect(str(buch / "buchhaltung.db"))
    conn.execute("CREATE TABLE transaktion (id INTEGER PRIMARY KEY, betrag INTEGER)")
    conn.execute("INSERT INTO transaktion (betrag) VALUES (1215)")
    conn.commit()
    conn.close()
    cfg.buch_dir = buch
    return cfg


# =========================================================================
# SQLite-sichere Kopie und Integritätsprüfung
# =========================================================================

def test_sqlite_copy_is_valid_and_passes_integrity_check(tmp_path):
    quelle = tmp_path / "quelle.db"
    _lege_db_an(quelle, zeilen=5)
    ziel = tmp_path / "kopie.db"

    backup.sqlite_sichere_kopie(quelle, ziel)

    assert ziel.exists()
    assert backup.integritaet_ok(ziel)
    conn = sqlite3.connect(str(ziel))
    try:
        assert conn.execute("SELECT COUNT(*) FROM cards").fetchone()[0] == 5
    finally:
        conn.close()


def test_copy_works_while_application_holds_connection(tmp_path):
    """Die Kopie muss auch bei laufender Anwendung funktionieren."""
    quelle = tmp_path / "quelle.db"
    _lege_db_an(quelle)
    offen = sqlite3.connect(str(quelle))            # simuliert die laufende App
    try:
        offen.execute("INSERT INTO cards (name) VALUES ('offen')")
        offen.commit()
        ziel = tmp_path / "kopie.db"
        backup.sqlite_sichere_kopie(quelle, ziel)
        assert backup.integritaet_ok(ziel)
    finally:
        offen.close()


def test_corrupt_source_aborts_and_uploads_nothing(tmp_path):
    cfg = _umgebung(tmp_path)
    (cfg.root / "mtg_lager.db").write_bytes(b"das ist keine datenbank")   # kaputt

    status = backup.run_backup(cfg)

    assert status["ergebnis"] == "fehler"
    assert status["archiv"] is None
    ziel = Path(cfg.remote)
    assert list(ziel.glob("*")) == []               # nichts hochgeladen


def test_failed_integrity_check_prevents_upload(tmp_path, monkeypatch):
    cfg = _umgebung(tmp_path)
    monkeypatch.setattr(backup, "integritaet_ok", lambda pfad: False)

    status = backup.run_backup(cfg)

    assert status["ergebnis"] == "fehler"
    assert "integrity_check" in status["meldung"]
    assert list(Path(cfg.remote).glob("*")) == []


# =========================================================================
# Archiv und Prüfsumme
# =========================================================================

def test_archive_contains_expected_files_and_checksum_matches(tmp_path):
    cfg = _umgebung(tmp_path)

    status = backup.run_backup(cfg)
    assert status["ergebnis"] == "erfolg", status["meldung"]

    ziel = Path(cfg.remote)
    archive = list(ziel.glob("tcginventory_*.tar.gz"))
    assert len(archive) == 1
    archiv = archive[0]

    with tarfile.open(archiv, "r:gz") as tar:
        namen = tar.getnames()
    assert "db/mtg_lager.db" in namen
    assert "data/belege/rechnung.pdf" in namen
    assert not any(n.endswith("default-cards.db") for n in namen)

    summendatei = ziel / f"{archiv.name}.sha256"
    assert summendatei.exists()
    erwartet = summendatei.read_text(encoding="utf-8").split()[0]
    assert erwartet == backup.sha256_datei(archiv) == status["sha256"]


def test_bulk_card_db_and_secrets_are_not_backed_up(tmp_path):
    cfg = _umgebung(tmp_path)
    _lege_db_an(cfg.data_dir / "default-cards.db")          # darf fehlen
    (cfg.data_dir / "token.json").write_text("{\"geheim\": true}", encoding="utf-8")
    (cfg.data_dir / "onedrive.pass").write_text("geheim", encoding="utf-8")

    backup.run_backup(cfg)

    archiv = next(Path(cfg.remote).glob("tcginventory_*.tar.gz"))
    with tarfile.open(archiv, "r:gz") as tar:
        namen = tar.getnames()
    assert not any("default-cards.db" in n for n in namen)
    assert not any("token.json" in n for n in namen)
    assert not any(n.endswith(".pass") for n in namen)


def test_work_directory_is_cleaned_up(tmp_path):
    cfg = _umgebung(tmp_path)
    backup.run_backup(cfg)
    uebrig = list(cfg.work_dir.rglob("*")) if cfg.work_dir.exists() else []
    assert uebrig == []


# =========================================================================
# Aufbewahrung (Retention)
# =========================================================================

def _name(zeit: datetime) -> str:
    return f"{backup.ARCHIV_PREFIX}{zeit.strftime(backup.ZEITFORMAT)}{backup.ARCHIV_SUFFIX}"


def test_retention_keeps_7_daily_and_12_monthly_over_14_months():
    namen = []
    start = datetime(2025, 1, 1, 3, 30)
    for monat in range(14):                       # 14 Monate à 20 Stände
        for tag in range(20):
            namen.append(_name(start + timedelta(days=monat * 30 + tag)))

    plan = backup.retention_plan(namen, keep_daily=7, keep_monthly=12)
    behalten = set(plan["behalten"])

    assert len(behalten) == 7 + 12                # keine Überschneidung in diesem Fall
    # Die 7 neuesten Stände sind dabei …
    neueste = sorted(namen, key=lambda n: backup.archiv_zeitpunkt(n))[-7:]
    assert set(neueste) <= behalten
    # … und je der erste Stand der 12 jüngsten Monate.
    erste = {}
    for n in sorted(namen, key=lambda n: backup.archiv_zeitpunkt(n)):
        erste.setdefault(backup.archiv_zeitpunkt(n).strftime("%Y-%m"), n)
    juengste_monate = sorted(erste, reverse=True)[:12]
    assert {erste[m] for m in juengste_monate} <= behalten
    # Alles andere wird entfernt, nichts doppelt.
    assert set(plan["loeschen"]).isdisjoint(behalten)
    assert set(plan["loeschen"]) | behalten == set(namen)


def test_retention_survives_gaps_in_history():
    """Mit Lücken bleiben trotzdem 7 Stände und die Monatsstände erhalten."""
    namen = [
        _name(datetime(2025, 1, 5, 3, 30)),       # einziger Stand im Januar
        _name(datetime(2025, 2, 9, 3, 30)),       # einziger Stand im Februar
        _name(datetime(2025, 3, 2, 3, 30)),
        _name(datetime(2025, 3, 3, 3, 30)),
        # April und Mai komplett ausgefallen
        _name(datetime(2025, 6, 1, 3, 30)),
        _name(datetime(2025, 6, 2, 3, 30)),
        _name(datetime(2025, 6, 3, 3, 30)),
        _name(datetime(2025, 6, 4, 3, 30)),
        _name(datetime(2025, 6, 5, 3, 30)),
    ]
    plan = backup.retention_plan(namen, keep_daily=7, keep_monthly=12)

    # Die Monatsstände von Januar und Februar dürfen der Tagesrotation nicht
    # zum Opfer fallen — sonst reißt die Aufbewahrung Löcher.
    assert _name(datetime(2025, 1, 5, 3, 30)) in plan["behalten"]
    assert _name(datetime(2025, 2, 9, 3, 30)) in plan["behalten"]
    assert len([n for n in namen if n in plan["behalten"]]) >= 7


def test_retention_deletes_via_store(tmp_path):
    cfg = _umgebung(tmp_path)
    store = backup.LocalStore(Path(cfg.remote))
    for tag in range(1, 13):                       # 12 Stände im selben Monat
        name = _name(datetime(2026, 5, tag, 3, 30))
        (Path(cfg.remote) / name).write_bytes(b"x")
        (Path(cfg.remote) / f"{name}.sha256").write_text("x", encoding="utf-8")

    entfernt = backup.wende_retention_an(store, cfg)

    verbleibend = store.list_archives()
    assert len(verbleibend) == 8                  # 7 neueste + erster des Monats
    assert _name(datetime(2026, 5, 1, 3, 30)) in verbleibend
    assert entfernt and all(e not in verbleibend for e in entfernt)
    # Begleitdateien werden mitentfernt.
    assert not (Path(cfg.remote) / f"{entfernt[0]}.sha256").exists()


# =========================================================================
# Status-Datei und Protokoll
# =========================================================================

def test_status_file_written_on_success(tmp_path):
    cfg = _umgebung(tmp_path)
    status = backup.run_backup(cfg)

    import json
    gespeichert = json.loads(cfg.status_file.read_text(encoding="utf-8"))
    assert gespeichert["ergebnis"] == "erfolg" == status["ergebnis"]
    assert gespeichert["groesse_bytes"] > 0
    assert gespeichert["sha256"]
    assert "mtg_lager.db" in gespeichert["datenbanken"]
    assert cfg.log_file.exists() and "ERFOLG" in cfg.log_file.read_text(encoding="utf-8")


def test_status_file_written_on_failure(tmp_path):
    cfg = _umgebung(tmp_path)
    (cfg.root / "mtg_lager.db").unlink()          # keine Datenbank -> Fehler

    status = backup.run_backup(cfg)

    import json
    gespeichert = json.loads(cfg.status_file.read_text(encoding="utf-8"))
    assert status["ergebnis"] == "fehler" == gespeichert["ergebnis"]
    assert gespeichert["meldung"]
    assert "FEHLER" in cfg.log_file.read_text(encoding="utf-8")


def test_main_returns_nonzero_on_failure(tmp_path, monkeypatch):
    cfg = _umgebung(tmp_path)
    (cfg.root / "mtg_lager.db").unlink()
    monkeypatch.setattr(backup.Config, "from_env", classmethod(lambda cls: cfg))
    assert backup.main([]) == 1


# =========================================================================
# Weboberfläche: Warnung nach 48 Stunden
# =========================================================================

def _schreibe_status(pfad: Path, zeit: datetime, ergebnis: str = "erfolg") -> None:
    import json
    pfad.write_text(json.dumps({
        "zeitpunkt": zeit.isoformat(timespec="seconds"), "ergebnis": ergebnis,
        "archiv": "tcginventory_test.tar.gz", "groesse_bytes": 2048,
        "sha256": "abc", "dauer_sekunden": 3.2, "meldung": "",
    }), encoding="utf-8")


def test_status_reader_warns_after_48_hours(tmp_path, monkeypatch):
    pfad = tmp_path / "status.json"
    monkeypatch.setenv("TCG_BACKUP_STATUS_FILE", str(pfad))

    _schreibe_status(pfad, datetime.now() - timedelta(hours=3))
    assert backup_status.lies_status()["warnung"] is False

    _schreibe_status(pfad, datetime.now() - timedelta(hours=49))
    aktuell = backup_status.lies_status()
    assert aktuell["warnung"] is True and aktuell["alter_stunden"] > 48

    _schreibe_status(pfad, datetime.now(), ergebnis="fehler")
    assert backup_status.lies_status()["warnung"] is True    # Fehlschlag warnt sofort


def test_status_reader_without_file(tmp_path, monkeypatch):
    monkeypatch.setenv("TCG_BACKUP_STATUS_FILE", str(tmp_path / "fehlt.json"))
    status = backup_status.lies_status()
    assert status["vorhanden"] is False and status["warnung"] is True


def test_web_view_shows_warning(tmp_path, monkeypatch):
    pfad = tmp_path / "status.json"
    monkeypatch.setenv("TCG_BACKUP_STATUS_FILE", str(pfad))
    _schreibe_status(pfad, datetime.now() - timedelta(hours=60))

    from TCGInventory import web
    web.app.config["TESTING"] = True
    client = web.app.test_client()
    with client.session_transaction() as s:
        s["user"] = "tester"

    seite = client.get("/system/backup").get_data(as_text=True)
    assert "Backup prüfen" in seite and "60" in seite

    _schreibe_status(pfad, datetime.now() - timedelta(hours=2))
    seite = client.get("/system/backup").get_data(as_text=True)
    assert "erfolgreich" in seite


# =========================================================================
# Wiederherstellung
# =========================================================================

def test_restore_validates_checksum_and_database(tmp_path):
    cfg = _umgebung(tmp_path)
    assert backup.run_backup(cfg)["ergebnis"] == "erfolg"
    name = backup.LocalStore(Path(cfg.remote)).list_archives()[0]

    ziel = tmp_path / "wiederherstellung"
    ergebnis = restore.restore(name, ziel, cfg)

    assert ergebnis["pruefsumme_ok"] is True
    assert ergebnis["datenbanken"]["mtg_lager.db"] is True
    assert "db/mtg_lager.db" in ergebnis["dateien"]
    assert "data/belege/rechnung.pdf" in ergebnis["dateien"]


def test_restore_leaves_live_directory_untouched(tmp_path):
    cfg = _umgebung(tmp_path)
    backup.run_backup(cfg)
    live = cfg.root / "mtg_lager.db"
    vorher = live.read_bytes()
    beleg_vorher = (cfg.data_dir / "belege" / "rechnung.pdf").read_bytes()

    name = backup.LocalStore(Path(cfg.remote)).list_archives()[0]
    restore.restore(name, tmp_path / "woanders", cfg)

    assert live.read_bytes() == vorher
    assert (cfg.data_dir / "belege" / "rechnung.pdf").read_bytes() == beleg_vorher


def test_restore_refuses_live_directory_as_target(tmp_path):
    cfg = _umgebung(tmp_path)
    backup.run_backup(cfg)
    name = backup.LocalStore(Path(cfg.remote)).list_archives()[0]

    for verbotenes_ziel in (cfg.root, cfg.data_dir):
        with pytest.raises(RuntimeError, match="Live-Verzeichnis"):
            restore.restore(name, verbotenes_ziel, cfg)


def test_restore_detects_manipulated_archive(tmp_path):
    cfg = _umgebung(tmp_path)
    backup.run_backup(cfg)
    name = backup.LocalStore(Path(cfg.remote)).list_archives()[0]
    (Path(cfg.remote) / name).write_bytes(b"manipuliert")     # Archiv verfälscht

    with pytest.raises(RuntimeError, match="Prüfsumme"):
        restore.restore(name, tmp_path / "ziel", cfg)


def test_restore_lists_available_states(tmp_path):
    cfg = _umgebung(tmp_path)
    backup.run_backup(cfg)
    namen = restore.liste_staende(cfg)
    assert len(namen) == 1 and namen[0].startswith(backup.ARCHIV_PREFIX)


# =========================================================================
# Keine Secrets im Repository
# =========================================================================

def test_configuration_comes_from_environment():
    cfg = backup.Config.from_env({
        "TCG_BACKUP_ROOT": "/pfad/app",
        "TCG_BACKUP_REMOTE": "onedrive-crypt:",
        "TCG_RCLONE_CONFIG": "/pfad/rclone.conf",
        "TCG_RCLONE_CONFIG_PASS_FILE": "/pfad/config.pass",
        "TCG_BACKUP_KEEP_DAILY": "5",
    })
    assert cfg.remote == "onedrive-crypt:"
    assert str(cfg.rclone_config) == str(Path("/pfad/rclone.conf"))
    assert str(cfg.rclone_pass_file) == str(Path("/pfad/config.pass"))
    assert cfg.keep_daily == 5
    # Kein Standardwert darf ein Ziel oder eine Zugangsdatei vorgeben.
    leer = backup.Config.from_env({})
    assert leer.remote == "" and leer.rclone_config is None


def test_no_credentials_checked_into_repository():
    verbotene_namen = {"rclone.conf", "credentials.json", "token.json",
                       "token.pickle", "tcginventory-backup.env"}
    verbotene_endungen = {".pass", ".pem", ".key"}
    for pfad in REPO.rglob("*"):
        if not pfad.is_file() or ".git" in pfad.parts or "venv" in pfad.parts:
            continue
        if "data" in pfad.parts:            # data/ ist gitignored (Laufzeitdaten)
            continue
        assert pfad.name not in verbotene_namen, f"Zugangsdaten im Repo: {pfad}"
        assert pfad.suffix not in verbotene_endungen, f"Schlüsseldatei im Repo: {pfad}"


def test_gitignore_covers_backup_secrets():
    inhalt = (REPO / ".gitignore").read_text(encoding="utf-8")
    for muster in ("rclone.conf", "*.pass", "credentials.json", "token.json",
                   "tcginventory-backup.env", "tcginventory_*.tar.gz"):
        assert muster in inhalt, f"{muster} fehlt in .gitignore"


def test_scripts_contain_no_hardcoded_secrets():
    """Kein Skript darf ein Passwort/Token als Literal zuweisen.

    Gesucht wird nach echten Zuweisungen wie ``password = "abc"`` – reine
    Erwähnungen (etwa ``client_secret.json`` in der Ausschlussliste) sind
    ausdrücklich in Ordnung.
    """
    import re
    muster = re.compile(
        r"""(password|passwort|secret|token|api_key)\s*=\s*['"][^'"]+['"]""",
        re.IGNORECASE)
    for name in ("backup.py", "restore.py"):
        quelle = (REPO / "scripts" / name).read_text(encoding="utf-8")
        treffer = muster.findall(quelle)
        assert not treffer, f"{name}: mögliche Zugangsdaten im Code ({treffer})"


# =========================================================================
# Buchhaltung: eigene Anwendung, dieselbe Sicherung
# =========================================================================

def test_buchhaltungsdatenbank_wird_mitgesichert(tmp_path):
    """Die Buchhaltung hat eine eigene Datenbank — sie darf nicht fehlen."""
    cfg = _mit_buchhaltung(tmp_path)
    ergebnis = backup.run_backup(cfg)
    assert ergebnis["ergebnis"] == "erfolg"

    archiv = Path(cfg.remote) / Path(ergebnis["archiv"]).name
    with tarfile.open(archiv) as tar:
        namen = tar.getnames()
    assert "db/buchhaltung.db" in namen
    assert "db/mtg_lager.db" in namen


def test_buchhaltungsbelege_werden_mitgesichert(tmp_path):
    """Die Belege liegen als Dateien; ohne sie waere die Sicherung wertlos."""
    cfg = _mit_buchhaltung(tmp_path)
    ergebnis = backup.run_backup(cfg)

    archiv = Path(cfg.remote) / Path(ergebnis["archiv"]).name
    with tarfile.open(archiv) as tar:
        namen = tar.getnames()
        inhalt = tar.extractfile(
            "buchhaltung/belege/2026/06/20260612_kartons.pdf").read()
    assert "buchhaltung/belege/2026/06/20260612_kartons.pdf" in namen
    assert inhalt == b"%PDF-1.4 Buchhaltungsbeleg"
    # Die Datenbank kommt als geprüfte Kopie, nicht als Rohdatei.
    assert "buchhaltung/buchhaltung.db" not in namen


def test_kaputte_buchhaltungsdatenbank_verhindert_den_upload(tmp_path):
    """Lieber gar keine Sicherung als eine, die sich nicht zurückspielen lässt."""
    cfg = _mit_buchhaltung(tmp_path)
    (cfg.buch_dir / "buchhaltung.db").write_bytes(b"kein SQLite")

    ergebnis = backup.run_backup(cfg)
    assert ergebnis["ergebnis"] == "fehler"
    assert not list(Path(cfg.remote).glob("*.tar.gz"))


def test_ohne_buchhaltung_bleibt_alles_beim_alten(tmp_path):
    """Wer die Buchhaltung nicht einsetzt, merkt von der Erweiterung nichts."""
    cfg = _umgebung(tmp_path)
    assert cfg.buch_dir is None
    ergebnis = backup.run_backup(cfg)
    assert ergebnis["ergebnis"] == "erfolg"

    archiv = Path(cfg.remote) / Path(ergebnis["archiv"]).name
    with tarfile.open(archiv) as tar:
        namen = tar.getnames()
    assert not any(n.startswith("buchhaltung/") for n in namen)


def test_konfiguration_nimmt_die_variable_der_buchhaltung(tmp_path):
    """BUCH_DATA_DIR ist die Variable, die die Buchhaltung selbst benutzt."""
    cfg = backup.Config.from_env({"BUCH_DATA_DIR": str(tmp_path / "daten")})
    assert cfg.buch_dir == tmp_path / "daten"

    # Die ausdrückliche Backup-Variable hat Vorrang.
    cfg = backup.Config.from_env({"BUCH_DATA_DIR": str(tmp_path / "a"),
                                  "TCG_BACKUP_BUCH_DIR": str(tmp_path / "b")})
    assert cfg.buch_dir == tmp_path / "b"

    assert backup.Config.from_env({}).buch_dir is None
