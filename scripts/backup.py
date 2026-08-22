#!/usr/bin/env python3
"""Verschlüsseltes Backup der geschäftskritischen Daten (WP4a).

Ablauf eines Laufs:

1. SQLite-Datenbanken **sicher** kopieren (``VACUUM INTO``, ersatzweise die
   Backup-API) — ein einfaches ``cp`` wäre bei laufender Anwendung unzulässig.
2. Kopien mit ``PRAGMA integrity_check`` prüfen. Schlägt das fehl, bricht der
   Lauf ab und es wird **nichts** hochgeladen.
3. Kopien, Belege und Konfiguration (ohne Secrets) in ein ``tar.gz`` packen.
4. SHA-256 des Archivs bilden und als Begleitdatei ablegen.
5. Beides in den Remote hochladen. Bei einem rclone-``crypt``-Remote passiert
   die Verschlüsselung auf dem Pi — der Cloud-Anbieter sieht nie Klartext,
   Datei- und Ordnernamen inklusive.
6. Lokale Zwischendateien aufräumen.
7. Aufbewahrung anwenden (7 Tagesstände + 12 Monatsstände).
8. Ergebnis in Logdatei und Status-JSON schreiben (nie stiller Fehlschlag).

Konfiguration ausschließlich über Umgebungsvariablen — es liegen **keine**
Zugangsdaten im Repository. Siehe ``docs/BACKUP.md``.
"""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import sqlite3
import subprocess
import sys
import tarfile
import time
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Dict, Iterable, List, Optional, Sequence

ARCHIV_PREFIX = "tcginventory_"
ARCHIV_SUFFIX = ".tar.gz"
ZEITFORMAT = "%Y-%m-%d_%H%M"

# Diese Datenbanken werden bewusst nicht gesichert: die Scryfall-Bulkdaten sind
# jederzeit neu erzeugbar (build_card_db.py) und nur unnötig groß.
DB_AUSGESCHLOSSEN = {"default-cards.db", "default-card.db"}
# Nie mitsichern: Zugangsdaten gehören nicht ins Backup-Archiv.
SECRET_NAMEN = {
    "credentials.json", "token.json", "token.pickle", "rclone.conf",
    "client_secret.json", ".env",
}
SECRET_ENDUNGEN = {".pem", ".key", ".pass"}


# ---------------------------------------------------------------------------
# Konfiguration
# ---------------------------------------------------------------------------
def _repo_root() -> Path:
    return Path(__file__).resolve().parent.parent


@dataclass
class Config:
    """Alle Pfade und Optionen; ausschließlich aus Umgebungsvariablen."""

    root: Path = field(default_factory=_repo_root)
    data_dir: Path = None            # type: ignore[assignment]
    # Datenverzeichnis der eigenstaendigen Buchhaltung. Sie hat eine eigene
    # Datenbank und eigene Belege; beides ist steuerrelevant und gehoert in
    # dieselbe Sicherung. Ein zweiter Sicherungslauf waere ein zweiter Ort,
    # an dem etwas vergessen werden kann.
    buch_dir: Optional[Path] = None
    work_dir: Path = None            # type: ignore[assignment]
    status_file: Path = None         # type: ignore[assignment]
    log_file: Path = None            # type: ignore[assignment]
    remote: str = ""
    rclone_bin: str = "rclone"
    rclone_config: Optional[Path] = None
    rclone_pass_file: Optional[Path] = None
    keep_daily: int = 7
    keep_monthly: int = 12

    @classmethod
    def from_env(cls, env: Optional[Dict[str, str]] = None) -> "Config":
        env = dict(os.environ if env is None else env)
        root = Path(env.get("TCG_BACKUP_ROOT") or _repo_root())
        data_dir = Path(env.get("TCG_BACKUP_DATA_DIR") or (root / "data"))

        def _opt(name: str) -> Optional[Path]:
            value = env.get(name)
            return Path(value) if value else None

        # Bevorzugt die Variable, die die Buchhaltung selbst benutzt — so
        # zeigen beide Systeme zwangslaeufig auf dasselbe Verzeichnis.
        buch = env.get("TCG_BACKUP_BUCH_DIR") or env.get("BUCH_DATA_DIR")
        return cls(
            root=root,
            data_dir=data_dir,
            buch_dir=Path(buch) if buch else None,
            work_dir=Path(env.get("TCG_BACKUP_WORK_DIR") or (data_dir / "backup_work")),
            status_file=Path(env.get("TCG_BACKUP_STATUS_FILE")
                             or (data_dir / "backup_status.json")),
            log_file=Path(env.get("TCG_BACKUP_LOG_FILE") or (data_dir / "backup.log")),
            remote=env.get("TCG_BACKUP_REMOTE", ""),
            rclone_bin=env.get("TCG_RCLONE_BIN", "rclone"),
            rclone_config=_opt("TCG_RCLONE_CONFIG"),
            rclone_pass_file=_opt("TCG_RCLONE_CONFIG_PASS_FILE"),
            keep_daily=int(env.get("TCG_BACKUP_KEEP_DAILY", "7")),
            keep_monthly=int(env.get("TCG_BACKUP_KEEP_MONTHLY", "12")),
        )


