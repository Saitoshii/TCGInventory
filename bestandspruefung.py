"""Bestand prüfen: wo der Bestand im System und das Regal auseinanderlaufen.

Bei einer Inventur stand mehr im System als im Regal. Die Ursachen sind
behoben — ihre Folgen stecken aber noch in den vorhandenen Daten. Diese Seite
findet sie, damit sie sich einzeln und nachvollziehbar bereinigen lassen:

* **Verkauft, aber nicht abgezogen.** Positionen verkaufter Bestellungen ohne
  zugeordnete Karte wurden nie vom Bestand abgezogen; Positionen, für die der
  Bestand zu wenig hergab, nur teilweise.
* **Set-Code verloren.** Beim Bearbeiten einer Karte wurde ihr Set-Code durch
  den Namen des Ordners ersetzt. Solche Karten findet keine Bestellung mehr,
  und ein neuer Import legt eine zweite Zeile für dieselbe Karte an. Steht an
  der Karte die Scryfall-ID, lässt sich der Set-Code aus den lokalen
  Kartendaten genau wiederherstellen — geraten wird nicht.
* **Doppelte Einträge.** Dieselbe Karte (Set, Nummer, Sprache, Foil) mehrfach
  im selben Ordner.
* **Plätze, die es nicht gibt.** Karten auf P10 und höher.

Entschieden wird hier nichts von selbst. Jede Änderung braucht einen Klick und
steht im Änderungsprotokoll.
"""

from __future__ import annotations

import re
import sqlite3
from collections import defaultdict
from datetime import datetime
from typing import Dict, List, Optional

from . import card_scanner
from .lager_manager import buche_aus, log_audit, platz_gibt_es_im_ordner


class PruefFehler(Exception):
    """Fachlicher Fehler — der Text ist für die Anzeige gedacht."""


# ---------------------------------------------------------------------------
# Verkauft, aber nicht (vollständig) abgezogen
# ---------------------------------------------------------------------------
def nicht_abgezogen(conn: sqlite3.Connection) -> List[Dict]:
    """Positionen verkaufter Bestellungen, deren Ware noch im Bestand steht.

    * ohne zugeordnete Karte — beim Abschließen wurde nichts abgezogen;
    * mit festgehaltener Abzugsmenge unter der bestellten Menge.

    Bei Positionen, die vor dem Festhalten der Abzugsmenge abgeschlossen
    wurden und eine Karte hatten, ist nicht mehr feststellbar, ob abgezogen
    wurde. Sie stehen hier nicht — das wäre Raten.
    """
    conn.row_factory = sqlite3.Row
    zeilen = conn.execute(
        "SELECT i.id, i.order_id, i.card_name, i.quantity, i.card_id, "
        "       i.abgezogen, i.set_name, i.language, i.condition, i.foil, "
        "       o.order_number, o.buyer_name, o.quelle, o.verkaufskanal, "
        "       COALESCE(o.date_completed, o.email_date, o.date_received) AS datum "
        "FROM order_items i JOIN orders o ON o.id = i.order_id "
        "WHERE o.status = 'sold' AND COALESCE(i.entfernt, 0) = 0 "
        "  AND COALESCE(i.bestand_geklaert, 0) = 0 "
        "  AND (i.card_id IS NULL "
        "       OR (i.abgezogen IS NOT NULL AND i.abgezogen < i.quantity)) "
        "ORDER BY datum DESC, i.id").fetchall()
    ergebnis = []
    for zeile in zeilen:
        eintrag = dict(zeile)
        eintrag["offen"] = (eintrag["quantity"] or 0) - (eintrag["abgezogen"] or 0)
        ergebnis.append(eintrag)
    return ergebnis


def _position(conn: sqlite3.Connection, item_id: int) -> sqlite3.Row:
    conn.row_factory = sqlite3.Row
    zeile = conn.execute(
        "SELECT i.*, o.status AS bestellstatus, o.order_number "
        "FROM order_items i JOIN orders o ON o.id = i.order_id "
        "WHERE i.id = ?", (item_id,)).fetchone()
    if zeile is None:
        raise PruefFehler("Diese Position gibt es nicht.")
    if zeile["bestellstatus"] != "sold":
        raise PruefFehler("Diese Bestellung ist noch offen – sie wird beim "
                          "Abschließen abgezogen.")
    if zeile["entfernt"] or zeile["bestand_geklaert"]:
        raise PruefFehler("Für diese Position ist bereits alles erledigt.")
    return zeile


