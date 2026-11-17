"""Ordner-Ansicht: jede Seite so, wie sie im Ordner aussieht — und Inventur darauf.

Ein Ordner ist ein echtes Binder: Seiten mit je neun Fächern, Platz 1 oben
links bis Platz 9 unten rechts. Der Lagerplatz einer Karte (``O07-S03-P4``)
sagt genau, wo sie steckt. Diese Ansicht zeigt eine Seite als Raster mit den
Kartenbildern auf ihrem Platz, damit man Bildschirm und Ordner nebeneinander
vergleichen kann.

Im **Inventur-Modus** wird Karte für Karte bestätigt oder berichtigt:

* **stimmt** — die Karte ist da, die Menge passt;
* **+ / −** — die Menge berichtigen (nie unter 1);
* **fehlt** — die Karte ist nicht da; sie wird aus dem Bestand entfernt;
* **Seite stimmt** — alles auf der Seite ist wie angezeigt.

Jede Änderung steht im Änderungsprotokoll. Was seit Beginn der laufenden
Inventur gezählt wurde (``folders.inventur_seit``), zeigt der Fortschritt.
"""

from __future__ import annotations

import re
import sqlite3
from datetime import datetime
from typing import Dict, List, Optional, Tuple

from .lager_manager import delete_card, log_audit

#: Fächer je Seite — Platz 1 oben links bis Platz 9 unten rechts.
PLAETZE_JE_SEITE = 9

_PLATZ = re.compile(r"O(\d+)-S(\d+)-P(\d+)")


class OrdnerFehler(Exception):
    """Fachlicher Fehler — der Text ist für die Anzeige gedacht."""


def platz_teile(code: Optional[str]) -> Optional[Tuple[int, int, int]]:
    """``"O07-S03-P4"`` → ``(7, 3, 4)``; alles andere → ``None``."""
    treffer = _PLATZ.fullmatch((code or "").strip())
    if not treffer:
        return None
    return tuple(int(teil) for teil in treffer.groups())  # type: ignore[return-value]


def platz_code(ordner_id: int, seite: int, platz: int) -> str:
    return f"O{int(ordner_id):02d}-S{int(seite):02d}-P{int(platz)}"


def _jetzt() -> str:
    return datetime.now().isoformat(timespec="seconds")


# ---------------------------------------------------------------------------
# Lesen
# ---------------------------------------------------------------------------
def ordner(conn: sqlite3.Connection, ordner_id: int) -> Optional[Dict]:
    conn.row_factory = sqlite3.Row
    zeile = conn.execute(
        "SELECT id, name, pages, inventur_seit FROM folders WHERE id = ?",
        (ordner_id,)).fetchone()
    return dict(zeile) if zeile else None


def _karten(conn: sqlite3.Connection, ordner_id: int) -> List[Dict]:
    """Alle Einzelkarten, die zu diesem Ordner gehören oder dort liegen."""
    conn.row_factory = sqlite3.Row
    praefix = f"O{int(ordner_id):02d}-"
    zeilen = conn.execute(
        "SELECT id, name, set_code, collector_number, language, condition, "
        "       foil, quantity, storage_code, image_url, price, folder_id, "
        "       gezaehlt_am, rarity "
        "FROM cards "
        "WHERE COALESCE(item_type, 'card') = 'card' "
        "  AND (folder_id = ? OR storage_code LIKE ?) "
        "ORDER BY name COLLATE NOCASE, id",
        (ordner_id, praefix + "%")).fetchall()
    karten = []
    for zeile in zeilen:
        karte = dict(zeile)
        teile = platz_teile(karte["storage_code"])
        # Ein Platz eines anderen Ordners zählt hier nicht als Platz.
        if teile and teile[0] != int(ordner_id):
            teile = None
        karte["platz_teile"] = teile
        karten.append(karte)
    return karten


def seitenzahl(conn: sqlite3.Connection, ordner_id: int) -> int:
    """Seiten laut Ordner — oder mehr, wenn Karten auf späteren Seiten liegen."""
    info = ordner(conn, ordner_id) or {}
    seiten = int(info.get("pages") or 0)
    for karte in _karten(conn, ordner_id):
        if karte["platz_teile"]:
            seiten = max(seiten, karte["platz_teile"][1])
    return max(seiten, 1)