# ---------------------------------------------------------------------------
# Remote-Ablage
# ---------------------------------------------------------------------------
class RemoteStore:
    """Schnittstelle zur Ablage. Der Upload ist gekapselt, damit die Logik
    (Kopie, Prüfsumme, Retention, Status) ohne Cloud testbar bleibt."""

    def upload(self, pfad: Path) -> None:
        raise NotImplementedError

    def list_archives(self) -> List[str]:
        raise NotImplementedError

    def delete(self, name: str) -> None:
        raise NotImplementedError


class LocalStore(RemoteStore):
    """Ablage in einem lokalen Verzeichnis — für Tests und für eine zusätzliche
    Sicherung auf USB-Datenträger."""

    def __init__(self, ziel: Path):
        self.ziel = Path(ziel)
        self.ziel.mkdir(parents=True, exist_ok=True)

    def upload(self, pfad: Path) -> None:
        shutil.copy2(pfad, self.ziel / pfad.name)

    def list_archives(self) -> List[str]:
        return sorted(p.name for p in self.ziel.glob(f"{ARCHIV_PREFIX}*{ARCHIV_SUFFIX}"))

    def delete(self, name: str) -> None:
        for kandidat in (self.ziel / name, self.ziel / f"{name}.sha256"):
            if kandidat.exists():
                kandidat.unlink()


class RcloneStore(RemoteStore):
    """Ablage über rclone. Auf ein ``crypt``-Remote gerichtet, sodass die
    Verschlüsselung auf dem Pi geschieht (inklusive Datei-/Ordnernamen)."""

    def __init__(self, cfg: Config):
        self.cfg = cfg

    def _basis_kommando(self) -> List[str]:
        cmd = [self.cfg.rclone_bin]
        if self.cfg.rclone_config:
            cmd += ["--config", str(self.cfg.rclone_config)]
        if self.cfg.rclone_pass_file:
            cmd += ["--password-command", f"cat {self.cfg.rclone_pass_file}"]
        return cmd

    def _run(self, args: Sequence[str]) -> str:
        proc = subprocess.run(self._basis_kommando() + list(args),
                              capture_output=True, text=True)
        if proc.returncode != 0:
            raise RuntimeError(
                f"rclone {' '.join(args)} fehlgeschlagen: "
                f"{(proc.stderr or proc.stdout).strip()}")
        return proc.stdout

    def upload(self, pfad: Path) -> None:
        self._run(["copy", str(pfad), self.cfg.remote])

    def list_archives(self) -> List[str]:
        ausgabe = self._run(["lsf", self.cfg.remote])
        return sorted(z.strip() for z in ausgabe.splitlines()
                      if z.strip().startswith(ARCHIV_PREFIX)
                      and z.strip().endswith(ARCHIV_SUFFIX))

    def delete(self, name: str) -> None:
        self._run(["deletefile", f"{self.cfg.remote}/{name}"])
        try:
            self._run(["deletefile", f"{self.cfg.remote}/{name}.sha256"])
        except RuntimeError:
            pass          # Begleitdatei fehlt — kein Grund zum Abbruch


