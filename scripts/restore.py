#!/usr/bin/env python3
"""Wiederherstellung eines Sicherungsstandes (WP4a).

Grundsatz: **niemals** direkt ins Live-Verzeichnis zurückspielen. Der Stand wird
in ein separates Zielverzeichnis entpackt und dort geprüft; das Produktivsetzen
bleibt ein bewusster, manueller Schritt (Dienst stoppen, Dateien tauschen,
Dienst starten).

Aufrufe::

    python scripts/restore.py liste
    python scripts/restore.py hole tcginventory_2026-05-04_0330.tar.gz --ziel /tmp/restore
"""

from __future__ import annotations

import argparse
import shutil
import subprocess
import sys
import tarfile
from pathlib import Path
from typing import Dict, List, Optional, Sequence

try:                                   # als Modul (python -m TCGInventory.scripts.restore)
    from . import backup
except ImportError:                    # als Skript (python scripts/restore.py)
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    import backup                      # type: ignore[no-redef]


def liste_staende(cfg: Optional[backup.Config] = None,
                  store: Optional[backup.RemoteStore] = None) -> List[str]:
    """Verfügbare Sicherungsstände, neueste zuletzt."""
    cfg = cfg or backup.Config.from_env()
    store = store or backup.make_store(cfg)
    namen = store.list_archives()
    return sorted(namen, key=lambda n: (backup.archiv_zeitpunkt(n) or n, n))


def _hole_datei(cfg: backup.Config, store: backup.RemoteStore,
                name: str, ziel_dir: Path) -> Path:
    """Eine Datei aus dem Remote holen (rclone entschlüsselt dabei automatisch)."""
    ziel = ziel_dir / name
    if isinstance(store, backup.LocalStore):
        quelle = store.ziel / name
        if not quelle.exists():
            raise FileNotFoundError(f"{name} nicht im Ziel gefunden")
        shutil.copy2(quelle, ziel)
        return ziel
    cmd = [cfg.rclone_bin]
    if cfg.rclone_config:
        cmd += ["--config", str(cfg.rclone_config)]
    if cfg.rclone_pass_file:
        cmd += ["--password-command", f"cat {cfg.rclone_pass_file}"]
    cmd += ["copy", f"{cfg.remote}/{name}", str(ziel_dir)]
    proc = subprocess.run(cmd, capture_output=True, text=True)
    if proc.returncode != 0:
        raise RuntimeError(f"Download fehlgeschlagen: {(proc.stderr or proc.stdout).strip()}")
    if not ziel.exists():
        raise FileNotFoundError(f"{name} wurde nicht heruntergeladen")
    return ziel


def _sichere_entpackung(tar: tarfile.TarFile, ziel: Path) -> None:
    """Entpacken mit Pfadprüfung — kein Ausbrechen aus dem Zielverzeichnis."""
    basis = ziel.resolve()
    for mitglied in tar.getmembers():
        pfad = (basis / mitglied.name).resolve()
        if basis != pfad and basis not in pfad.parents:
            raise RuntimeError(f"Unsicherer Pfad im Archiv: {mitglied.name}")
    try:                       # Python 3.12+: zusätzlich der sichere Standardfilter
        tar.extractall(ziel, filter="data")
    except TypeError:
        tar.extractall(ziel)


