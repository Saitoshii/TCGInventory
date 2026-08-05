#!/usr/bin/env bash
# Bequemer Aufruf der Wiederherstellung auf dem Pi.
#
#   ./scripts/restore.sh liste
#   ./scripts/restore.sh hole tcginventory_2026-05-04_0330.tar.gz --ziel /tmp/restore
#
# Die eigentliche Logik steckt in restore.py (dort auch die Prüfungen:
# SHA-256, PRAGMA integrity_check, kein Schreiben ins Live-Verzeichnis).
set -euo pipefail

HIER="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

# Umgebung laden, falls vorhanden (enthält die Pfade zur rclone-Konfiguration).
if [ -f /etc/tcginventory-backup.env ]; then
    set -a
    # shellcheck disable=SC1091
    . /etc/tcginventory-backup.env
    set +a
fi

exec python3 "$HIER/restore.py" "$@"
