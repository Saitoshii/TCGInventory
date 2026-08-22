"""Die Buchhaltung gehoert in dieselbe Sicherung wie das Inventar.

Sie ist ein eigener Dienst mit eigener Datenbank und eigenen Belegen. Die
Sicherung nimmt dieses Verzeichnis nur mit, wenn ``TCG_BACKUP_BUCH_DIR`` oder
``BUCH_DATA_DIR`` in der Backup-Umgebung steht — fehlt die Variable, wird es
stillschweigend uebersprungen. Ein Lauf gilt dann als erfolgreich, obwohl
ausgerechnet die steuerrelevanten Daten fehlen.

Genau diese Stille wird hier abgestellt.
"""

import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(__file__))))

from TCGInventory import backup_status                      # noqa: E402
from TCGInventory.scripts import backup                     # noqa: E402


def _buchhaltung(pfad):
    """Ein Datenverzeichnis anlegen, wie es die Buchhaltung hinterlaesst."""
    pfad.mkdir(parents=True, exist_ok=True)
    (pfad / "buchhaltung.db").write_bytes(b"SQLite format 3\x00")
    (pfad / "belege").mkdir(exist_ok=True)
    return pfad


def _config(tmp_path, buch_dir=None):
    return backup.Config.from_env({
        "TCG_BACKUP_ROOT": str(tmp_path / "repo"),
        "TCG_BACKUP_DATA_DIR": str(tmp_path / "repo" / "data"),
        **({"TCG_BACKUP_BUCH_DIR": str(buch_dir)} if buch_dir else {}),
    })


# ---------------------------------------------------------------------------
# Erkennen
# ---------------------------------------------------------------------------
def test_warnt_wenn_buchhaltung_im_heim_liegt_aber_fehlt(tmp_path):
    heim = tmp_path / "heim"
    _buchhaltung(heim / "buchhaltung-daten")

    meldungen = backup.warnungen(_config(tmp_path), heim=heim)
    assert len(meldungen) == 1
    assert "NICHT gesichert" in meldungen[0]
    assert "TCG_BACKUP_BUCH_DIR" in meldungen[0]


def test_keine_warnung_wenn_das_verzeichnis_gesichert_wird(tmp_path):
    heim = tmp_path / "heim"
    daten = _buchhaltung(heim / "buchhaltung-daten")

    assert backup.warnungen(_config(tmp_path, buch_dir=daten), heim=heim) == []


def test_keine_warnung_ohne_buchhaltung(tmp_path):
    """Wer sie nicht benutzt, soll nicht behelligt werden."""
    heim = tmp_path / "heim"
    heim.mkdir()
    assert backup.warnungen(_config(tmp_path), heim=heim) == []


def test_leeres_verzeichnis_ohne_datenbank_zaehlt_nicht(tmp_path):
    heim = tmp_path / "heim"
    (heim / "buchhaltung-daten").mkdir(parents=True)
    assert backup.warnungen(_config(tmp_path), heim=heim) == []


# ---------------------------------------------------------------------------
# Env-Datei der Buchhaltung
# ---------------------------------------------------------------------------
def test_env_datei_verraet_das_verzeichnis(tmp_path, monkeypatch):
    """Liegt das Verzeichnis woanders, steht der Pfad in /etc/buchhaltung.env."""
    daten = _buchhaltung(tmp_path / "woanders" / "buchhaltung-daten")
    env = tmp_path / "buchhaltung.env"
    env.write_text(f"BUCH_SECRET_KEY=geheim\nBUCH_DATA_DIR={daten}\n",
                   encoding="utf-8")
    monkeypatch.setattr(backup, "BUCH_ENV_DATEI", env)

    meldungen = backup.warnungen(_config(tmp_path), heim=tmp_path / "leer")
    assert len(meldungen) == 1
    assert str(daten) in meldungen[0]


def test_aus_der_env_datei_kommt_nichts_geheimes_in_die_meldung(tmp_path, monkeypatch):
    daten = _buchhaltung(tmp_path / "woanders" / "buchhaltung-daten")
    env = tmp_path / "buchhaltung.env"
    env.write_text(
        "BUCH_SECRET_KEY=streng-geheim-nicht-ausgeben\n"
        f"BUCH_DATA_DIR={daten}\n"
        "BUCH_INVENTAR_TOKEN=auch-geheim\n", encoding="utf-8")
    monkeypatch.setattr(backup, "BUCH_ENV_DATEI", env)

    text = " ".join(backup.warnungen(_config(tmp_path), heim=tmp_path / "leer"))
    assert "streng-geheim-nicht-ausgeben" not in text
    assert "auch-geheim" not in text