def restore(name: str, ziel: Path, cfg: Optional[backup.Config] = None,
            store: Optional[backup.RemoteStore] = None) -> Dict:
    """Stand holen, Prüfsumme validieren, entpacken und Datenbanken prüfen.

    Das Live-Verzeichnis wird dabei nicht berührt.
    """
    cfg = cfg or backup.Config.from_env()
    store = store or backup.make_store(cfg)
    ziel = Path(ziel)
    if ziel.resolve() in (cfg.root.resolve(), cfg.data_dir.resolve()):
        raise RuntimeError(
            "Zielverzeichnis darf nicht das Live-Verzeichnis sein – bitte einen "
            "separaten Ordner angeben.")
    ziel.mkdir(parents=True, exist_ok=True)

    ergebnis: Dict = {"archiv": name, "ziel": str(ziel), "pruefsumme_ok": False,
                      "datenbanken": {}, "dateien": []}

    archiv = _hole_datei(cfg, store, name, ziel)

    # Prüfsumme: fehlt die Begleitdatei, wird das ausdrücklich gemeldet.
    try:
        summendatei = _hole_datei(cfg, store, f"{name}.sha256", ziel)
        erwartet = summendatei.read_text(encoding="utf-8").split()[0]
        tatsaechlich = backup.sha256_datei(archiv)
        if erwartet != tatsaechlich:
            raise RuntimeError(
                f"Prüfsumme stimmt nicht! erwartet {erwartet}, berechnet {tatsaechlich}")
        ergebnis["pruefsumme_ok"] = True
    except FileNotFoundError:
        ergebnis["pruefsumme_ok"] = None          # keine Begleitdatei vorhanden

    entpackt = ziel / "inhalt"
    entpackt.mkdir(parents=True, exist_ok=True)
    with tarfile.open(archiv, "r:gz") as tar:
        _sichere_entpackung(tar, entpackt)
    # Immer mit "/" melden – Archivpfade sind plattformunabhängig.
    ergebnis["dateien"] = sorted(
        p.relative_to(entpackt).as_posix() for p in entpackt.rglob("*") if p.is_file())

    for db in sorted((entpackt / "db").glob("*.db")) if (entpackt / "db").is_dir() else []:
        ergebnis["datenbanken"][db.name] = backup.integritaet_ok(db)

    ergebnis["entpackt_nach"] = str(entpackt)
    return ergebnis


def _naechste_schritte(ergebnis: Dict) -> str:
    entpackt = ergebnis.get("entpackt_nach", "<Zielverzeichnis>")
    return (
        "\nManuelle Schritte zum Produktivsetzen (bewusst nicht automatisiert):\n"
        "  1. sudo systemctl stop tcginventory.service\n"
        f"  2. Aktuelle Datei zur Sicherheit beiseitelegen:\n"
        f"     mv ~/TCGInventory/mtg_lager.db ~/mtg_lager.db.vor-restore\n"
        f"  3. Stand einspielen:\n"
        f"     cp {entpackt}/db/mtg_lager.db ~/TCGInventory/mtg_lager.db\n"
        f"     cp -r {entpackt}/data/belege ~/TCGInventory/data/\n"
        "  4. sudo systemctl start tcginventory.service\n"
        "  5. In der Weboberfläche stichprobenartig prüfen (Bestand, Bestellungen,\n"
        "     Buchungsjournal).\n"
        "\nHinweis: default-cards.db wird bewusst nicht gesichert. Sie wird bei\n"
        "Bedarf mit build_card_db.py neu aus den Scryfall-Bulkdaten erzeugt.\n"
    )


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="Sicherungsstand wiederherstellen")
    unter = parser.add_subparsers(dest="befehl", required=True)
    unter.add_parser("liste", help="verfügbare Sicherungsstände anzeigen")
    p_hole = unter.add_parser("hole", help="Stand holen, prüfen und entpacken")
    p_hole.add_argument("archiv", help="Dateiname des Standes")
    p_hole.add_argument("--ziel", required=True,
                        help="Zielverzeichnis (NICHT das Live-Verzeichnis)")
    args = parser.parse_args(argv)

    cfg = backup.Config.from_env()
    try:
        if args.befehl == "liste":
            namen = liste_staende(cfg)
            if not namen:
                print("Keine Sicherungsstände gefunden.")
                return 1
            print("Verfügbare Sicherungsstände (älteste zuerst):")
            for name in namen:
                zeit = backup.archiv_zeitpunkt(name)
                print(f"  {name}   {zeit.strftime('%d.%m.%Y %H:%M') if zeit else ''}")
            return 0

        ergebnis = restore(args.archiv, Path(args.ziel), cfg)
    except Exception as exc:                        # noqa: BLE001
        print(f"Wiederherstellung fehlgeschlagen: {exc}", file=sys.stderr)
        return 1

    if ergebnis["pruefsumme_ok"] is True:
        print("Prüfsumme (SHA-256): OK")
    elif ergebnis["pruefsumme_ok"] is None:
        print("Prüfsumme: keine Begleitdatei gefunden – bitte prüfen!")
    for name, ok in ergebnis["datenbanken"].items():
        print(f"integrity_check {name}: {'OK' if ok else 'FEHLGESCHLAGEN'}")
    print(f"\nEntpackt nach: {ergebnis['entpackt_nach']}")
    print(_naechste_schritte(ergebnis))
    return 0 if all(ergebnis["datenbanken"].values()) else 1


if __name__ == "__main__":
    raise SystemExit(main())