def _gezaehlt(karte: Dict, seit: Optional[str]) -> bool:
    if not karte.get("gezaehlt_am"):
        return False
    return not seit or karte["gezaehlt_am"] >= seit


def seite(conn: sqlite3.Connection, ordner_id: int, nummer: int) -> Dict:
    """Eine Seite als neun Fächer, dazu Karten ohne gültigen Platz.

    ``faecher``: Liste von neun Fächern (Platz 1–9) mit ihren Karten — ein
    Fach kann mehrere Karten tragen. ``ohne_platz``: Karten des Ordners ohne
    Platz. ``ungueltig``: Karten auf einem Platz ab 10, den es nicht gibt.
    """
    info = ordner(conn, ordner_id) or {}
    seit = info.get("inventur_seit")
    faecher = [{"platz": p, "code": platz_code(ordner_id, nummer, p), "karten": []}
               for p in range(1, PLAETZE_JE_SEITE + 1)]
    ohne_platz, ungueltig = [], []
    for karte in _karten(conn, ordner_id):
        karte["gezaehlt"] = _gezaehlt(karte, seit)
        teile = karte["platz_teile"]
        if not teile:
            ohne_platz.append(karte)
        elif not 1 <= teile[2] <= PLAETZE_JE_SEITE:
            ungueltig.append(karte)
        elif teile[1] == nummer:
            faecher[teile[2] - 1]["karten"].append(karte)
    return {"faecher": faecher, "ohne_platz": ohne_platz, "ungueltig": ungueltig}


def uebersicht(conn: sqlite3.Connection, ordner_id: int) -> List[Dict]:
    """Je Seite: belegte Fächer, Stückzahl und ob sie in dieser Inventur geprüft ist."""
    info = ordner(conn, ordner_id) or {}
    seit = info.get("inventur_seit") or ""
    anzahl = seitenzahl(conn, ordner_id)
    seiten = {n: {"seite": n, "faecher": set(), "stueck": 0, "geprueft": False,
                  "geprueft_am": None}
              for n in range(1, anzahl + 1)}
    for karte in _karten(conn, ordner_id):
        teile = karte["platz_teile"]
        if teile and 1 <= teile[2] <= PLAETZE_JE_SEITE and teile[1] in seiten:
            seiten[teile[1]]["faecher"].add(teile[2])
            seiten[teile[1]]["stueck"] += karte["quantity"] or 0
    for nummer, am in conn.execute(
            "SELECT seite, geprueft_am FROM inventur_seiten WHERE ordner_id = ?",
            (ordner_id,)):
        if nummer in seiten:
            seiten[nummer]["geprueft_am"] = am
            seiten[nummer]["geprueft"] = am >= seit
    ergebnis = []
    for nummer in sorted(seiten):
        eintrag = seiten[nummer]
        eintrag["belegte_plaetze"] = sorted(eintrag.pop("faecher"))
        eintrag["belegt"] = len(eintrag["belegte_plaetze"])
        ergebnis.append(eintrag)
    return ergebnis


def fortschritt(conn: sqlite3.Connection, ordner_id: int) -> Dict:
    """Wie weit die laufende Inventur dieses Ordners ist."""
    info = ordner(conn, ordner_id) or {}
    seit = info.get("inventur_seit")
    seiten = uebersicht(conn, ordner_id)
    karten = [k for k in _karten(conn, ordner_id)]
    return {
        "seit": seit,
        "seiten": len(seiten),
        "seiten_geprueft": sum(1 for s in seiten if s["geprueft"]),
        "karten": len(karten),
        "karten_gezaehlt": sum(1 for k in karten if _gezaehlt(k, seit)),
    }


def suche(conn: sqlite3.Connection, ordner_id: int, text: str) -> List[Dict]:
    """Karten dieses Ordners, deren Name oder Nummer den Text enthält."""
    text = (text or "").strip().casefold()
    if not text:
        return []
    treffer = []
    for karte in _karten(conn, ordner_id):
        if text in (karte["name"] or "").casefold() or \
                text == str(karte["collector_number"] or "").casefold():
            treffer.append(karte)
    return treffer[:50]


