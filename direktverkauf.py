"""Direktverkauf: Bestellungen, die nicht von Cardmarket kommen.

Nicht jeder Verkauf läuft über Cardmarket — auf dem Flohmarkt oder im
Direktkontakt entsteht keine Bestellmail. Trotzdem soll derselbe Weg gelten:
eine Bestellung unter „Bestellungen", ein Dokument zum Ausdrucken, der
Lagerplatz wird frei, und die Buchhaltung holt sich den Umsatz über die API.

Was hier **nicht** passiert:

* **Keine Preise raten.** Was die Karte im Bestand gekostet hat, ist nicht der
  Verkaufspreis. Jede Position bekommt ihren Preis von Hand.
* **Kein zweiter Bestellweg.** Die Zeilen landen in denselben Tabellen
  (``orders``/``order_items``) wie die Cardmarket-Bestellungen, nur mit
  ``quelle = 'manuell'``. Alles Nachgelagerte — Beileger, API, Buchhaltung —
  funktioniert dadurch unverändert weiter.
* **Keine Plattformgebühr.** Die fällt bei Cardmarket an, nicht hier. Der
  Verkaufskanal wandert bis in die Buchhaltung durch.
"""

from __future__ import annotations

import sqlite3
import uuid
from datetime import datetime
from typing import Dict, List, Optional, Sequence

from . import DB_FILE
from .lager_manager import log_audit, sell_card

#: Kanäle, über die ohne Cardmarket verkauft wird.
KANAELE = {
    "direktverkauf": "Direktverkauf",
    "flohmarkt": "Flohmarkt",
}

#: Dokumentarten zum Ausdrucken.
BELEGARTEN = {
    "beileger": "Beileger (mit Adresse, für Versand)",
    "quittung": "Quittung (ohne Adresse, für vor Ort)",
}


class DirektverkaufFehler(ValueError):
    """Fachlicher Fehler — der Text ist für die Anzeige gedacht."""


def _preis(wert) -> float:
    """Preisangabe lesen. Komma und Punkt sind beide erlaubt."""
    if wert is None or str(wert).strip() == "":
        raise DirektverkaufFehler("Bitte für jede Position einen Preis angeben.")
    text = str(wert).strip().replace("€", "").replace(" ", "")
    if "," in text and "." in text:
        text = text.replace(".", "")          # 1.234,56
    text = text.replace(",", ".")
    try:
        preis = float(text)
    except ValueError:
        raise DirektverkaufFehler(f"„{wert}“ ist kein Preis.") from None
    if preis < 0:
        raise DirektverkaufFehler("Ein Preis kann nicht negativ sein.")
    return round(preis, 2)


def naechste_nummer(cursor) -> str:
    """Fortlaufende Nummer für Direktverkäufe: DV-2026-0001.

    Bewusst von den Cardmarket-Bestellnummern unterscheidbar — beim Blick auf
    ein gedrucktes Dokument soll sofort klar sein, woher der Verkauf kam.
    """
    jahr = datetime.now().year
    cursor.execute(
        "SELECT order_number FROM orders WHERE order_number LIKE ? "
        "ORDER BY order_number DESC LIMIT 1", (f"DV-{jahr}-%",))
    letzte = cursor.fetchone()
    laufend = int(letzte[0].rsplit("-", 1)[1]) + 1 if letzte else 1
    return f"DV-{jahr}-{laufend:04d}"