def make_store(cfg: Config) -> RemoteStore:
    """Lokales Verzeichnis, wenn das Ziel kein ``remote:``-Präfix hat."""
    if not cfg.remote:
        raise RuntimeError("Kein Backup-Ziel gesetzt (TCG_BACKUP_REMOTE).")
    ohne_laufwerk = cfg.remote[2:] if len(cfg.remote) > 2 and cfg.remote[1] == ":" else cfg.remote
    return RcloneStore(cfg) if ":" in ohne_laufwerk else LocalStore(Path(cfg.remote))


# ---------------------------------------------------------------------------
# Schritt 1+2: SQLite sicher kopieren und prüfen
# ---------------------------------------------------------------------------
def finde_datenbanken(cfg: Config) -> List[Path]:
    """Alle zu sichernden SQLite-Dateien.

    Aus ``data/``, aus dem Projektwurzelverzeichnis (dort liegt
    ``mtg_lager.db`` standardmäßig) und aus dem Datenverzeichnis der
    Buchhaltung, falls eines konfiguriert ist.
    """
    gefunden: List[Path] = []
    verzeichnisse = [cfg.data_dir, cfg.root]
    if cfg.buch_dir:
        verzeichnisse.append(cfg.buch_dir)
    for verzeichnis in verzeichnisse:
        if not verzeichnis.is_dir():
            continue
        for pfad in sorted(verzeichnis.glob("*.db")):
            if pfad.name in DB_AUSGESCHLOSSEN or pfad.name.startswith("test"):
                continue
            if pfad not in gefunden:
                gefunden.append(pfad)
    return gefunden


def sqlite_sichere_kopie(quelle: Path, ziel: Path) -> None:
    """Konsistente Kopie einer SQLite-Datei, auch bei laufender Anwendung.

    Bevorzugt ``VACUUM INTO`` (kompakt und atomar); ältere SQLite-Versionen
    bekommen die Backup-API. Ein einfaches Kopieren wäre nicht zulässig, weil
    mitten in einer Transaktion ein unbrauchbarer Stand entstehen kann.
    """
    ziel.parent.mkdir(parents=True, exist_ok=True)
    if ziel.exists():
        ziel.unlink()
    conn = sqlite3.connect(f"file:{quelle}?mode=ro", uri=True)
    try:
        try:
            conn.execute("VACUUM INTO ?", (str(ziel),))
        except sqlite3.OperationalError:
            ziel_conn = sqlite3.connect(str(ziel))
            try:
                conn.backup(ziel_conn)
            finally:
                ziel_conn.close()
    finally:
        conn.close()


def integritaet_ok(pfad: Path) -> bool:
    """``PRAGMA integrity_check`` auf der erzeugten Kopie."""
    try:
        conn = sqlite3.connect(str(pfad))
        try:
            ergebnis = conn.execute("PRAGMA integrity_check").fetchone()
        finally:
            conn.close()
    except sqlite3.DatabaseError:
        return False
    return bool(ergebnis) and ergebnis[0] == "ok"


# ---------------------------------------------------------------------------
# Schritt 3+4: Archiv und Prüfsumme
# ---------------------------------------------------------------------------
def _ist_secret(pfad: Path) -> bool:
    return pfad.name in SECRET_NAMEN or pfad.suffix.lower() in SECRET_ENDUNGEN


