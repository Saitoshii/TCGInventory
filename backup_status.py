"""Backup-Status für die Weboberfläche (WP4a).

Liest die Status-Datei, die ``scripts/backup.py`` nach jedem Lauf schreibt.
Ein still ausgefallenes Backup ist gefährlicher als gar keines — deshalb gilt
ein Stand als **veraltet**, wenn der letzte Erfolg länger als 48 Stunden
zurückliegt, und die Oberfläche warnt dann deutlich.

Dieses Modul liest nur; es startet keine Backups und kennt keine Zugangsdaten.
"""

from __future__ import annotations

import json
import os
from datetime import datetime
from pathlib import Path
from typing import Dict, Optional

# Ab dieser Zeit ohne erfolgreichen Lauf wird gewarnt.
WARNSCHWELLE_STUNDEN = 48


def status_pfad() -> Path:
    """Pfad der Status-Datei — identische Vorgabe wie im Backup-Skript."""
    aus_umgebung = os.environ.get("TCG_BACKUP_STATUS_FILE")
    if aus_umgebung:
        return Path(aus_umgebung)
    basis = os.environ.get("TCG_BACKUP_DATA_DIR")
    if basis:
        return Path(basis) / "backup_status.json"
    return Path(__file__).resolve().parent / "data" / "backup_status.json"


def _parse_zeit(wert) -> Optional[datetime]:
    try:
        return datetime.fromisoformat(str(wert))
    except (TypeError, ValueError):
        return None


def lies_status(jetzt: Optional[datetime] = None) -> Dict:
    """Aufbereiteter Status für die Anzeige.

    Rückgabe enthält immer ``vorhanden``, ``warnung`` und ``meldung``, damit die
    Vorlage keine Sonderfälle behandeln muss.
    """
    jetzt = jetzt or datetime.now()
    pfad = status_pfad()
    ergebnis: Dict = {
        "vorhanden": False,
        "ergebnis": None,
        "zeitpunkt": None,
        "alter_stunden": None,
        "groesse_mb": None,
        "sha256": None,
        "dauer_sekunden": None,
        "archiv": None,
        "meldung": "Es liegt noch kein Backup-Bericht vor.",
        "warnung": True,
        # Ein Lauf kann gelingen und trotzdem das Falsche sichern — etwa wenn
        # die Buchhaltung eingerichtet, aber nicht eingeschlossen ist.
        "warnungen": [],
        "schwelle_stunden": WARNSCHWELLE_STUNDEN,
        "pfad": str(pfad),
    }
    if not pfad.exists():
        return ergebnis

    try:
        daten = json.loads(pfad.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        ergebnis["meldung"] = "Backup-Bericht ist unlesbar."
        return ergebnis

    zeit = _parse_zeit(daten.get("zeitpunkt"))
    erfolgreich = daten.get("ergebnis") == "erfolg"
    alter = (jetzt - zeit).total_seconds() / 3600 if zeit else None

    ergebnis.update({
        "vorhanden": True,
        "ergebnis": daten.get("ergebnis"),
        "zeitpunkt": zeit.strftime("%d.%m.%Y %H:%M") if zeit else None,
        "alter_stunden": round(alter, 1) if alter is not None else None,
        "groesse_mb": round((daten.get("groesse_bytes") or 0) / 1024 / 1024, 1),
        "sha256": daten.get("sha256"),
        "dauer_sekunden": daten.get("dauer_sekunden"),
        "archiv": daten.get("archiv"),
        "datenbanken": daten.get("datenbanken") or [],
        "meldung": daten.get("meldung") or "",
        "warnungen": [str(w) for w in (daten.get("warnungen") or [])],
    })
    # Gewarnt wird, wenn der letzte Lauf fehlschlug oder der letzte Erfolg zu
    # lange her ist. Ein fehlender Zeitstempel gilt ebenfalls als Warnung.
    ergebnis["warnung"] = (
        not erfolgreich or alter is None or alter > WARNSCHWELLE_STUNDEN
    )
    return ergebnis