def nachtraeglich_ausbuchen(conn: sqlite3.Connection, item_id: int,
                            card_id: int, benutzer: str) -> Dict:
    """Die noch offene Menge einer verkauften Position jetzt abziehen.

    Ist der Position noch keine Karte zugeordnet, wird die gewählte Karte
    eingetragen. Ist eine zugeordnet, muss es dieselbe sein — eine andere Karte
    abzuziehen, als verkauft wurde, wäre eine neue Entscheidung über die
    Bestellung und gehört nicht hierher.

    Alles in einer Transaktion. Gibt ``{"abgezogen": n, "offen": rest}``.
    """
    zeile = _position(conn, item_id)
    if zeile["card_id"] and int(zeile["card_id"]) != int(card_id):
        # Nur wenn die zugeordnete Karte gar nicht mehr im Bestand steht, darf
        # eine andere gewählt werden — sonst bleibt es bei der verkauften.
        noch_da = conn.execute("SELECT 1 FROM cards WHERE id = ?",
                               (zeile["card_id"],)).fetchone()
        if noch_da:
            raise PruefFehler("Dieser Position ist eine andere Karte zugeordnet.")
    karte = conn.execute("SELECT name, quantity FROM cards WHERE id = ?",
                         (card_id,)).fetchone()
    if karte is None:
        raise PruefFehler("Diese Karte gibt es nicht mehr im Bestand.")

    offen = (zeile["quantity"] or 0) - (zeile["abgezogen"] or 0)
    cursor = conn.cursor()
    try:
        abgezogen = buche_aus(cursor, card_id, offen, benutzer)
        bisher = zeile["abgezogen"] or 0
        cursor.execute(
            "UPDATE order_items SET card_id = ?, abgezogen = ?, "
            "bestand_geklaert = ?, "
            "match_status = CASE WHEN match_status = 'matched' THEN match_status "
            "                    ELSE 'manual' END "
            "WHERE id = ?",
            (card_id, bisher + abgezogen, 1 if abgezogen >= offen else 0, item_id))
        log_audit(card_id, benutzer, "nachtrag-verkauf", "quantity",
                  f"Bestellung {zeile['order_number'] or zeile['order_id']}",
                  f"{abgezogen} von {offen} nachträglich abgezogen", cursor)
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    return {"abgezogen": abgezogen, "offen": offen - abgezogen}


def als_erledigt_markieren(conn: sqlite3.Connection, item_id: int,
                           benutzer: str) -> None:
    """Am Bestand ist für diese Position nichts mehr zu tun.

    Etwa weil die Ware nie im Bestand geführt wurde, oder weil der Bestand
    bereits von Hand berichtigt ist. Der Bestand bleibt unverändert.
    """
    zeile = _position(conn, item_id)
    conn.execute("UPDATE order_items SET bestand_geklaert = 1 WHERE id = ?",
                 (item_id,))
    log_audit(zeile["card_id"], benutzer, "bestand-geklaert", "order_item",
              f"{zeile['card_name']} – Bestellung "
              f"{zeile['order_number'] or zeile['order_id']}",
              "ohne Abzug erledigt", conn.cursor())
    conn.commit()


# ---------------------------------------------------------------------------
# Set-Code verloren
# ---------------------------------------------------------------------------
def _sieht_nicht_nach_set_code_aus(set_code: str, ordnername: str) -> Optional[str]:
    code = (set_code or "").strip()
    if not code:
        return "Set-Code fehlt"
    if ordnername and code.casefold() == ordnername.strip().casefold():
        return "Set-Code ist der Name des Ordners"
    if re.search(r"\s", code):
        return "Set-Code enthält Leerzeichen"
    return None


def falscher_set_code(conn: sqlite3.Connection) -> List[Dict]:
    """Karten, deren Set-Code keiner ist — mit Korrektur, wenn sie eindeutig ist.

    Die Korrektur kommt ausschließlich aus der Scryfall-ID an der Karte und den
    lokalen Kartendaten. Ohne ID (oder ohne Kartendaten) gibt es keinen
    Vorschlag; dann bleibt nur das Bearbeiten von Hand.
    """
    conn.row_factory = sqlite3.Row
    zeilen = conn.execute(
        "SELECT c.id, c.name, c.set_code, c.collector_number, c.language, "
        "       c.foil, c.quantity, c.storage_code, c.scryfall_id, "
        "       COALESCE(f.name, '') AS ordner "
        "FROM cards c LEFT JOIN folders f ON f.id = c.folder_id "
        "WHERE COALESCE(c.item_type, 'card') = 'card' "
        "ORDER BY c.id").fetchall()
    ergebnis = []
    for zeile in zeilen:
        grund = _sieht_nicht_nach_set_code_aus(zeile["set_code"], zeile["ordner"])
        if not grund:
            continue
        eintrag = dict(zeile)
        eintrag["grund"] = grund
        eintrag["korrektur"] = _korrektur(zeile)
        ergebnis.append(eintrag)
    return ergebnis


def _korrektur(zeile) -> Optional[Dict]:
    """Set-Code (und ggf. Nummer) aus der Scryfall-ID — nur wenn eindeutig."""
    gefunden = card_scanner.karte_nach_scryfall_id(zeile["scryfall_id"])
    if not gefunden or not gefunden.get("set_code"):
        return None
    nummer = (zeile["collector_number"] or "").strip()
    if nummer and nummer != (gefunden.get("collector_number") or ""):
        # Nummer an der Karte und Scryfall-ID passen nicht zusammen — dann
        # ist unklar, was stimmt.
        return None
    return gefunden


