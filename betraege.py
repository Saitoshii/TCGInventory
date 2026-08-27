"""Beträge einer Bestellung von Hand erfassen.

Der Regelfall bleibt: die Zahlen stammen aus der Bestell-Mail. Für manche
Bestellungen geht das nicht mehr —

* die Mail wurde vor der Betragserfassung eingelesen und gibt beim Nachlesen
  nichts her,
* Cardmarket hat einen anderen Mailtyp geschickt, in dem nicht alle Summen
  stehen,
* die Mail ist im Postfach nicht mehr auffindbar.

Erfasst werden hier **nur die Angaben der Bestellung selbst**: Gesamtbetrag,
Warenwert und Versandkosten. Das sind die Zahlen, die auf Beileger und
Quittung gedruckt werden und im Verkaufs-Export stehen — sie gehören zum
Vorgang „Bestellung", nicht zur Buchhaltung.

**Nicht** hier: Plattformgebühr und Auszahlungsbetrag. Die braucht dieses
System nirgends; sie beschreiben, was Cardmarket einbehält und überweist. Wo
die fehlen, entscheidet das die Buchhaltung — sonst läge dieselbe
Finanzregel an zwei Orten und müsste synchron gehalten werden.

Was aus der Mail gelesen wird, reicht dieses System unverändert weiter, auch
Gebühr und Auszahlung. Lesen und Weitergeben ist Botendienst; das Festlegen
eines Betrags ist eine Entscheidung.
"""

from __future__ import annotations

import sqlite3
from datetime import datetime
from typing import Dict, List, Optional, Tuple

#: Eingabefelder und ihre Spalte in ``orders``. Bewusst nur die Angaben, die
#: dieses System selbst braucht — siehe Modulkopf.
FELDER = {
    "gesamt": "amount_gesamt",
    "warenwert": "amount_gesamtwert",
    "versand": "amount_versand",
}

#: Ohne diese beiden ist die Bestellung unvollständig: der Versand steht auf
#: dem Beileger, der Gesamtbetrag in Übersicht und Export.
PFLICHT = ("gesamt", "versand")

BESCHRIFTUNG = {
    "gesamt": "Gesamtbetrag",
    "warenwert": "Warenwert",
    "versand": "Versandkosten",
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
    """Ist die Bestellung in sich stimmig?

    Geprüft wird ausschliesslich, was die Bestellung selbst betrifft:
    Warenwert und Versand müssen den Gesamtbetrag ergeben. Was Cardmarket
    davon einbehält, wird hier nicht bewertet — das ist Sache der Buchhaltung.
    """
    fehler: List[str] = []
    gesamt = werte.get("gesamt")
    versand = werte.get("versand")
    warenwert = werte.get("warenwert")

    for schluessel in PFLICHT:
        if werte.get(schluessel) is None:
            fehler.append(f"{BESCHRIFTUNG[schluessel]} fehlt.")
    if gesamt is not None and gesamt <= 0:
        fehler.append("Der Gesamtbetrag muss größer als null sein.")
    if fehler:
        return fehler

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