def baue_archiv(archiv: Path, db_kopien: Iterable[Path], cfg: Config) -> Path:
    """Datenbankkopien, Belege und Konfiguration (ohne Secrets) packen."""
    archiv.parent.mkdir(parents=True, exist_ok=True)
    ausgeschlossen = {cfg.work_dir.resolve(), cfg.status_file.resolve(),
                      cfg.log_file.resolve()}

    def filter_fn(info: tarfile.TarInfo) -> Optional[tarfile.TarInfo]:
        if _ist_secret(Path(info.name)):
            return None
        return info

    with tarfile.open(archiv, "w:gz") as tar:
        for kopie in db_kopien:
            tar.add(kopie, arcname=f"db/{kopie.name}")
        if cfg.data_dir.is_dir():
            for pfad in sorted(cfg.data_dir.rglob("*")):
                if not pfad.is_file() or _ist_secret(pfad):
                    continue
                aufgeloest = pfad.resolve()
                if any(aufgeloest == a or a in aufgeloest.parents for a in ausgeschlossen):
                    continue
                if pfad.suffix == ".db":
                    continue          # Datenbanken kommen als geprüfte Kopie mit
                tar.add(pfad, arcname=f"data/{pfad.relative_to(cfg.data_dir)}",
                        filter=filter_fn)
        # Die Belege der Buchhaltung sind Nachweise zu Geschaeftsvorfaellen
        # und liegen als Dateien, nicht in der Datenbank. Ohne sie waere die
        # Sicherung unvollstaendig.
        if cfg.buch_dir and cfg.buch_dir.is_dir():
            for pfad in sorted(cfg.buch_dir.rglob("*")):
                if not pfad.is_file() or _ist_secret(pfad):
                    continue
                if pfad.suffix in (".db", ".db-wal", ".db-shm"):
                    continue      # kommt als geprüfte Kopie mit
                tar.add(pfad,
                        arcname=f"buchhaltung/{pfad.relative_to(cfg.buch_dir)}",
                        filter=filter_fn)
    return archiv


def sha256_datei(pfad: Path) -> str:
    h = hashlib.sha256()
    with open(pfad, "rb") as f:
        for block in iter(lambda: f.read(1024 * 1024), b""):
            h.update(block)
    return h.hexdigest()


# ---------------------------------------------------------------------------
# Schritt 7: Aufbewahrung
# ---------------------------------------------------------------------------
def archiv_zeitpunkt(name: str) -> Optional[datetime]:
    """``tcginventory_2026-05-04_0330.tar.gz`` -> ``datetime``."""
    if not (name.startswith(ARCHIV_PREFIX) and name.endswith(ARCHIV_SUFFIX)):
        return None
    kern = name[len(ARCHIV_PREFIX):-len(ARCHIV_SUFFIX)]
    try:
        return datetime.strptime(kern, ZEITFORMAT)
    except ValueError:
        return None


def retention_plan(namen: Sequence[str], keep_daily: int = 7,
                   keep_monthly: int = 12) -> Dict[str, List[str]]:
    """Entscheidet, welche Stände bleiben und welche entfernt werden.

    Behalten werden:
      * die ``keep_daily`` **neuesten** Stände (nicht die letzten sieben
        Kalendertage — so bleiben auch bei Ausfalltagen sieben Stände erhalten),
      * zusätzlich der **erste** Stand jedes Monats für die ``keep_monthly``
        jüngsten Monate, in denen es überhaupt einen Stand gibt.

    Dadurch überlebt der Monatsstand die Tagesrotation, was für die
    Aufbewahrungspflicht von Geschäftsunterlagen nötig ist.
    """
    datiert = [(n, t) for n in namen if (t := archiv_zeitpunkt(n)) is not None]
    datiert.sort(key=lambda x: (x[1], x[0]))

    behalten = set()
    for name, _ in datiert[-keep_daily:] if keep_daily > 0 else []:
        behalten.add(name)

    erster_im_monat: Dict[str, str] = {}
    for name, zeit in datiert:                    # aufsteigend -> erster gewinnt
        erster_im_monat.setdefault(zeit.strftime("%Y-%m"), name)
    for monat in sorted(erster_im_monat, reverse=True)[:keep_monthly]:
        behalten.add(erster_im_monat[monat])

    loeschen = [n for n, _ in datiert if n not in behalten]
    return {"behalten": sorted(behalten), "loeschen": loeschen}


def wende_retention_an(store: RemoteStore, cfg: Config) -> List[str]:
    plan = retention_plan(store.list_archives(), cfg.keep_daily, cfg.keep_monthly)
    for name in plan["loeschen"]:
        store.delete(name)
    return plan["loeschen"]


