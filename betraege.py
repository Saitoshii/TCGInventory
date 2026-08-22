"""Beträge einer Bestellung von Hand erfassen.

Der Regelfall bleibt: die Zahlen stammen aus der Bestell-Mail. Für manche
Bestellungen geht das nicht mehr —

* die Mail wurde vor der Betragserfassung eingelesen und gibt beim Nachlesen
  nichts her,
* Cardmarket hat einen anderen Mailtyp geschickt, in dem nicht alle Summen
  stehen,
* die Mail ist im Postfach nicht mehr auffindbar.

Dann ist eine Eingabe von Hand die einzige Möglichkeit, die Bestellung
überhaupt zu buchen. Sie ist aber **keine Quelle, sondern eine Entscheidung**
— und wird deshalb dauerhaft als solche gekennzeichnet (``betraege_manuell``),
mit Benutzer und Zeitpunkt.

Geprüft wird hier mit **denselben** Kontrollrechnungen wie in der Buchhaltung.
Sonst nimmt das Formular eine Eingabe an, die drüben abgelehnt wird — und
niemand versteht, warum.
"""

from __future__ import annotations

import sqlite3
from datetime import datetime
from typing import Dict, List, Optional, Tuple

#: Eingabefelder und ihre Spalte in ``orders``.
FELDER = {
    "gesamt": "amount_gesamt",
    "warenwert": "amount_gesamtwert",
    "versand": "amount_versand",
    "gebuehren": "amount_gebuehren",
    "auszahlung": "amount_auszahlung",
}

#: Ohne diese drei bucht die Buchhaltung nicht.
PFLICHT = ("gesamt", "versand", "gebuehren")

BESCHRIFTUNG = {
    "gesamt": "Gesamtbetrag",
    "warenwert": "Warenwert",
    "versand": "Versandkosten",
    "gebuehren": "Cardmarket-Gebühr",
    "auszahlung": "Auszahlungsbetrag",
}


class BetragFehler(Exception):
    """Fachlicher Fehler — der Text ist für die Anzeige gedacht."""


def parse_betrag(roh: str) -> Optional[int]:
    """Eine Eingabe wie ``1,82`` oder ``1.82`` in Cent umwandeln.

    Leer heißt „nicht angegeben" und ergibt ``None`` — das ist etwas anderes
    als null Euro. Gerechnet wird in ganzen Cent; über Fließkomma käme bei
    ``0,07`` irgendwann eine Sechs oder Acht heraus.
    """
    text = (roh or "").strip().replace("€", "").replace(" ", "")
    if not text:
        return None
    text = text.replace(",", ".")
    if text.count(".") > 1:
        raise BetragFehler(f"„{roh}" + "“ ist kein Betrag.")
    try:
        vor, _, nach = text.partition(".")
        negativ = vor.startswith("-")
        vor = vor.lstrip("+-")
        if not vor.isdigit() and vor != "":
            raise ValueError
        if nach and not nach.isdigit():
            raise ValueError
        cent = int(vor or "0") * 100 + int((nach + "00")[:2])
        if len(nach) > 2:
            raise BetragFehler(
                f"„{roh}" + "“ hat mehr als zwei Nachkommastellen.")
        return -cent if negativ else cent
    except ValueError:
        raise BetragFehler(f"„{roh}" + "“ ist kein Betrag.") from None


def pruefe(werte: Dict[str, Optional[int]]) -> List[str]:
    """Dieselben Kontrollrechnungen wie in der Buchhaltung.

    Wortgleich gehalten: was hier durchgeht, muss dort buchbar sein. Weicht
    eine der beiden Seiten ab, nimmt das Formular eine Eingabe an, die drüben
    scheitert.
    """
    fehler: List[str] = []
    gesamt = werte.get("gesamt")
    versand = werte.get("versand")
    gebuehren = werte.get("gebuehren")
    auszahlung = werte.get("auszahlung")
    warenwert = werte.get("warenwert")

    for schluessel in PFLICHT:
        if werte.get(schluessel) is None:
            fehler.append(f"{BESCHRIFTUNG[schluessel]} fehlt.")
    if gesamt is not None and gesamt <= 0:
        fehler.append("Der Gesamtbetrag muss größer als null sein.")
    if fehler:
        return fehler

    if auszahlung is not None and gesamt - gebuehren != auszahlung:
        fehler.append(
            f"Kontrollrechnung geht nicht auf: Gesamtbetrag minus Gebühr "
            f"ergibt {_euro(gesamt - gebuehren)}, angegeben ist "
            f"{_euro(auszahlung)}.")
    if warenwert is not None and warenwert + versand != gesamt:
        fehler.append(
            f"Warenwert und Versand ergeben nicht den Gesamtbetrag: "
            f"{_euro(warenwert)} + {_euro(versand)} = "
            f"{_euro(warenwert + versand)}, angegeben ist {_euro(gesamt)}.")
    return fehler


def _euro(cent: int) -> str:
    """Cent als deutscher Betrag — ohne Fließkomma."""
    vorzeichen = "-" if cent < 0 else ""
    cent = abs(int(cent))
    return f"{vorzeichen}{cent // 100},{cent % 100:02d} €"


def vorschlag_auszahlung(gesamt: Optional[int],
                         gebuehren: Optional[int]) -> Optional[int]:
    """Was die Auszahlung rechnerisch sein müsste — nur als Hinweis.

    Bewusst kein automatisches Ausfüllen: der Wert steht auf der
    Cardmarket-Abrechnung und soll von dort abgeschrieben werden, nicht aus
    einer Rechnung entstehen.
    """
    if gesamt is None or gebuehren is None:
        return None
    return gesamt - gebuehren


def speichere(conn: sqlite3.Connection, order_id: int,
              werte: Dict[str, Optional[int]], benutzer: str,
              bestellnummer: Optional[str] = None) -> Tuple[int, List[str]]:
    """Beträge von Hand setzen. Gibt (Anzahl gesetzter Felder, Fehler) zurück.

    Bei Fehlern wird **nichts** geschrieben: eine halb erfasste Bestellung
    wäre schlimmer als eine gar nicht erfasste.
    """
    fehler = pruefe(werte)
    if fehler:
        return 0, fehler

    zeile = conn.execute("SELECT id FROM orders WHERE id = ?",
                         (order_id,)).fetchone()
    if zeile is None:
        return 0, ["Diese Bestellung gibt es nicht."]

    setzen: Dict[str, object] = {}
    for schluessel, spalte in FELDER.items():
        wert = werte.get(schluessel)
        if wert is not None:
            setzen[spalte] = wert / 100.0        # gespeichert wird in Euro
    anzahl_betraege = len(setzen)                # die Bestellnummer zaehlt nicht mit
    if bestellnummer and bestellnummer.strip():
        setzen["order_number"] = bestellnummer.strip()

    setzen["betraege_manuell"] = 1
    setzen["betraege_von"] = benutzer
    setzen["betraege_am"] = datetime.now().isoformat(timespec="seconds")

    zuweisung = ", ".join(f"{s} = ?" for s in setzen)
    conn.execute(f"UPDATE orders SET {zuweisung} WHERE id = ?",
                 list(setzen.values()) + [order_id])
    conn.commit()
    return anzahl_betraege, []
