"""Positionen aus einer Bestellung nehmen, wenn die Ware nicht lieferbar war.

Der Fall aus dem Betrieb: eine Karte stand im Bestand und war doch nicht da.
Der Betrag dafür wird dem Käufer über Cardmarket erstattet, der Rest der
Bestellung geht trotzdem raus.

Drei Dinge müssen dabei zusammenpassen:

* **Der Beleg** darf die Position nicht mehr zeigen — der Käufer bekommt sie
  ja nicht.
* **Der Bestand** darf sie nicht abziehen: verkauft wurde sie nicht.
* **Die Buchhaltung** muss den erstatteten Betrag erfahren. Sie führt das als
  Erstattung, nicht als Storno: der Verkauf war echt und wird nur gemindert.

Deshalb wird die Zeile **gekennzeichnet, nicht gelöscht**. Eine gelöschte Zeile
erklärt nichts, und der Betrag wäre für die Erstattung verloren. Was
herausgenommen wurde, bleibt mit Grund, Person und Zeitpunkt sichtbar — und
lässt sich zurücknehmen, solange die Bestellung noch offen ist.

Der Bestand wird **nicht** von selbst korrigiert. Ob eine Karte wirklich fehlt
oder nur verlegt ist, weiß das Programm nicht; wer sie ausbuchen will, sagt
das ausdrücklich.
"""

from __future__ import annotations

import sqlite3
from datetime import datetime
from typing import Dict, List, Optional, Tuple


class PositionFehler(Exception):
    """Fachlicher Fehler — der Text ist für die Anzeige gedacht."""


def _spalten_vorhanden(conn: sqlite3.Connection) -> bool:
    """Kennt diese Datenbank die Kennzeichnung schon?

    Ältere Datenbanken haben die Spalten nicht. Dann verhält sich alles wie
    zuvor, statt beim Anzeigen einer Bestellung abzustürzen.
    """
    spalten = {z[1] for z in conn.execute("PRAGMA table_info(order_items)")}
    return "entfernt" in spalten


def lies(conn: sqlite3.Connection, item_id: int) -> Optional[sqlite3.Row]:
    conn.row_factory = sqlite3.Row
    return conn.execute(
        "SELECT i.*, o.status AS bestellstatus, o.id AS bestellung_id "
        "FROM order_items i JOIN orders o ON o.id = i.order_id "
        "WHERE i.id = ?", (item_id,)).fetchone()


def entferne(conn: sqlite3.Connection, item_id: int, grund: str,
             benutzer: str = "", bestand_korrigieren: bool = False) -> Dict:
    """Eine Position aus der Bestellung nehmen.

    ``bestand_korrigieren`` zieht die Menge zusätzlich vom Lagerbestand ab —
    für den Fall, dass die Ware tatsächlich fehlt und nicht nur verlegt ist.
    Ohne ausdrückliche Angabe bleibt der Bestand unberührt.
    """
    if not grund.strip():
        raise PositionFehler(
            "Bitte einen Grund angeben. Ohne ihn ist später nicht mehr "
            "nachvollziehbar, warum die Position fehlt.")

    zeile = lies(conn, item_id)
    if zeile is None:
        raise PositionFehler("Diese Position gibt es nicht.")
    if zeile["bestellstatus"] != "open":
        raise PositionFehler(
            "Diese Bestellung ist bereits abgeschlossen. Eine nachträgliche "
            "Änderung würde den gedruckten Beleg und den Bestand "
            "auseinanderlaufen lassen.")
    if zeile["entfernt"]:
        raise PositionFehler("Diese Position ist bereits herausgenommen.")

    jetzt = datetime.now().isoformat(timespec="seconds")
    conn.execute(
        "UPDATE order_items SET entfernt = 1, entfernt_grund = ?, "
        "entfernt_von = ?, entfernt_am = ? WHERE id = ?",
        (grund.strip(), benutzer, jetzt, item_id))

    korrigiert = 0
    if bestand_korrigieren and zeile["card_id"]:
        korrigiert = _bestand_mindern(conn, zeile["card_id"],
                                      zeile["quantity"] or 1)
    conn.commit()
    return {"entfernt": True, "bestand_korrigiert": korrigiert}