def erstelle_bestellung(
    positionen: Sequence[Dict],
    kaeufer: str = "",
    kanal: str = "direktverkauf",
    versand: float = 0.0,
    adresse: str = "",
    datum: Optional[str] = None,
    notiz: str = "",
    benutzer: str = "system",
    db_file: Optional[str] = None,
) -> Dict:
    """Einen Direktverkauf als Bestellung anlegen.

    ``positionen`` ist eine Liste von Angaben je verkaufter Karte:
    ``{"card_id": 12, "quantity": 1, "unit_price": "3,50"}``. Statt einer
    ``card_id`` darf auch ein freier ``card_name`` stehen — für Dinge, die gar
    nicht im Bestand geführt werden (eine Einzelkarte aus der Wühlkiste, ein
    Display aus dem Lager).

    Karten mit ``card_id`` werden **ausgebucht**: die Menge sinkt, und beim
    letzten Exemplar wird der Lagerplatz frei. Das ist derselbe Weg wie beim
    Knopf „verkauft" in der Kartenübersicht.

    Gibt ``{"order_id": …, "order_number": …}`` zurück.
    """
    if not positionen:
        raise DirektverkaufFehler("Ohne Position gibt es nichts zu verkaufen.")
    if kanal not in KANAELE:
        raise DirektverkaufFehler(
            f"Unbekannter Verkaufskanal „{kanal}“. Möglich: "
            + ", ".join(KANAELE))

    versand = _preis(versand) if versand else 0.0
    tag = datum or datetime.now().strftime("%Y-%m-%d")
    pfad = db_file or DB_FILE

    # Erst prüfen, dann schreiben: eine halb angelegte Bestellung mit schon
    # ausgebuchten Karten wäre die schlechteste aller Zwischenstufen.
    vorbereitet: List[Dict] = []
    with sqlite3.connect(pfad) as conn:
        cursor = conn.cursor()
        for nr, position in enumerate(positionen, start=1):
            # Kein "or 1": eine eingetippte 0 würde damit stillschweigend zu
            # einer 1. Fehlt die Angabe ganz, ist 1 die naheliegende Menge.
            roh = position.get("quantity")
            menge = 1 if roh in (None, "") else int(roh)
            if menge < 1:
                raise DirektverkaufFehler(
                    f"Position {nr}: die Menge muss mindestens 1 sein.")
            preis = _preis(position.get("unit_price"))
            karte_id = position.get("card_id")

            if karte_id:
                # Die Kartentabelle führt nur den Set-Code, keinen Set-Namen;
                # auf dem Beleg steht deshalb der Code in Großbuchstaben.
                cursor.execute(
                    "SELECT name, set_code, language, condition, foil, "
                    "quantity, storage_code, image_url FROM cards WHERE id = ?",
                    (int(karte_id),))
                karte = cursor.fetchone()
                if not karte:
                    raise DirektverkaufFehler(
                        f"Position {nr}: Karte {karte_id} gibt es nicht mehr.")
                if (karte[5] or 0) < menge:
                    raise DirektverkaufFehler(
                        f"Position {nr}: von „{karte[0]}“ sind nur "
                        f"{karte[5] or 0} Stück im Bestand, verkauft werden "
                        f"sollen {menge}.")
                vorbereitet.append({
                    "card_id": int(karte_id), "quantity": menge,
                    "unit_price": preis, "card_name": karte[0],
                    "set_code": karte[1],
                    "set_name": (karte[1] or "").upper() or None,
                    "language": karte[2], "condition": karte[3],
                    "foil": karte[4] or 0, "storage_code": karte[6],
                    "image_url": karte[7],
                })
                continue

            name = (position.get("card_name") or "").strip()
            if not name:
                raise DirektverkaufFehler(
                    f"Position {nr}: entweder eine Karte aus dem Bestand "
                    f"wählen oder eine Bezeichnung eintragen.")
            vorbereitet.append({
                "card_id": None, "quantity": menge, "unit_price": preis,
                "card_name": name,
                "set_code": (position.get("set_code") or "").strip() or None,
                "set_name": (position.get("set_name") or "").strip() or None,
                "language": (position.get("language") or "").strip() or None,
                "condition": (position.get("condition") or "").strip() or None,
                "foil": 1 if position.get("foil") else 0,
                "storage_code": None, "image_url": None,
            })

        warenwert = round(sum(p["quantity"] * p["unit_price"] for p in vorbereitet), 2)
        gesamt = round(warenwert + versand, 2)
        nummer = naechste_nummer(cursor)

        # Status gleich 'sold': der Verkauf ist gelaufen, das Geld da und die
        # Karte weg. Stünde er als „offen" in der Liste, könnte ein zweiter
        # Klick auf „verkauft" dieselbe Karte ein weiteres Mal ausbuchen.
        cursor.execute(
            "INSERT INTO orders (buyer_name, email_message_id, date_received, "
            "status, date_completed, order_number, address, address_raw, "
            "email_date, quelle, verkaufskanal, amount_gesamtwert, "
            "amount_versand, amount_gesamt, amount_gebuehren, "
            "amount_auszahlung, address_confirmed) "
            "VALUES (?, ?, ?, 'sold', ?, ?, ?, ?, ?, 'manuell', ?, ?, ?, ?, 0, ?, ?)",
            (kaeufer.strip() or "Direktverkauf",
             f"manuell:{uuid.uuid4()}",       # die Spalte ist NOT NULL UNIQUE
             tag, tag, nummer, adresse.strip() or None, adresse.strip() or None,
             tag, kanal, warenwert, versand, gesamt, gesamt,
             1 if adresse.strip() else 0))
        bestellung_id = cursor.lastrowid

        for p in vorbereitet:
            cursor.execute(
                "INSERT INTO order_items (order_id, card_name, quantity, "
                "image_url, storage_code, card_id, match_status, set_name, "
                "set_code, language, condition, foil, unit_price) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (bestellung_id, p["card_name"], p["quantity"], p["image_url"],
                 p["storage_code"], p["card_id"],
                 "manual" if p["card_id"] else "unresolved",
                 p["set_name"], p["set_code"], p["language"], p["condition"],
                 p["foil"], p["unit_price"]))

        log_audit(bestellung_id, benutzer, "direktverkauf", "order",
                  None, f"{nummer} ({KANAELE[kanal]}, {gesamt:.2f} €)", cursor)
        if notiz:
            cursor.execute("UPDATE orders SET buchung_pruefen = NULL WHERE id = ?",
                           (bestellung_id,))
        conn.commit()

    # Erst wenn die Bestellung steht, wird ausgebucht. Schlägt das fehl,
    # bleibt die Bestellung erhalten und der Bestand lässt sich von Hand
    # nachziehen — umgekehrt wäre die Karte weg und der Beleg fehlte.
    #
    # Der Rückgabewert von sell_card wird ausgewertet: geht das Ausbuchen
    # schief, stünde sonst ein Verkauf im System, während die Karte weiter im
    # Regal liegt. Das fällt niemandem auf — außer beim nächsten Zählen.
    nicht_ausgebucht: List[str] = []
    for p in vorbereitet:
        if not p["card_id"]:
            continue
        for _ in range(p["quantity"]):
            if not sell_card(p["card_id"], user=benutzer):
                nicht_ausgebucht.append(p["card_name"])
                break

    return {"order_id": bestellung_id, "order_number": nummer,
            "warenwert": warenwert, "versand": versand, "gesamt": gesamt,
            "nicht_ausgebucht": nicht_ausgebucht}
