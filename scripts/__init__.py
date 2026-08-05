"""Betriebs-Skripte (Backup/Restore).

Die Module hier sind bewusst eigenständig: sie nutzen nur die Standardbibliothek
und importieren nichts aus der Anwendung, damit sie auch dann laufen, wenn die
Anwendung gerade nicht startet. Aufruf entweder direkt
(``python scripts/backup.py``) oder als Modul
(``python -m TCGInventory.scripts.backup``).
"""