def _bestand_mindern(conn: sqlite3.Connection, card_id: int,
                     menge: int) -> int:
    """Den Bestand um die fehlende Menge senken. Nie unter null.

    Das ist keine Verkaufsbuchung, sondern eine Berichtigung: die Ware war
    nicht da. Deshalb wird nur die Menge gesenkt und der Artikel nicht auf
    „verkauft" gesetzt.
    """
    zeile = conn.execute("SELECT quantity FROM cards WHERE id = ?",
                         (card_id,)).fetchone()
    if zeile is None:
        return 0
    vorher = zeile[0] or 0
    nachher = max(0, vorher - max(0, menge))
    conn.execute("UPDATE cards SET quantity = ? WHERE id = ?",
                 (nachher, card_id))
    return vorher - nachher


def stelle_zurueck(conn: sqlite3.Connection, item_id: int) -> None:
    """Eine herausgenommene Position wieder aufnehmen.

    Der Bestand wird dabei **nicht** wieder erhöht: ob die Karte inzwischen
    aufgetaucht ist, weiß das Programm nicht.
    """
    zeile = lies(conn, item_id)
    if zeile is None:
        raise PositionFehler("Diese Position gibt es nicht.")
    if zeile["bestellstatus"] != "open":
        raise PositionFehler(
            "Diese Bestellung ist bereits abgeschlossen.")
    if not zeile["entfernt"]:
        raise PositionFehler("Diese Position ist gar nicht herausgenommen.")
    conn.execute(
        "UPDATE order_items SET entfernt = 0, entfernt_grund = NULL, "
        "entfernt_von = NULL, entfernt_am = NULL WHERE id = ?", (item_id,))
    conn.commit()


def positionen(conn: sqlite3.Connection, order_id: int,
               nur_gelieferte: bool = False) -> List[Dict]:
    """Die Positionen einer Bestellung.

    ``nur_gelieferte=True`` lässt die herausgenommenen weg — das ist die
    Sicht für Beileger und Quittung.
    """
    conn.row_factory = sqlite3.Row
    if not _spalten_vorhanden(conn):
        return [dict(z) for z in conn.execute(
            "SELECT * FROM order_items WHERE order_id = ? ORDER BY card_name",
            (order_id,))]
    abfrage = "SELECT * FROM order_items WHERE order_id = ?"
    if nur_gelieferte:
        abfrage += " AND COALESCE(entfernt, 0) = 0"
    abfrage += " ORDER BY card_name"
    return [dict(z) for z in conn.execute(abfrage, (order_id,))]


def erstattung_cent(conn: sqlite3.Connection, order_id: int) -> int:
    """Wert der herausgenommenen Positionen, in Cent.

    Die Grundlage für die Erstattung in der Buchhaltung. Positionen ohne
    hinterlegten Einzelpreis zählen mit null — geraten wird nicht; dass etwas
    fehlt, meldet :func:`ohne_preis`.
    """
    if not _spalten_vorhanden(conn):
        return 0
    zeilen = conn.execute(
        "SELECT quantity, unit_price FROM order_items "
        "WHERE order_id = ? AND COALESCE(entfernt, 0) = 1", (order_id,))
    summe = 0
    for menge, preis in zeilen:
        if preis is None:
            continue
        summe += round(float(preis) * 100) * (menge or 1)
    return summe


def ohne_preis(conn: sqlite3.Connection, order_id: int) -> List[str]:
    """Herausgenommene Positionen, für die kein Einzelpreis gespeichert ist.

    Ihr Anteil fehlt in der Erstattungssumme. Das muss sichtbar sein, sonst
    wird stillschweigend zu wenig erstattet.
    """
    if not _spalten_vorhanden(conn):
        return []
    return [z[0] for z in conn.execute(
        "SELECT card_name FROM order_items WHERE order_id = ? "
        "AND COALESCE(entfernt, 0) = 1 AND unit_price IS NULL", (order_id,))]


def anzahl_entfernt(conn: sqlite3.Connection, order_id: int) -> Tuple[int, int]:
    """(Anzahl Zeilen, Stückzahl) der herausgenommenen Positionen."""
    if not _spalten_vorhanden(conn):
        return 0, 0
    zeile = conn.execute(
        "SELECT COUNT(*), COALESCE(SUM(quantity), 0) FROM order_items "
        "WHERE order_id = ? AND COALESCE(entfernt, 0) = 1",
        (order_id,)).fetchone()
    return int(zeile[0] or 0), int(zeile[1] or 0)
