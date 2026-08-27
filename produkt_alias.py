"""Gelernte Zuordnungen: Cardmarket-Schreibweise → eigene Schreibweise.

Einzelkarten findet das System über die lokale Scryfall-Datenbank. Displays,
Precons und Zubehör stehen dort nicht — für sie gibt es nichts nachzuschlagen,
und die Position landet jedes Mal wieder in der Handzuordnung.

Statt schlauer zu raten, merkt sich das System die Entscheidung: Sie ordnen ein
Produkt **einmal** zu, und ab dem zweiten Verkauf wird es erkannt.

Gespeichert wird dabei keine Verknüpfung auf eine Bestandszeile, sondern auf
die **Identität** (Name, Set, Sprache). Bestandszeilen kommen und gehen — ein
neuer Karton ist eine neue Zeile, die Identität bleibt. Damit fügt sich das in
den bestehenden Weg ein: der Abgleich läuft weiterhin über
``name + set_code + language``, nur eben mit unserer Schreibweise statt der von
Cardmarket.

Der abgeschnittene Name
-----------------------
Cardmarket kürzt lange Produktnamen in der Mail::

    1x Commander: Magic: The Gathering | Teenage Mutant Ninja Turtles: ... 48,50 EUR

Der volle Name steht dort nicht — er lässt sich auch nicht rekonstruieren. Die
Zuordnung merkt sich deshalb den gekürzten Text, so wie er kommt.

Das hat einen Haken: **mehrere Produkte können auf denselben Text gekürzt
werden.** Ein Set hat typischerweise vier Commander-Decks, und alle vier
beginnen gleich. Eine gelernte Zuordnung träfe dann ab dem zweiten Deck still
das Falsche.

Deshalb wird vor jedem automatischen Treffer geprüft, ob der Anfang im
**eigenen Bestand** eindeutig ist. Ist er es nicht, greift die Zuordnung nicht
und die Position geht wie bisher zur Handauswahl. Lieber einmal mehr fragen
als einmal falsch liefern.
"""

from __future__ import annotations

import sqlite3
from datetime import datetime
from typing import Dict, List, Optional


def _schluessel(text: Optional[str]) -> str:
    """Vergleichsform: getrimmt und klein — Cardmarket schreibt nicht immer gleich."""
    return (text or "").strip().lower()


def ist_gekuerzt(text: Optional[str]) -> bool:
    return "..." in (text or "") or "…" in (text or "")


def _anfang(text: str) -> str:
    """Der Teil vor der Kürzung — ohne die Punkte selbst."""
    for marke in ("...", "…"):
        if marke in text:
            return text.split(marke, 1)[0].strip()
    return text.strip()


#: Verglichen wird ohne Rücksicht auf Groß- und Kleinschreibung, gespeichert
#: aber im Original: die Übersicht soll zeigen, was Cardmarket wirklich
#: schreibt, und nicht eine kleingeschriebene Fassung davon.
_VERGLEICH = ("LOWER(mail_text) = ? AND "
              "LOWER(COALESCE(mail_set, '')) = COALESCE(?, '')")


def merke(conn: sqlite3.Connection, mail_text: str, mail_set: Optional[str],
          ziel_name: str, ziel_set_code: Optional[str],
          ziel_language: Optional[str], benutzer: str = "") -> int:
    """Eine Zuordnung anlegen oder ersetzen."""
    conn.execute(f"DELETE FROM produkt_alias WHERE {_VERGLEICH}",
                 (_schluessel(mail_text), _schluessel(mail_set)))
    cur = conn.execute(
        "INSERT INTO produkt_alias (mail_text, mail_set, ziel_name, "
        "ziel_set_code, ziel_language, angelegt_von, angelegt_am) "
        "VALUES (?, ?, ?, ?, ?, ?, ?)",
        ((mail_text or "").strip(), (mail_set or "").strip() or None, ziel_name,
         (ziel_set_code or "").lower() or None,
         (ziel_language or "").lower() or None,
         benutzer, datetime.now().isoformat(timespec="seconds")))
    return cur.lastrowid


#: Ausdrücklich aufgezählt statt ``SELECT *``: so entsteht ein einfaches
#: ``dict`` ohne ``row_factory`` an der Verbindung zu drehen. Die Verbindung
#: gehört dem Aufrufer — sie hinter seinem Rücken umzustellen, änderte still,
#: wie **seine** übrigen Abfragen zurückkommen.
_SPALTEN = ("id", "mail_text", "mail_set", "ziel_name", "ziel_set_code",
            "ziel_language", "angelegt_von", "angelegt_am")


def finde(conn: sqlite3.Connection, mail_text: str,
          mail_set: Optional[str] = None) -> Optional[Dict]:
    """Eine gelernte Zuordnung zu diesem Mailtext, falls es eine gibt.

    Gibt es die Tabelle noch nicht — eine Datenbank von vor dieser Änderung —,
    kommt ``None`` zurück. Ein Absturz beim Einlesen einer Bestellung wäre die
    falsche Antwort auf eine fehlende Zusatzfunktion; ohne Zuordnungen läuft
    alles wie zuvor.
    """
    try:
        zeile = conn.execute(
            f"SELECT {', '.join(_SPALTEN)} FROM produkt_alias "
            f"WHERE {_VERGLEICH} LIMIT 1",
            (_schluessel(mail_text), _schluessel(mail_set))).fetchone()
    except sqlite3.OperationalError:
        return None
    return dict(zip(_SPALTEN, zeile)) if zeile else None


def anfang_ist_eindeutig(conn: sqlite3.Connection, mail_text: str) -> bool:
    """Gibt es im Bestand nur **ein** Produkt, das so anfängt?

    Nur für gekürzte Namen von Belang. Passen mehrere, darf keine gelernte
    Zuordnung greifen — sonst würde ab dem zweiten Commander-Deck still das
    falsche Produkt ausgebucht.
    """
    if not ist_gekuerzt(mail_text):
        return True
    anfang = _anfang(mail_text)
    if not anfang:
        return False
    zeilen = conn.execute(
        "SELECT DISTINCT LOWER(name) FROM cards "
        "WHERE LOWER(name) LIKE ? ESCAPE '\\'",
        (anfang.lower().replace("\\", "\\\\").replace("%", "\\%")
         .replace("_", "\\_") + "%",)).fetchall()
    return len(zeilen) <= 1


def alle(conn: sqlite3.Connection) -> List[Dict]:
    """Alle gelernten Zuordnungen — für die Übersicht."""
    try:
        zeilen = conn.execute(
            f"SELECT {', '.join(_SPALTEN)} FROM produkt_alias "
            f"ORDER BY angelegt_am DESC").fetchall()
    except sqlite3.OperationalError:
        return []
    return [dict(zip(_SPALTEN, z)) for z in zeilen]


def entferne(conn: sqlite3.Connection, alias_id: int) -> bool:
    cur = conn.execute("DELETE FROM produkt_alias WHERE id = ?", (alias_id,))
    return cur.rowcount > 0