# ---------------------------------------------------------------------------
# Schritt 8: Protokoll und Status
# ---------------------------------------------------------------------------
def schreibe_log(cfg: Config, zeile: str) -> None:
    try:
        cfg.log_file.parent.mkdir(parents=True, exist_ok=True)
        with open(cfg.log_file, "a", encoding="utf-8") as f:
            f.write(f"{datetime.now().isoformat(timespec='seconds')} {zeile}\n")
    except OSError:
        pass          # Ein nicht schreibbares Log darf das Backup nicht stoppen


#: Von der Buchhaltung angelegt; enthaelt auch Geheimnisse — gelesen wird
#: ausschliesslich die Zeile mit dem Datenverzeichnis.
BUCH_ENV_DATEI = Path("/etc/buchhaltung.env")

#: Uebliches Datenverzeichnis, wenn scripts/einrichten.sh der Buchhaltung
#: ohne eigene Vorgabe gelaufen ist.
BUCH_STANDARD = "buchhaltung-daten"


def _buch_dir_aus_env(env_datei: Optional[Path] = None) -> Optional[Path]:
    """``BUCH_DATA_DIR`` aus der Env-Datei der Buchhaltung lesen.

    Nur diese eine Zeile wird ausgewertet. In derselben Datei stehen
    Sitzungsschluessel und Zugangstoken; die haben hier nichts zu suchen und
    landen weder im Status noch im Log.
    """
    datei = env_datei or BUCH_ENV_DATEI
    try:
        text = datei.read_text(encoding="utf-8")
    except OSError:
        return None                    # nicht lesbar ist kein Fehler
    for zeile in text.splitlines():
        zeile = zeile.strip()
        if zeile.startswith("BUCH_DATA_DIR="):
            wert = zeile.split("=", 1)[1].strip().strip("\"'")
            if wert:
                return Path(wert)
    return None


def finde_buchhaltung(cfg: Config, heim: Optional[Path] = None) -> Optional[Path]:
    """Ein Datenverzeichnis der Buchhaltung, das **nicht** gesichert wird.

    Die Buchhaltung ist ein eigener Dienst mit eigener Datenbank. Ist sie
    eingerichtet, aber ``TCG_BACKUP_BUCH_DIR``/``BUCH_DATA_DIR`` fehlt in der
    Backup-Umgebung, ueberspringt der Lauf sie stillschweigend — und das sind
    ausgerechnet die steuerrelevanten Daten. Lieber einmal zu viel warnen als
    ein Jahr lang das Falsche sichern.

    Gibt ``None`` zurueck, wenn nichts gefunden wird oder das Gefundene
    ohnehin schon in der Sicherung liegt.
    """
    kandidaten = []
    aus_env = _buch_dir_aus_env()
    if aus_env:
        kandidaten.append(aus_env)
    kandidaten.append(Path(heim or Path.home()) / BUCH_STANDARD)

    gesichert = cfg.buch_dir.resolve() if cfg.buch_dir else None
    for kandidat in kandidaten:
        if not (kandidat / "buchhaltung.db").is_file():
            continue
        try:
            if gesichert and kandidat.resolve() == gesichert:
                return None            # liegt bereits in der Sicherung
        except OSError:
            pass
        return kandidat
    return None


def warnungen(cfg: Config, heim: Optional[Path] = None) -> List[str]:
    """Was am Lauf zwar gelingt, aber trotzdem falsch ist."""
    offen = finde_buchhaltung(cfg, heim=heim)
    if not offen:
        return []
    return [
        f"Die Buchhaltung unter {offen} wird NICHT gesichert. "
        f"Dort liegen Buchungen und Belege. Abhilfe: TCG_BACKUP_BUCH_DIR={offen} "
        f"in die Backup-Umgebung eintragen und den naechsten Lauf pruefen."
    ]


