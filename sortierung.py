"""Sortierschlüssel für Lagerplätze, Kartennamen und Sammlernummern.

Warum das nicht einfach ``ORDER BY`` in SQL erledigt:

* **Zahlen in Text.** SQLite vergleicht ``storage_code`` zeichenweise. Damit
  steht ``O01-S10-P1`` vor ``O01-S2-P1``, weil ``1`` vor ``2`` kommt. Wer die
  Plätze von Hand angelegt hat (``O1-S3-P4`` statt ``O01-S03-P4``), findet
  seine Karten am Ende der Liste wieder.
* **Groß- und Kleinschreibung.** Die Standardsortierung von SQLite ist binär:
  alle Großbuchstaben kommen vor allen Kleinbuchstaben. ``brainstorm`` landet
  damit hinter ``Zombie Ogre``.
* **Sonderzeichen.** ``Æther Vial``, ``Jötun Grunt`` oder ``Márton Stromgald``
  stehen binär sortiert hinter dem ganzen Alphabet.
* **Karten ohne Platz.** ``NULL`` sortiert in SQLite zuerst. Displays und
  Zubehör hätten damit die erste Zeile im Ordner belegt.

Die Plätze in der Datenbank werden dabei **nicht angefasst**. Sortiert wird
beim Anzeigen; wo eine Karte liegt, ändert sich dadurch nicht.
"""

from __future__ import annotations

import re
import unicodedata
from typing import Tuple

#: Zeichen, die sich nicht in Grundbuchstaben zerlegen lassen und deshalb von
#: Hand zugeordnet werden. ``ß`` fehlt hier bewusst — ``casefold()`` macht
#: daraus bereits ``ss``.
_ERSATZ = {
    "æ": "ae", "œ": "oe", "ø": "o", "å": "a",
    "þ": "th", "ð": "d", "đ": "d", "ł": "l", "ı": "i",
}

_ZAHLEN = re.compile(r"(\d+)")


def alphabet(text: str | None) -> str:
    """Vergleichsform eines Namens: klein, ohne Akzente, ohne Sonderformen.

    ``Æther Vial`` wird zu ``aether vial`` und steht damit dort, wo man es
    sucht — am Anfang.
    """
    gefaltet = (text or "").casefold()
    gefaltet = "".join(_ERSATZ.get(zeichen, zeichen) for zeichen in gefaltet)
    zerlegt = unicodedata.normalize("NFKD", gefaltet)
    return "".join(z for z in zerlegt if not unicodedata.combining(z))


def natuerlich(text: str | None) -> Tuple:
    """Zahlen als Zahlen vergleichen: ``S2`` vor ``S10``.

    Der Text wird in Zahl- und Buchstabenstücke zerlegt. Jedes Stück wird zu
    einem Tripel, damit sich Zahlen und Buchstaben überhaupt vergleichen
    lassen — Zahlen sortieren vor Buchstaben.
    """
    return tuple(
        (0, int(stueck), "") if stueck.isdigit() else (1, 0, alphabet(stueck))
        for stueck in _ZAHLEN.split((text or "").strip()) if stueck
    )


def _leer_ans_ende(text: str | None) -> Tuple:
    """Natürliche Ordnung, aber Leeres sortiert hinter allem anderen."""
    if not (text or "").strip():
        return (1, ())
    return (0, natuerlich(text))


def platz(code: str | None) -> Tuple:
    """Sortierschlüssel für einen Lagerplatz. Ohne Platz ans **Ende**.

    Displays, Zubehör und noch nicht einsortierte Karten haben keinen Platz.
    Sie gehören unter die einsortierten Karten, nicht darüber.
    """
    return _leer_ans_ende(code)


def nummer(wert: str | None) -> Tuple:
    """Sortierschlüssel für eine Sammlernummer. Ohne Nummer ans **Ende**.

    Sammlernummern sind nicht überall gleich lang (``1``, ``10``, ``100``) und
    tragen manchmal einen Buchstaben (``281a``). Beides muss zusammenpassen.
    """
    return _leer_ans_ende(wert)