def set_code_wiederherstellen(conn: sqlite3.Connection, card_id: int,
                              benutzer: str) -> str:
    """Den Set-Code einer Karte aus ihrer Scryfall-ID wiederherstellen.

    Gerechnet wird beim Klick neu — aus der Datenbank, nicht aus dem
    Formular. Gibt den neuen Set-Code zurück.
    """
    conn.row_factory = sqlite3.Row
    zeile = conn.execute(
        "SELECT c.id, c.set_code, c.collector_number, c.scryfall_id, "
        "       COALESCE(f.name, '') AS ordner "
        "FROM cards c LEFT JOIN folders f ON f.id = c.folder_id "
        "WHERE c.id = ?", (card_id,)).fetchone()
    if zeile is None:
        raise PruefFehler("Diese Karte gibt es nicht mehr.")
    if not _sieht_nicht_nach_set_code_aus(zeile["set_code"], zeile["ordner"]):
        raise PruefFehler("Der Set-Code dieser Karte ist bereits in Ordnung.")
    korrektur = _korrektur(zeile)
    if not korrektur:
        raise PruefFehler("Für diese Karte lässt sich der Set-Code nicht sicher "
                          "bestimmen – bitte an der Karte von Hand eintragen.")
    neu = korrektur["set_code"]
    conn.execute(
        "UPDATE cards SET set_code = ?, "
        "collector_number = CASE WHEN COALESCE(collector_number, '') = '' "
        "                        THEN ? ELSE collector_number END WHERE id = ?",
        (neu, korrektur.get("collector_number") or "", card_id))
    log_audit(card_id, benutzer, "update", "set_code", zeile["set_code"], neu,
              conn.cursor())
    conn.commit()
    return neu


def alle_set_codes_wiederherstellen(conn: sqlite3.Connection,
                                    benutzer: str) -> int:
    """Alle eindeutig bestimmbaren Set-Codes wiederherstellen. Gibt die Anzahl."""
    anzahl = 0
    for eintrag in falscher_set_code(conn):
        if eintrag["korrektur"]:
            set_code_wiederherstellen(conn, eintrag["id"], benutzer)
            anzahl += 1
    return anzahl


# ---------------------------------------------------------------------------
# Doppelte Einträge, Plätze, die es nicht gibt
# ---------------------------------------------------------------------------
def doppelte_eintraege(conn: sqlite3.Connection) -> List[List[Dict]]:
    """Gruppen von Zeilen mit derselben Karte im selben Ordner.

    Dieselbe Karte gehört in eine Zeile mit der richtigen Menge. Mehrere
    Zeilen entstanden etwa durch abweichende Schreibweisen des Set-Codes
    (``TRK``/``trk``) oder durch einen verlorenen Set-Code. Ob beide Zeilen
    echte Karten sind, lässt sich nur am Ordner feststellen — deshalb wird
    hier nur gezeigt, nicht zusammengelegt.
    """
    conn.row_factory = sqlite3.Row
    zeilen = conn.execute(
        "SELECT c.id, c.name, c.set_code, c.collector_number, c.language, "
        "       c.foil, c.quantity, c.condition, c.storage_code, c.date_added, "
        "       c.folder_id, COALESCE(f.name, '') AS ordner "
        "FROM cards c LEFT JOIN folders f ON f.id = c.folder_id "
        "WHERE COALESCE(c.item_type, 'card') = 'card' "
        "  AND COALESCE(c.set_code, '') <> '' "
        "  AND COALESCE(c.collector_number, '') <> '' "
        "ORDER BY c.id").fetchall()
    gruppen = defaultdict(list)
    for zeile in zeilen:
        ordner = zeile["folder_id"] if zeile["folder_id"] not in (None, "") else None
        schluessel = (zeile["set_code"].strip().lower(),
                      str(zeile["collector_number"]).strip(),
                      (zeile["language"] or "").lower(), int(bool(zeile["foil"])),
                      ordner)
        gruppen[schluessel].append(dict(zeile))
    return [gruppe for gruppe in gruppen.values() if len(gruppe) > 1]


def plaetze_die_es_nicht_gibt(conn: sqlite3.Connection) -> List[Dict]:
    """Karten auf einem Platz ab P10 — den gibt es im Ordner nicht."""
    conn.row_factory = sqlite3.Row
    return [dict(z) for z in conn.execute(
        "SELECT c.id, c.name, c.set_code, c.collector_number, c.quantity, "
        "       c.storage_code, COALESCE(f.name, '') AS ordner "
        "FROM cards c LEFT JOIN folders f ON f.id = c.folder_id "
        "WHERE COALESCE(c.storage_code, '') <> '' ORDER BY c.storage_code")
        if not platz_gibt_es_im_ordner(z["storage_code"])]


def alte_offene_bestellungen(conn: sqlite3.Connection, tage: int) -> int:
    grenze = datetime.fromtimestamp(
        datetime.now().timestamp() - tage * 86400).isoformat()
    return conn.execute(
        "SELECT COUNT(*) FROM orders WHERE status = 'open' "
        "AND COALESCE(email_date, date_received) < ?", (grenze,)).fetchone()[0]


def menge_null(conn: sqlite3.Connection) -> int:
    return conn.execute(
        "SELECT COUNT(*) FROM cards WHERE quantity <= 0 OR status = 'archiviert'"
    ).fetchone()[0]