def schreibe_status(cfg: Config, status: Dict) -> None:
    cfg.status_file.parent.mkdir(parents=True, exist_ok=True)
    tmp = cfg.status_file.with_suffix(".tmp")
    tmp.write_text(json.dumps(status, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(cfg.status_file)
    try:
        os.chmod(cfg.status_file, 0o600)
    except OSError:
        pass


# ---------------------------------------------------------------------------
# Hauptlauf
# ---------------------------------------------------------------------------
def run_backup(cfg: Optional[Config] = None, store: Optional[RemoteStore] = None) -> Dict:
    """Einen vollständigen Backup-Lauf ausführen und den Status zurückgeben."""
    cfg = cfg or Config.from_env()
    begonnen = time.time()
    zeitstempel = datetime.now().strftime(ZEITFORMAT)
    status: Dict = {
        "zeitpunkt": datetime.now().isoformat(timespec="seconds"),
        "ergebnis": "fehler",
        "archiv": None,
        "groesse_bytes": 0,
        "sha256": None,
        "dauer_sekunden": 0,
        "entfernt": [],
        "meldung": "",
        # Ein Lauf kann gelingen und trotzdem das Falsche sichern.
        "warnungen": warnungen(cfg),
    }
    arbeit = cfg.work_dir / zeitstempel
    try:
        store = store or make_store(cfg)

        # 1+2: sichere Kopien und Integritätsprüfung
        quellen = finde_datenbanken(cfg)
        if not quellen:
            raise RuntimeError(f"Keine Datenbank in {cfg.data_dir} oder {cfg.root} gefunden.")
        arbeit.mkdir(parents=True, exist_ok=True)
        try:
            os.chmod(cfg.work_dir, 0o700)
        except OSError:
            pass

        kopien: List[Path] = []
        for quelle in quellen:
            ziel = arbeit / quelle.name
            try:
                sqlite_sichere_kopie(quelle, ziel)
            except sqlite3.DatabaseError as exc:
                raise RuntimeError(f"{quelle.name}: Kopie fehlgeschlagen ({exc})") from exc
            if not integritaet_ok(ziel):
                raise RuntimeError(
                    f"{quelle.name}: integrity_check fehlgeschlagen – kein Upload.")
            kopien.append(ziel)

        # 3+4: Archiv und Prüfsumme
        archiv = cfg.work_dir / f"{ARCHIV_PREFIX}{zeitstempel}{ARCHIV_SUFFIX}"
        baue_archiv(archiv, kopien, cfg)
        pruefsumme = sha256_datei(archiv)
        summendatei = archiv.with_name(archiv.name + ".sha256")
        summendatei.write_text(f"{pruefsumme}  {archiv.name}\n", encoding="utf-8")
        for datei in (archiv, summendatei):
            try:
                os.chmod(datei, 0o600)
            except OSError:
                pass

        # 5: Upload (bei crypt-Remote verschlüsselt der Pi vor dem Senden)
        store.upload(archiv)
        store.upload(summendatei)

        status.update({
            "archiv": archiv.name,
            "groesse_bytes": archiv.stat().st_size,
            "sha256": pruefsumme,
            "datenbanken": [q.name for q in quellen],
        })

        # 6: aufräumen
        shutil.rmtree(arbeit, ignore_errors=True)
        archiv.unlink(missing_ok=True)
        summendatei.unlink(missing_ok=True)

        # 7: Aufbewahrung
        status["entfernt"] = wende_retention_an(store, cfg)
        status["ergebnis"] = "erfolg"
        status["meldung"] = "Backup erfolgreich."
    except Exception as exc:                       # noqa: BLE001 – nie still scheitern
        status["meldung"] = str(exc)
        shutil.rmtree(arbeit, ignore_errors=True)
    finally:
        status["dauer_sekunden"] = round(time.time() - begonnen, 1)
        schreibe_status(cfg, status)
        for warnung in status.get("warnungen", []):
            schreibe_log(cfg, f"WARNUNG {warnung}")
        schreibe_log(
            cfg,
            f"{status['ergebnis'].upper()} archiv={status['archiv']} "
            f"groesse={status['groesse_bytes']} dauer={status['dauer_sekunden']}s "
            f"sha256={status['sha256']} {status['meldung']}".strip(),
        )
    return status


def main(argv: Optional[Sequence[str]] = None) -> int:
    status = run_backup()
    if status["ergebnis"] == "erfolg":
        print(f"Backup erfolgreich: {status['archiv']} "
              f"({status['groesse_bytes'] / 1024 / 1024:.1f} MB)")
        return 0
    print(f"Backup FEHLGESCHLAGEN: {status['meldung']}", file=sys.stderr)
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