# ---------------------------------------------------------------------------
# Inventur
# ---------------------------------------------------------------------------
def _karte_im_ordner(conn: sqlite3.Connection, ordner_id: int, card_id: int) -> Dict:
    for karte in _karten(conn, ordner_id):
        if karte["id"] == card_id:
            return karte
    raise OrdnerFehler("Diese Karte liegt nicht (mehr) in diesem Ordner.")


def menge_aendern(conn: sqlite3.Connection, ordner_id: int, card_id: int,
                  schritt: int, benutzer: str) -> int:
    """Die Menge um ``schritt`` ändern, nie unter 1. Gibt die neue Menge zurück.

    Für „ist gar nicht da" gibt es :func:`fehlt` — eine Menge 0 ließe eine
    leere Zeile auf dem Platz zurück.
    """
    karte = _karte_im_ordner(conn, ordner_id, card_id)
    vorher = karte["quantity"] or 0
    nachher = max(1, vorher + int(schritt))
    jetzt = _jetzt()
    cursor = conn.cursor()
    cursor.execute("UPDATE cards SET quantity = ?, gezaehlt_am = ? WHERE id = ?",
                   (nachher, jetzt, card_id))
    if nachher != vorher:
        log_audit(card_id, benutzer, "inventur", "quantity", str(vorher),
                  str(nachher), cursor)
    conn.commit()
    return nachher


def bestaetigen(conn: sqlite3.Connection, ordner_id: int, card_id: int,
                benutzer: str) -> None:
    """Die Karte ist da, die Menge stimmt."""
    _karte_im_ordner(conn, ordner_id, card_id)
    conn.execute("UPDATE cards SET gezaehlt_am = ? WHERE id = ?",
                 (_jetzt(), card_id))
    conn.commit()


def fehlt(conn: sqlite3.Connection, ordner_id: int, card_id: int,
          benutzer: str) -> Dict:
    """Die Karte ist nicht da: aus dem Bestand entfernen, Platz freigeben."""
    karte = _karte_im_ordner(conn, ordner_id, card_id)
    log_audit(card_id, benutzer, "inventur-fehlt", "quantity",
              str(karte["quantity"] or 0), "0", conn.cursor())
    conn.commit()
    delete_card(card_id, benutzer)
    return karte


def seite_geprueft(conn: sqlite3.Connection, ordner_id: int, nummer: int,
                   benutzer: str) -> int:
    """Alles auf dieser Seite ist wie angezeigt. Gibt die Zahl der Karten zurück."""
    if nummer < 1:
        raise OrdnerFehler("Diese Seite gibt es nicht.")
    jetzt = _jetzt()
    karten = [k for fach in seite(conn, ordner_id, nummer)["faecher"]
              for k in fach["karten"]]
    cursor = conn.cursor()
    cursor.executemany("UPDATE cards SET gezaehlt_am = ? WHERE id = ?",
                       [(jetzt, k["id"]) for k in karten])
    cursor.execute(
        "INSERT INTO inventur_seiten (ordner_id, seite, geprueft_am, benutzer) "
        "VALUES (?, ?, ?, ?) ON CONFLICT(ordner_id, seite) DO UPDATE SET "
        "geprueft_am = excluded.geprueft_am, benutzer = excluded.benutzer",
        (ordner_id, nummer, jetzt, benutzer))
    log_audit(None, benutzer, "inventur-seite", "folder",
              f"Ordner {ordner_id}, Seite {nummer}",
              f"{len(karten)} Karte(n) geprüft", cursor)
    conn.commit()
    return len(karten)


def inventur_beginnen(conn: sqlite3.Connection, ordner_id: int,
                      benutzer: str) -> str:
    """Eine neue Inventur dieses Ordners beginnen — der Fortschritt beginnt bei null."""
    if ordner(conn, ordner_id) is None:
        raise OrdnerFehler("Diesen Ordner gibt es nicht.")
    jetzt = _jetzt()
    cursor = conn.cursor()
    cursor.execute("UPDATE folders SET inventur_seit = ? WHERE id = ?",
                   (jetzt, ordner_id))
    log_audit(None, benutzer, "inventur-beginn", "folder",
              f"Ordner {ordner_id}", jetzt, cursor)
    conn.commit()
    return jetzt
