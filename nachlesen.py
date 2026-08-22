"""Fehlende Beträge aus der Original-Bestellmail nachtragen.

Es gibt keinen Cardmarket-API-Zugang; verbindlich ist allein, was in der
Bestellmail steht. Manche Bestellungen haben trotzdem keine Beträge:

* Alles, was vor dem 10.07.2026 eingelesen wurde — damals hat der Parser die
  Beträge noch nicht erfasst.
* Einzelne spätere Mails, aus denen nur ein Teil gelesen wurde.

Die Roh-Mail wird nicht gespeichert, aber an jeder Bestellung steht die
Gmail-Message-ID. Damit lässt sich die Mail erneut holen und erneut lesen —
das ist kein Schätzen, sondern ein zweiter Blick in dieselbe Quelle.

Zwei Regeln, die dieses Modul durchsetzt:

1. **Nur Lücken füllen.** Ein vorhandener Wert wird nie überschrieben. Was
   einmal gebucht wurde, soll sich nicht unter der Hand ändern.
2. **Nichts anlegen, nichts ausbuchen.** Es entstehen keine Bestellungen und
   keine Bestandsänderungen. Bearbeitet werden ausschließlich Bestellungen,
   die es schon gibt.

Was auch nach dem Nachlesen fehlt, wird gemeldet und bleibt leer.
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass, field
from typing import Dict, List, Optional

from TCGInventory.email_parser import parse_order_email
from TCGInventory.gmail_auth import (get_email_body, get_email_date,
                                     get_email_subject, get_gmail_service,
                                     hole_nachricht)

#: Spalten, die nachgetragen werden dürfen, und ihr Schlüssel im Parser.
BETRAGSFELDER = {
    "amount_gesamt": "gesamtbetrag",
    "amount_gesamtwert": "gesamtwert",
    "amount_versand": "versandkosten",
    "amount_gebuehren": "gebuehren",
    "amount_auszahlung": "auszahlungsbetrag",
}

#: Diese Felder dürfen ebenfalls nachgetragen werden, wenn sie leer sind.
TEXTFELDER = ("order_number",)


@dataclass
class Ergebnis:
    """Was das Nachlesen bewirkt hat — für Anzeige und Protokoll."""

    geprueft: int = 0
    ergaenzt: int = 0
    unveraendert: int = 0
    ohne_mail: int = 0
    meldungen: List[str] = field(default_factory=list)
    #: Je Bestellung: welche Felder gefüllt wurden.
    details: Dict[int, List[str]] = field(default_factory=dict)


def _fehlt(wert) -> bool:
    """Leer heißt: NULL, Leerstring — oder die Zeichenkette „None“.

    Letzteres steht tatsächlich in älteren Zeilen: irgendwann ist ein
    ``str(None)`` in die Spalte geraten. Für die Anzeige ist das dasselbe wie
    leer, und nachtragen soll man es genauso dürfen.
    """
    if wert is None:
        return True
    if isinstance(wert, str):
        return wert.strip() in ("", "None", "none")
    return False


def offene_bestellungen(conn: sqlite3.Connection) -> List[sqlite3.Row]:
    """Bestellungen, denen ein Betrag oder die Bestellnummer fehlt."""
    conn.row_factory = sqlite3.Row
    spalten = ", ".join(BETRAGSFELDER) + ", " + ", ".join(TEXTFELDER)
    zeilen = conn.execute(
        f"SELECT id, email_message_id, buyer_name, {spalten} FROM orders "
        "ORDER BY id"
    ).fetchall()
    return [z for z in zeilen
            if any(_fehlt(z[s]) for s in list(BETRAGSFELDER) + list(TEXTFELDER))]


def _werte_aus_mail(nachricht) -> Optional[Dict]:
    """Die Mail erneut durch den Parser schicken."""
    körper = get_email_body(nachricht)
    if not körper:
        return None
    geparst = parse_order_email(
        körper, nachricht.get("id", ""),
        subject=get_email_subject(nachricht),
        email_date=get_email_date(nachricht))
    if not geparst:
        return None
    return geparst


def lese_nach(conn: sqlite3.Connection, service=None,
              grenze: int = 50, schreiben: bool = False) -> Ergebnis:
    """Fehlende Angaben aus den Original-Mails nachtragen.

    Ohne ``schreiben=True`` ist der Lauf eine reine Vorschau: es wird gelesen
    und berichtet, aber nichts geändert. So lässt sich vorher ansehen, was
    passieren würde.
    """
    ergebnis = Ergebnis()
    kandidaten = offene_bestellungen(conn)[:grenze]
    if not kandidaten:
        ergebnis.meldungen.append("Keine Bestellung mit fehlenden Angaben.")
        return ergebnis

    service = service or get_gmail_service()
    if service is None:
        ergebnis.meldungen.append(
            "Keine Verbindung zu Gmail. Ohne Zugang lassen sich die "
            "Original-Mails nicht lesen.")
        return ergebnis

    for zeile in kandidaten:
        ergebnis.geprueft += 1
        kennung = zeile["order_number"] or f"interne Nr. {zeile['id']}"
        nachricht = hole_nachricht(service, zeile["email_message_id"])
        if nachricht is None:
            ergebnis.ohne_mail += 1
            ergebnis.meldungen.append(
                f"{kennung}: Mail nicht mehr abrufbar — bleibt unverändert.")
            continue

        geparst = _werte_aus_mail(nachricht)
        if not geparst:
            ergebnis.ohne_mail += 1
            ergebnis.meldungen.append(
                f"{kennung}: Mail gefunden, aber nicht lesbar — unverändert.")
            continue

        betraege = geparst.get("amounts") or {}
        neu: Dict[str, object] = {}
        for spalte, schluessel in BETRAGSFELDER.items():
            if _fehlt(zeile[spalte]) and betraege.get(schluessel) is not None:
                neu[spalte] = betraege[schluessel]
        for spalte in TEXTFELDER:
            wert = (geparst.get(spalte) or "").strip()
            if _fehlt(zeile[spalte]) and wert:
                neu[spalte] = wert

        if not neu:
            ergebnis.unveraendert += 1
            fehlend = [s for s in BETRAGSFELDER if _fehlt(zeile[s])]
            ergebnis.meldungen.append(
                f"{kennung}: in der Mail steht nichts zu "
                f"{', '.join(fehlend) or 'den offenen Feldern'} — bleibt leer.")
            continue

        ergebnis.ergaenzt += 1
        ergebnis.details[zeile["id"]] = sorted(neu)
        if schreiben:
            zuweisung = ", ".join(f"{s} = ?" for s in neu)
            conn.execute(f"UPDATE orders SET {zuweisung} WHERE id = ?",
                         list(neu.values()) + [zeile["id"]])

    if schreiben:
        conn.commit()
    return ergebnis