def test_unlesbare_env_datei_ist_kein_fehler(tmp_path, monkeypatch):
    monkeypatch.setattr(backup, "BUCH_ENV_DATEI", tmp_path / "gibtsnicht.env")
    assert backup._buch_dir_aus_env() is None


# ---------------------------------------------------------------------------
# Weg bis in die Anzeige
# ---------------------------------------------------------------------------
def test_warnung_steht_im_statusbericht_und_wird_angezeigt(tmp_path, monkeypatch):
    """Vom Lauf bis auf die Backup-Seite — sonst sieht es niemand."""
    bericht = tmp_path / "backup_status.json"
    bericht.write_text(json.dumps({
        "zeitpunkt": "2026-08-22T09:00:00",
        "ergebnis": "erfolg",
        "archiv": "a.tar.gz",
        "groesse_bytes": 1024,
        "sha256": "abc",
        "dauer_sekunden": 3,
        "meldung": "Backup erfolgreich.",
        "warnungen": ["Die Buchhaltung unter /home/x/buchhaltung-daten "
                      "wird NICHT gesichert."],
    }), encoding="utf-8")
    monkeypatch.setattr(backup_status, "status_pfad", lambda: bericht)

    status = backup_status.lies_status()
    assert status["ergebnis"] == "erfolg"
    assert len(status["warnungen"]) == 1
    assert "NICHT gesichert" in status["warnungen"][0]


def test_alter_bericht_ohne_warnungen_bleibt_lesbar(tmp_path, monkeypatch):
    """Berichte von vor dieser Änderung dürfen nicht zum Fehler führen."""
    bericht = tmp_path / "backup_status.json"
    bericht.write_text(json.dumps({
        "zeitpunkt": "2026-08-22T09:00:00", "ergebnis": "erfolg",
    }), encoding="utf-8")
    monkeypatch.setattr(backup_status, "status_pfad", lambda: bericht)

    assert backup_status.lies_status()["warnungen"] == []


# ---------------------------------------------------------------------------
# Verdrahtung: der Lauf selbst muss die Warnung mitschreiben
# ---------------------------------------------------------------------------
def _lauffaehige_config(tmp_path):
    """Eine Konfiguration, mit der run_backup wirklich durchlaeuft."""
    import sqlite3
    root = tmp_path / "app"
    data = root / "data"
    data.mkdir(parents=True)
    conn = sqlite3.connect(str(root / "mtg_lager.db"))
    conn.execute("CREATE TABLE cards (id INTEGER PRIMARY KEY, name TEXT)")
    conn.execute("INSERT INTO cards (name) VALUES ('Sol Ring')")
    conn.commit()
    conn.close()
    return backup.Config(
        root=root, data_dir=data,
        work_dir=tmp_path / "work",
        status_file=tmp_path / "status.json",
        log_file=tmp_path / "backup.log",
        remote=str(tmp_path / "remote"),
    )


def test_lauf_schreibt_die_warnung_in_status_und_log(tmp_path, monkeypatch):
    """Ohne diese Prüfung faellt es nicht auf, wenn run_backup die Warnung
    gar nicht erst uebernimmt — die Einzelteile waeren trotzdem grün."""
    heim = tmp_path / "heim"
    _buchhaltung(heim / "buchhaltung-daten")
    monkeypatch.setattr(backup.Path, "home", staticmethod(lambda: heim))
    monkeypatch.setattr(backup, "BUCH_ENV_DATEI", tmp_path / "gibtsnicht.env")

    cfg = _lauffaehige_config(tmp_path)
    status = backup.run_backup(cfg)

    assert status["ergebnis"] == "erfolg", status["meldung"]
    assert status["warnungen"], "der Lauf gilt als erfolgreich, warnt aber nicht"
    assert "NICHT gesichert" in status["warnungen"][0]

    im_bericht = json.loads(cfg.status_file.read_text(encoding="utf-8"))
    assert im_bericht["warnungen"] == status["warnungen"]
    assert "WARNUNG" in cfg.log_file.read_text(encoding="utf-8")


def test_lauf_ohne_buchhaltung_warnt_nicht(tmp_path, monkeypatch):
    heim = tmp_path / "heim"
    heim.mkdir()
    monkeypatch.setattr(backup.Path, "home", staticmethod(lambda: heim))
    monkeypatch.setattr(backup, "BUCH_ENV_DATEI", tmp_path / "gibtsnicht.env")

    status = backup.run_backup(_lauffaehige_config(tmp_path))
    assert status["ergebnis"] == "erfolg", status["meldung"]
    assert status["warnungen"] == []
