"""Buchhaltungsmodul (WP3b): EÜR, Belege, Briefmarken, Versand-Marge.

Rahmen
------
* Gewinnermittlung per **EÜR** nach dem **Zufluss-/Abflussprinzip**.
* **Kleinunternehmer nach § 19 UStG**: keine Umsatzsteuer, keine Vorsteuer,
  keine Steuersätze — alle Beträge sind Bruttobeträge = Buchungsbeträge.
* Keine Steuerberechnung; das Modul liefert Summen, keine Steuererklärung.
* Beträge durchgängig als **Integer in Cent** (nie Float).

Grundregel 1 — Unveränderbarkeit
    Das Journal ist **append-only**: nie ändern, nie löschen. Korrekturen
    ausschließlich per Stornobuchung mit Verweis. Erzwungen per Trigger in
    ``setup_db.py``; dieses Modul kapselt nur die erlaubten Operationen.

Grundregel 2 — Datenherkunft
    Einnahmen und Gebühren stammen ausschließlich aus den Bestelldaten der
    Cardmarket-Mail. Der Dragonshield-Export (``Price Bought``) ist **keine**
    Finanzquelle und wird hier weder direkt noch indirekt gelesen.

Porto — die eiserne Regel
    Porto wird **genau einmal** zur Ausgabe: beim **Kauf** der Briefmarke.
    Das Verbrauchen einer Marke beim Versand erzeugt **niemals** eine Buchung,
    sondern nur einen Bestandsabgang plus einen historisch eingefrorenen
    Portowert. Der Versand-Überschuss (vereinnahmter Versand minus echtes
    Porto) entsteht dadurch automatisch als Gewinn und wird nicht gebucht.
"""

from __future__ import annotations

import csv
import hashlib
import io
import os
import re
import sqlite3
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional, Sequence, Tuple

from . import DB_FILE

# ---------------------------------------------------------------------------
# Kategorien (fest vorgegeben, erweiterbar)
# ---------------------------------------------------------------------------
KATEGORIEN_EINNAHME = [
    "Warenverkauf",
    "Vereinnahmte Versandkosten",
    "Sonstige Einnahmen",
]
KATEGORIEN_AUSGABE = [
    "Wareneinkauf",
    "Porto/Briefmarken",
    "Verpackungsmaterial",
    "Cardmarket-Gebühren",
    "Bürobedarf",
    "Sonstige Ausgaben",
]

KAT_WARENVERKAUF = "Warenverkauf"
KAT_VERSANDEINNAHME = "Vereinnahmte Versandkosten"
KAT_GEBUEHREN = "Cardmarket-Gebühren"
KAT_PORTO = "Porto/Briefmarken"

BELEGE_DIR = Path(__file__).resolve().parent / "data" / "belege"
ALLOWED_RECEIPT_EXT = {".pdf", ".jpg", ".jpeg", ".png"}

CSV_DELIMITER = ";"
CSV_ENCODING = "utf-8-sig"

# Schwellwert, ab dem in der Übersicht auf niedrigen Markenbestand hingewiesen wird.
BESTAND_WARNUNG = 5

# Geschäftsbeginn (Gründung der GbR): 01.06.2026. Bestellungen davor stammen aus
# der Zeit vor der Gründung und gehören nicht in die Buchhaltung — sie werden
# deshalb gar nicht erst zur Übernahme angeboten. Der Stichtag zählt inklusive:
# eine Bestellung vom 01.06. gehört dazu. Über die Umgebungsvariable
# ``TCG_GESCHAEFTSBEGINN`` (Format JJJJ-MM-TT) anpassbar.
GESCHAEFTSBEGINN = os.environ.get("TCG_GESCHAEFTSBEGINN", "2026-06-01")


# ---------------------------------------------------------------------------
# Betrags- und Datums-Helfer (immer Cent)
# ---------------------------------------------------------------------------
def to_cent(value) -> int:
    """Euro-Wert (float/str, auch mit Komma) verlustfrei in Cent umrechnen."""
    if value is None or value == "":
        return 0
    if isinstance(value, int):
        return value * 100
    s = str(value).strip().replace(" ", "").replace("€", "")
    if "," in s and "." in s:
        s = s.replace(".", "").replace(",", ".")
    elif "," in s:
        s = s.replace(",", ".")
    try:
        return int(round(float(s) * 100))
    except ValueError:
        return 0


def cent_to_de(cents: int) -> str:
    """Cent -> deutsches Format ohne Währungszeichen: ``1234`` -> ``12,34``."""
    cents = int(cents or 0)
    sign = "-" if cents < 0 else ""
    cents = abs(cents)
    return f"{sign}{cents // 100},{cents % 100:02d}"


def de_date(value) -> str:
    """ISO-Datum/Zeitstempel -> ``TT.MM.JJJJ``."""
    s = str(value or "").strip()
    if not s:
        return ""
    head = s.split("T")[0].split(" ")[0]
    try:
        return datetime.strptime(head, "%Y-%m-%d").strftime("%d.%m.%Y")
    except ValueError:
        return s


def _connect(db_file: Optional[str] = None) -> sqlite3.Connection:
    conn = sqlite3.connect(db_file or DB_FILE)
    conn.row_factory = sqlite3.Row
    return conn


# ---------------------------------------------------------------------------
# Buchen (append-only)
# ---------------------------------------------------------------------------
def add_booking(
    buchungsdatum: str,
    art: str,
    kategorie: str,
    betrag_cent: int,
    beschreibung: str = "",
    bestellung_id: Optional[int] = None,
    beleg_id: Optional[int] = None,
    storniert_buchung_id: Optional[int] = None,
    zahlungseingang_am: Optional[str] = None,
    db_file: Optional[str] = None,
) -> int:
    """Neue Buchung anhängen und deren ``id`` zurückgeben.

    ``lfd_nr`` wird systemvergeben und ist fortlaufend und lückenlos (es gibt
    keine Löschungen).
    """
    if art not in ("einnahme", "ausgabe", "storno"):
        raise ValueError(f"Ungültige Buchungsart: {art}")
    with _connect(db_file) as conn:
        c = conn.cursor()
        c.execute("BEGIN IMMEDIATE")
        next_nr = c.execute("SELECT COALESCE(MAX(lfd_nr), 0) + 1 FROM journal").fetchone()[0]
        c.execute(
            """
            INSERT INTO journal (lfd_nr, erfasst_am, buchungsdatum, art, kategorie,
                                 betrag_cent, beschreibung, bestellung_id, beleg_id,
                                 storniert_buchung_id, zahlungseingang_am)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (next_nr, datetime.now().isoformat(), buchungsdatum, art, kategorie,
             int(betrag_cent), beschreibung, bestellung_id, beleg_id,
             storniert_buchung_id, zahlungseingang_am),
        )
        new_id = c.lastrowid
        conn.commit()
    return new_id


def storno_booking(buchung_id: int, grund: str = "", db_file: Optional[str] = None) -> int:
    """Buchung stornieren: erzeugt eine neue Stornozeile mit Verweis.

    Die ursprüngliche Buchung bleibt unverändert; nur ihr Rückverweis
    ``storniert_durch`` wird einmalig nachgetragen. Ein Storno kann selbst nicht
    storniert werden, eine Buchung nicht zweimal.
    """
    with _connect(db_file) as conn:
        row = conn.execute("SELECT * FROM journal WHERE id = ?", (buchung_id,)).fetchone()
        if not row:
            raise ValueError("Buchung nicht gefunden")
        if row["art"] == "storno":
            raise ValueError("Eine Stornobuchung kann nicht storniert werden")
        if row["storniert_durch"] is not None:
            raise ValueError("Buchung ist bereits storniert")

    text = f"Storno zu Buchung #{row['lfd_nr']}"
    if grund:
        text += f": {grund}"
    storno_id = add_booking(
        buchungsdatum=datetime.now().strftime("%Y-%m-%d"),
        art="storno",
        kategorie=row["kategorie"],
        betrag_cent=row["betrag_cent"],
        beschreibung=text,
        bestellung_id=row["bestellung_id"],
        storniert_buchung_id=buchung_id,
        db_file=db_file,
    )
    with _connect(db_file) as conn:
        # Einmaliges Nachtragen des Rückverweises (vom Trigger erlaubt).
        conn.execute("UPDATE journal SET storniert_durch = ? WHERE id = ?",
                     (storno_id, buchung_id))
        conn.commit()

    # War es ein Markenkauf, verschwindet der zugehörige Sofort-Verbrauch mit —
    # sonst bliebe ein Verbrauch ohne Kauf stehen und der Bestand ginge ins Minus.
    if row["kategorie"] == KAT_PORTO:
        _entferne_verbrauch_zum_kauf(buchung_id, db_file)
    return storno_id


# ---------------------------------------------------------------------------
# Einnahmen aus Bestellungen (nur Maildaten)
# ---------------------------------------------------------------------------
def order_already_booked(order_id: int, db_file: Optional[str] = None) -> bool:
    """True, wenn die Bestellung eine *aktive* (nicht stornierte) Buchung hat.

    Eine stornierte Übernahme zählt nicht mehr — so lässt sich ein Fehlgriff per
    Storno korrigieren und die Bestellung danach erneut übernehmen.
    """
    with _connect(db_file) as conn:
        row = conn.execute(
            "SELECT 1 FROM journal WHERE bestellung_id = ? AND art <> 'storno' "
            "AND storniert_durch IS NULL LIMIT 1",
            (order_id,),
        ).fetchone()
    return row is not None


def _set_pruefgrund(order_id: int, grund: Optional[str], db_file: Optional[str] = None) -> None:
    with _connect(db_file) as conn:
        conn.execute("UPDATE orders SET buchung_pruefen = ? WHERE id = ?", (grund, order_id))
        conn.commit()


def book_order(order_id: int, db_file: Optional[str] = None) -> List[int]:
    """Eine versendete Bestellung übernehmen — erzeugt **genau drei** Buchungen:

      * ``Warenverkauf`` (Einnahme)              = Gesamtwert − Versandkosten
      * ``Vereinnahmte Versandkosten`` (Einnahme) = Versandkosten aus der Mail
      * ``Cardmarket-Gebühren`` (Ausgabe)         = Gebühren aus der Mail

    Das tatsächliche Porto wird hier **nicht** gebucht — es ist bereits beim
    Kauf der Briefmarke als Ausgabe erfasst.

    Buchungsdatum ist das **Versanddatum**; der steuerlich maßgebliche Zufluss
    wird separat über die Auszahlung gesetzt. Auffälligkeiten (fehlende
    Versandkosten, unstimmige Summen) landen in der Prüfliste — es wird nichts
    stillschweigend korrigiert. Eine Bestellung kann nur einmal übernommen
    werden (zusätzlich per UNIQUE-Index abgesichert).
    """
    with _connect(db_file) as conn:
        o = conn.execute(
            "SELECT id, order_number, buyer_name, email_date, date_received, "
            "date_completed, amount_gesamt, amount_gesamtwert, amount_versand, "
            "amount_gebuehren FROM orders WHERE id = ?",
            (order_id,),
        ).fetchone()
        if not o:
            raise ValueError("Bestellung nicht gefunden")
        items = conn.execute(
            "SELECT quantity, unit_price FROM order_items WHERE order_id = ?",
            (order_id,),
        ).fetchall()
    if order_already_booked(order_id, db_file):
        raise ValueError("Diese Bestellung wurde bereits übernommen")

    hinweise: List[str] = []

    versand_cent = to_cent(o["amount_versand"])
    if o["amount_versand"] is None:
        hinweise.append("Versandkosten fehlen in der Mail (mit 0,00 gebucht)")

    gebuehren_cent = to_cent(o["amount_gebuehren"])
    if o["amount_gebuehren"] is None:
        hinweise.append("Cardmarket-Gebühren fehlen in der Mail (mit 0,00 gebucht)")

    positionen_cent = sum(to_cent(it["unit_price"]) * int(it["quantity"] or 1)
                          for it in items)

    gesamt_cent = to_cent(o["amount_gesamt"])
    if gesamt_cent <= 0:
        # Ohne Gesamtwert aus der Mail den Warenwert aus den Positionen der
        # Mail bilden — ebenfalls Cardmarket-Daten, nur weniger belastbar.
        gesamt_cent = positionen_cent + versand_cent
        hinweise.append("Gesamtwert fehlte in der Mail – aus den Positionen gebildet")

    waren_cent = gesamt_cent - versand_cent

    # Konsistenzprüfung: Warenverkauf + Versandkosten muss dem Gesamtwert
    # entsprechen; die Positionssumme dient als Gegenprobe.
    if waren_cent + versand_cent != gesamt_cent:
        hinweise.append("Warenverkauf + Versand ergibt nicht den Gesamtwert")
    if positionen_cent and positionen_cent != waren_cent:
        hinweise.append(
            f"Positionssumme {cent_to_de(positionen_cent)} € weicht vom Warenwert "
            f"{cent_to_de(waren_cent)} € ab"
        )

    datum = str(o["date_completed"] or o["email_date"] or o["date_received"] or "")[:10]
    ref = f"Bestellung {o['order_number'] or order_id} ({o['buyer_name'] or ''})".strip()

    ids = [
        add_booking(datum, "einnahme", KAT_WARENVERKAUF, waren_cent, ref,
                    bestellung_id=order_id, db_file=db_file),
        add_booking(datum, "einnahme", KAT_VERSANDEINNAHME, versand_cent, ref,
                    bestellung_id=order_id, db_file=db_file),
        add_booking(datum, "ausgabe", KAT_GEBUEHREN, gebuehren_cent, ref,
                    bestellung_id=order_id, db_file=db_file),
    ]
    _set_pruefgrund(order_id, "; ".join(hinweise) if hinweise else None, db_file)
    return ids


def pruefliste(db_file: Optional[str] = None) -> List[dict]:
    """Bestellungen, deren Buchung geprüft werden sollte."""
    with _connect(db_file) as conn:
        rows = conn.execute(
            "SELECT id, order_number, buyer_name, buchung_pruefen, "
            "amount_gesamt, amount_versand, amount_gebuehren FROM orders "
            "WHERE buchung_pruefen IS NOT NULL AND buchung_pruefen <> '' "
            "ORDER BY id DESC"
        ).fetchall()
    return [dict(r) for r in rows]


def bookable_orders(db_file: Optional[str] = None) -> List[dict]:
    """Versendete Bestellungen ab Geschäftsbeginn, die noch nicht gebucht sind.

    Bestellungen von vor dem Geschäftsbeginn werden gar nicht erst angeboten —
    sie stammen aus der Zeit vor der Gründung und gehören nicht in die EÜR.
    """
    with _connect(db_file) as conn:
        rows = conn.execute(
            """
            SELECT o.id, o.order_number, o.buyer_name,
                   COALESCE(o.date_completed, o.email_date, o.date_received) AS datum,
                   o.amount_gesamt, o.amount_versand, o.amount_gebuehren,
                   o.amount_auszahlung
            FROM orders o
            WHERE o.status = 'sold'
              AND substr(COALESCE(o.date_completed, o.email_date, o.date_received), 1, 10) >= ?
              AND NOT EXISTS (SELECT 1 FROM journal j
                              WHERE j.bestellung_id = o.id AND j.art <> 'storno'
                                AND j.storniert_durch IS NULL)
            ORDER BY datum DESC
            """, (GESCHAEFTSBEGINN,)
        ).fetchall()
    return [dict(r) for r in rows]


def count_vor_geschaeftsbeginn(db_file: Optional[str] = None) -> int:
    """Wie viele versendete, ungebuchte Bestellungen liegen vor Geschäftsbeginn?

    Nur zur Information in der Oberfläche — sie werden bewusst ausgeblendet.
    """
    with _connect(db_file) as conn:
        return conn.execute(
            "SELECT COUNT(*) FROM orders o WHERE o.status = 'sold' "
            "AND substr(COALESCE(o.date_completed, o.email_date, o.date_received), 1, 10) < ? "
            "AND NOT EXISTS (SELECT 1 FROM journal j WHERE j.bestellung_id = o.id "
            "AND j.art <> 'storno' AND j.storniert_durch IS NULL)",
            (GESCHAEFTSBEGINN,)
        ).fetchone()[0]


# ---------------------------------------------------------------------------
# Briefmarken: Stammdaten, Kauf (bucht), Verbrauch (bucht NIE)
# ---------------------------------------------------------------------------
def list_markenarten(only_active: bool = False, db_file: Optional[str] = None) -> List[dict]:
    """Markenarten inkl. abgeleitetem Bestand.

    Bestand = Inventurkorrektur + gekaufte Stück (ohne stornierte Käufe)
              − verbrauchte Stück.
    """
    with _connect(db_file) as conn:
        q = """
            SELECT m.id, m.bezeichnung, m.nennwert_cent, m.aktiv, m.bestand_korrektur,
                   COALESCE((SELECT SUM(k.stueckzahl) FROM markenkauf k
                             LEFT JOIN journal j ON j.lfd_nr = k.journal_lfd_nr
                             WHERE k.markenart_id = m.id
                               AND (j.id IS NULL OR j.storniert_durch IS NULL)), 0) AS gekauft,
                   COALESCE((SELECT SUM(v.stueckzahl) FROM markenverbrauch v
                             WHERE v.markenart_id = m.id), 0) AS verbraucht
            FROM markenart m
        """
        if only_active:
            q += " WHERE m.aktiv = 1"
        q += " ORDER BY m.aktiv DESC, m.nennwert_cent"
        rows = [dict(r) for r in conn.execute(q).fetchall()]
    for r in rows:
        # Nie unter null: ein negativer Bestand waere physisch unmöglich und
        # nur ein Zeichen für inkonsistente Daten.
        r["bestand"] = max(0, r["bestand_korrektur"] + r["gekauft"] - r["verbraucht"])
        r["warnung"] = r["aktiv"] == 1 and r["bestand"] < BESTAND_WARNUNG
    return rows


def get_markenart(markenart_id: int, db_file: Optional[str] = None) -> Optional[dict]:
    for m in list_markenarten(db_file=db_file):
        if m["id"] == int(markenart_id):
            return m
    return None


def add_markenart(bezeichnung: str, nennwert_cent: int, db_file: Optional[str] = None) -> int:
    bezeichnung = (bezeichnung or "").strip()
    nennwert_cent = int(nennwert_cent)
    if not bezeichnung or nennwert_cent <= 0:
        raise ValueError("Bezeichnung und Nennwert (> 0) sind erforderlich.")
    with _connect(db_file) as conn:
        c = conn.cursor()
        c.execute("INSERT INTO markenart (bezeichnung, nennwert_cent) VALUES (?, ?)",
                  (bezeichnung, nennwert_cent))
        conn.commit()
        return c.lastrowid


def change_nennwert(markenart_id: int, neuer_nennwert_cent: int,
                    db_file: Optional[str] = None) -> int:
    """Portoerhöhung: legt einen **neuen** Stammsatz an und deaktiviert den alten.

    Bestehende Buchungen und bereits erfasste Verbräuche bleiben unverändert —
    sie verweisen weiterhin auf den alten Stammsatz mit dessen historischem
    Nennwert. Vorhandene alte Marken behalten dadurch ihren Wert.
    """
    neuer_nennwert_cent = int(neuer_nennwert_cent)
    if neuer_nennwert_cent <= 0:
        raise ValueError("Nennwert muss größer als 0 sein.")
    with _connect(db_file) as conn:
        row = conn.execute("SELECT bezeichnung, nennwert_cent FROM markenart WHERE id = ?",
                           (markenart_id,)).fetchone()
        if not row:
            raise ValueError("Markenart nicht gefunden")
        if row["nennwert_cent"] == neuer_nennwert_cent:
            return int(markenart_id)
        c = conn.cursor()
        c.execute("INSERT INTO markenart (bezeichnung, nennwert_cent) VALUES (?, ?)",
                  (row["bezeichnung"], neuer_nennwert_cent))
        neue_id = c.lastrowid
        c.execute("UPDATE markenart SET aktiv = 0 WHERE id = ?", (markenart_id,))
        conn.commit()
        return neue_id


def set_markenart_aktiv(markenart_id: int, aktiv: bool, db_file: Optional[str] = None) -> None:
    with _connect(db_file) as conn:
        conn.execute("UPDATE markenart SET aktiv = ? WHERE id = ?",
                     (1 if aktiv else 0, markenart_id))
        conn.commit()


def set_bestand(markenart_id: int, ziel_bestand: int, db_file: Optional[str] = None) -> int:
    """Physische Inventur: Bestand einer Markenart auf ``ziel_bestand`` setzen.

    Angepasst wird nur die Inventurkorrektur — **keine** Buchung, da hierbei
    kein Geld fließt.
    """
    m = get_markenart(markenart_id, db_file)
    if not m:
        raise ValueError("Markenart nicht gefunden")
    ziel = max(0, int(ziel_bestand))
    neue_korrektur = ziel - (m["gekauft"] - m["verbraucht"])
    with _connect(db_file) as conn:
        conn.execute("UPDATE markenart SET bestand_korrektur = ? WHERE id = ?",
                     (neue_korrektur, markenart_id))
        conn.commit()
    return ziel


def buy_stamps(datum: str, markenart_id: int, stueckzahl: int, betrag_cent: int,
               beleg_id: Optional[int] = None, bestellung_id: Optional[int] = None,
               db_file: Optional[str] = None) -> int:
    """Markenkauf — der **einzige** Weg, wie Porto zur Ausgabe wird.

    Erzeugt genau **eine** Ausgabebuchung (Kategorie ``Porto/Briefmarken``,
    Buchungsdatum = Kaufdatum, Abflussprinzip) und einen Bestandszugang.
    Identisch für Vorratskauf und Sofortkauf. Gibt die Journal-``id`` zurück.

    ``bestellung_id`` wird beim Sofortkauf gesetzt, damit die Ausgabe im Block
    der Bestellung erscheint und nicht unter „Sonstige Buchungen" verschwindet.
    """
    stueckzahl = int(stueckzahl)
    betrag_cent = int(betrag_cent)
    if stueckzahl <= 0 or betrag_cent <= 0:
        raise ValueError("Stückzahl und Betrag müssen größer als 0 sein.")
    m = get_markenart(markenart_id, db_file)
    if not m:
        raise ValueError("Markenart nicht gefunden")
    if not datum:
        raise ValueError("Kaufdatum ist erforderlich.")

    beschr = f"Briefmarkenkauf {stueckzahl}× {m['bezeichnung']} ({cent_to_de(m['nennwert_cent'])} €)"
    booking_id = add_booking(datum, "ausgabe", KAT_PORTO, betrag_cent, beschr,
                             bestellung_id=bestellung_id, beleg_id=beleg_id,
                             db_file=db_file)
    with _connect(db_file) as conn:
        lfd_nr = conn.execute("SELECT lfd_nr FROM journal WHERE id = ?",
                              (booking_id,)).fetchone()["lfd_nr"]
        conn.execute(
            "INSERT INTO markenkauf (datum, markenart_id, stueckzahl, betrag_cent, "
            "journal_lfd_nr, beleg_id) VALUES (?, ?, ?, ?, ?, ?)",
            (datum, int(markenart_id), stueckzahl, betrag_cent, lfd_nr, beleg_id),
        )
        conn.commit()
    return booking_id


def consume_stamps(markenart_id: int, stueckzahl: int = 1,
                   bestellung_id: Optional[int] = None, datum: Optional[str] = None,
                   markenkauf_id: Optional[int] = None,
                   db_file: Optional[str] = None) -> int:
    """Marken beim Versand verbrauchen — erzeugt **niemals** eine Buchung.

    Speichert den Portowert zum Zeitpunkt des Verbrauchs (historisch
    eingefroren). Dieser Wert ist eine reine Info-Größe für die Versand-Marge
    und fließt nie in die Einnahmen-/Ausgabensummen der EÜR ein.

    Ohne ausreichenden Bestand wird abgelehnt — ein negativer Vorrat wäre
    physisch unmöglich und würde nur stillschweigend falsche Zahlen erzeugen.
    """
    stueckzahl = int(stueckzahl)
    if stueckzahl <= 0:
        raise ValueError("Stückzahl muss größer als 0 sein.")
    m = get_markenart(markenart_id, db_file)
    if not m:
        raise ValueError("Markenart nicht gefunden")
    if m["bestand"] < stueckzahl:
        raise ValueError(
            f"Nicht genug Marken vom Typ {m['bezeichnung']} im Vorrat "
            f"(vorhanden {m['bestand']}, gebraucht {stueckzahl}). "
            f"Bitte zuerst welche kaufen oder 'Sofort gekauft' wählen.")
    datum = datum or datetime.now().strftime("%Y-%m-%d")
    with _connect(db_file) as conn:
        c = conn.cursor()
        c.execute(
            "INSERT INTO markenverbrauch (bestellung_id, markenart_id, stueckzahl, "
            "portowert_cent, datum, markenkauf_id) VALUES (?, ?, ?, ?, ?, ?)",
            (bestellung_id, int(markenart_id), stueckzahl, m["nennwert_cent"],
             datum[:10], markenkauf_id),
        )
        conn.commit()
        return c.lastrowid


def buy_and_consume(datum: str, markenart_id: int, stueckzahl: int, betrag_cent: int,
                    bestellung_id: Optional[int] = None, beleg_id: Optional[int] = None,
                    db_file: Optional[str] = None) -> Tuple[int, int]:
    """Sofortkauf: regulärer Markenkauf **und** direkt danach der Verbrauch.

    Das Porto ist damit genau **einmal** gebucht (durch den Kauf), nicht
    zweimal. Kauf und Verbrauch sind verknüpft: wird der Kauf storniert,
    verschwindet auch der Verbrauch, sodass der Bestand stimmig bleibt.
    """
    booking_id = buy_stamps(datum, markenart_id, stueckzahl, betrag_cent,
                            beleg_id=beleg_id, bestellung_id=bestellung_id,
                            db_file=db_file)
    with _connect(db_file) as conn:
        kauf_id = conn.execute(
            "SELECT k.id FROM markenkauf k JOIN journal j ON j.lfd_nr = k.journal_lfd_nr "
            "WHERE j.id = ?", (booking_id,)).fetchone()["id"]
    verbrauch_id = consume_stamps(markenart_id, stueckzahl, bestellung_id, datum,
                                  markenkauf_id=kauf_id, db_file=db_file)
    return booking_id, verbrauch_id


def remove_consumption(verbrauch_id: int, db_file: Optional[str] = None) -> bool:
    """Einen Verbrauch zurücknehmen (Marke war doch nicht verbraucht).

    Betrifft nur den Bestand, nie das Journal — ein Verbrauch ist keine Buchung.
    """
    with _connect(db_file) as conn:
        cur = conn.execute("DELETE FROM markenverbrauch WHERE id = ?", (verbrauch_id,))
        conn.commit()
        return cur.rowcount > 0


def remove_consumptions_for_order(order_id: int, db_file: Optional[str] = None) -> int:
    """Alle Verbräuche einer Bestellung zurücknehmen (Marken zurück in den Vorrat)."""
    with _connect(db_file) as conn:
        cur = conn.execute("DELETE FROM markenverbrauch WHERE bestellung_id = ?",
                           (order_id,))
        conn.commit()
        return cur.rowcount


def _entferne_verbrauch_zum_kauf(buchung_id: int, db_file: Optional[str] = None) -> int:
    """Beim Storno eines Markenkaufs die damit erzeugten Verbräuche entfernen.

    Sonst bliebe der Verbrauch ohne zugehörigen Kauf stehen und der Bestand
    rutschte ins Minus.
    """
    with _connect(db_file) as conn:
        zeile = conn.execute(
            "SELECT k.id FROM markenkauf k JOIN journal j ON j.lfd_nr = k.journal_lfd_nr "
            "WHERE j.id = ?", (buchung_id,)).fetchone()
        if not zeile:
            return 0
        cur = conn.execute("DELETE FROM markenverbrauch WHERE markenkauf_id = ?",
                           (zeile["id"],))
        conn.commit()
        return cur.rowcount


def list_stamp_purchases(db_file: Optional[str] = None, limit: int = 200) -> List[dict]:
    with _connect(db_file) as conn:
        rows = conn.execute(
            """
            SELECT k.id, k.datum, k.stueckzahl, k.betrag_cent, k.journal_lfd_nr,
                   k.beleg_id, m.bezeichnung, m.nennwert_cent,
                   (j.storniert_durch IS NOT NULL) AS storniert
            FROM markenkauf k
            JOIN markenart m ON m.id = k.markenart_id
            LEFT JOIN journal j ON j.lfd_nr = k.journal_lfd_nr
            ORDER BY k.datum DESC, k.id DESC LIMIT ?
            """, (limit,)
        ).fetchall()
    return [dict(r) for r in rows]


def consumption_for_order(order_id: int, db_file: Optional[str] = None) -> List[dict]:
    with _connect(db_file) as conn:
        rows = conn.execute(
            "SELECT v.id, v.stueckzahl, v.portowert_cent, v.datum, m.bezeichnung "
            "FROM markenverbrauch v JOIN markenart m ON m.id = v.markenart_id "
            "WHERE v.bestellung_id = ? ORDER BY v.id", (order_id,)
        ).fetchall()
    return [dict(r) for r in rows]


# ---------------------------------------------------------------------------
# Auszahlungen (Zuflussprinzip, manuell)
# ---------------------------------------------------------------------------
def create_auszahlung(datum: str, betrag_cent: int, notiz: str = "",
                      db_file: Optional[str] = None) -> int:
    if not datum:
        raise ValueError("Datum ist erforderlich.")
    with _connect(db_file) as conn:
        c = conn.cursor()
        c.execute("INSERT INTO auszahlung (datum, betrag_cent, notiz) VALUES (?, ?, ?)",
                  (datum[:10], int(betrag_cent), notiz or ""))
        conn.commit()
        return c.lastrowid


def assign_orders_to_auszahlung(auszahlung_id: int, order_ids: Sequence[int],
                                db_file: Optional[str] = None) -> int:
    """Bestellungen einer Auszahlung zuordnen (Zufluss setzen).

    Setzt ``zahlungseingang_am`` und ``auszahlung_id`` auf allen zugehörigen
    Buchungen, die noch keine Zuordnung haben (einmaliges Nachtragen, vom
    Trigger erlaubt). Eine Bestellung kann nur **einer** Auszahlung zugeordnet
    werden — bereits zugeordnete werden übersprungen.
    """
    if not order_ids:
        return 0
    with _connect(db_file) as conn:
        row = conn.execute("SELECT datum FROM auszahlung WHERE id = ?",
                           (auszahlung_id,)).fetchone()
        if not row:
            raise ValueError("Auszahlung nicht gefunden")
        datum = row["datum"]
        placeholders = ",".join("?" for _ in order_ids)
        cur = conn.execute(
            f"UPDATE journal SET zahlungseingang_am = ?, auszahlung_id = ? "
            f"WHERE bestellung_id IN ({placeholders}) "
            f"AND zahlungseingang_am IS NULL AND auszahlung_id IS NULL "
            f"AND art <> 'storno' AND storniert_durch IS NULL",
            (datum, int(auszahlung_id), *order_ids),
        )
        conn.commit()
        return cur.rowcount


def order_auszahlung_id(order_id: int, db_file: Optional[str] = None) -> Optional[int]:
    with _connect(db_file) as conn:
        row = conn.execute(
            "SELECT auszahlung_id FROM journal WHERE bestellung_id = ? "
            "AND auszahlung_id IS NOT NULL LIMIT 1", (order_id,)
        ).fetchone()
    return row["auszahlung_id"] if row else None


def list_auszahlungen(db_file: Optional[str] = None) -> List[dict]:
    """Auszahlungen mit Abgleichshilfe: erfasster Betrag gegen die Summe der
    zugeordneten Bestellungen (Einnahmen − Gebühren). Die Differenz wird nur
    angezeigt, nie automatisch korrigiert."""
    with _connect(db_file) as conn:
        rows = [dict(r) for r in conn.execute(
            "SELECT * FROM auszahlung ORDER BY datum DESC, id DESC").fetchall()]
        for a in rows:
            z = conn.execute(
                "SELECT COALESCE(SUM(CASE WHEN art = 'einnahme' THEN betrag_cent "
                "ELSE -betrag_cent END), 0) AS summe, "
                "COUNT(DISTINCT bestellung_id) AS bestellungen "
                "FROM journal WHERE auszahlung_id = ? AND art <> 'storno' "
                "AND storniert_durch IS NULL", (a["id"],)
            ).fetchone()
            a["zugeordnet_cent"] = z["summe"]
            a["bestellungen"] = z["bestellungen"]
            a["differenz_cent"] = z["summe"] - a["betrag_cent"]
    return rows


def open_payment_orders(db_file: Optional[str] = None) -> List[dict]:
    """Gebuchte Bestellungen ohne Auszahlungszuordnung."""
    with _connect(db_file) as conn:
        rows = conn.execute(
            """
            SELECT o.id, o.order_number, o.buyer_name,
                   COALESCE(o.date_completed, o.email_date, o.date_received) AS datum,
                   SUM(CASE WHEN j.art = 'einnahme' THEN j.betrag_cent
                            ELSE -j.betrag_cent END) AS netto_cent
            FROM journal j JOIN orders o ON o.id = j.bestellung_id
            WHERE j.auszahlung_id IS NULL AND j.zahlungseingang_am IS NULL
              AND j.art <> 'storno' AND j.storniert_durch IS NULL
              AND substr(COALESCE(o.date_completed, o.email_date, o.date_received), 1, 10) >= ?
            GROUP BY o.id
            ORDER BY datum ASC
            """, (GESCHAEFTSBEGINN,)
        ).fetchall()
    return [dict(r) for r in rows]


# ---------------------------------------------------------------------------
# Belege
# ---------------------------------------------------------------------------
def save_receipt(filename: str, data: bytes, mime: str = "",
                 db_file: Optional[str] = None) -> int:
    """Belegdatei unverändert speichern und mit SHA-256 registrieren."""
    ext = Path(filename or "").suffix.lower()
    if ext not in ALLOWED_RECEIPT_EXT:
        raise ValueError("Nur PDF, JPG oder PNG erlaubt")
    BELEGE_DIR.mkdir(parents=True, exist_ok=True)

    digest = hashlib.sha256(data).hexdigest()
    safe = re.sub(r"[^A-Za-z0-9._-]", "_", Path(filename).name)
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    target = BELEGE_DIR / f"{stamp}_{safe}"
    counter = 1
    while target.exists():          # niemals einen vorhandenen Beleg überschreiben
        target = BELEGE_DIR / f"{stamp}_{counter}_{safe}"
        counter += 1
    target.write_bytes(data)        # unverändert, keine Re-Komprimierung

    with _connect(db_file) as conn:
        c = conn.cursor()
        c.execute(
            "INSERT INTO belege (original_name, gespeichert_als, mime, groesse, sha256, "
            "hochgeladen_am) VALUES (?, ?, ?, ?, ?, ?)",
            (Path(filename).name, target.name, mime, len(data), digest,
             datetime.now().isoformat()),
        )
        conn.commit()
        return c.lastrowid


def get_receipt(beleg_id: int, db_file: Optional[str] = None) -> Optional[dict]:
    with _connect(db_file) as conn:
        row = conn.execute("SELECT * FROM belege WHERE id = ?", (beleg_id,)).fetchone()
    if not row:
        return None
    d = dict(row)
    d["pfad"] = BELEGE_DIR / d["gespeichert_als"]
    return d


# ---------------------------------------------------------------------------
# Lesen / Auswertung
# ---------------------------------------------------------------------------
def list_bookings(db_file: Optional[str] = None, limit: int = 500,
                  von: str = "", bis: str = "", art: str = "",
                  kategorie: str = "") -> List[dict]:
    """Journal chronologisch (neueste zuerst), optional gefiltert."""
    q = "SELECT * FROM journal WHERE 1=1"
    params: List = []
    if von:
        q += " AND buchungsdatum >= ?"
        params.append(von)
    if bis:
        q += " AND buchungsdatum <= ?"
        params.append(bis)
    if art:
        q += " AND art = ?"
        params.append(art)
    if kategorie:
        q += " AND kategorie = ?"
        params.append(kategorie)
    q += " ORDER BY lfd_nr DESC LIMIT ?"
    params.append(limit)
    with _connect(db_file) as conn:
        rows = conn.execute(q, params).fetchall()
        # Verweis auf die Stornobuchung mitgeben (lfd_nr statt interner id).
        storno_nr = {r["id"]: r["lfd_nr"] for r in conn.execute(
            "SELECT id, lfd_nr FROM journal").fetchall()}
    out = []
    for r in rows:
        d = dict(r)
        d["storniert_durch_nr"] = storno_nr.get(d.get("storniert_durch"))
        out.append(d)
    return out


def journal_by_order(db_file: Optional[str] = None) -> dict:
    """Buchungen je Bestellung gruppiert, plus ``sonstige`` (ohne Bestellung).

    Kontrolle je Bestellung: ``Warenverkauf + Versand − Gebühren`` sollte der
    Cardmarket-Auszahlung (``amount_auszahlung``, Net sale price) entsprechen.
    """
    with _connect(db_file) as conn:
        rows = [dict(r) for r in conn.execute(
            "SELECT * FROM journal ORDER BY lfd_nr").fetchall()]
        omap = {r["id"]: dict(r) for r in conn.execute(
            "SELECT id, order_number, buyer_name, amount_auszahlung, buchung_pruefen, "
            "COALESCE(date_completed, email_date, date_received) AS datum "
            "FROM orders").fetchall()}
        verbrauch: Dict[int, int] = {}
        for v in conn.execute(
            "SELECT bestellung_id, SUM(portowert_cent * stueckzahl) AS porto "
            "FROM markenverbrauch WHERE bestellung_id IS NOT NULL "
            "GROUP BY bestellung_id"
        ).fetchall():
            verbrauch[v["bestellung_id"]] = v["porto"]

    groups: Dict[int, dict] = {}
    sonstige: List[dict] = []
    for b in rows:
        oid = b["bestellung_id"]
        if oid is None:
            sonstige.append(b)
            continue
        g = groups.get(oid)
        if g is None:
            om = omap.get(oid, {})
            g = groups[oid] = {
                "order_id": oid,
                "order_number": om.get("order_number"),
                "buyer_name": om.get("buyer_name"),
                "datum": (om.get("datum") or "")[:10],
                "pruefen": om.get("buchung_pruefen"),
                "auszahlung_cent": to_cent(om.get("amount_auszahlung")),
                "porto_cent": verbrauch.get(oid, 0),
                "bookings": [],
                "warenverkauf_cent": 0, "versand_cent": 0, "gebuehren_cent": 0,
                "portokauf_cent": 0,
            }
        g["bookings"].append(b)
        if b["art"] != "storno" and b["storniert_durch"] is None:
            k = b["kategorie"]
            if k == KAT_WARENVERKAUF:
                g["warenverkauf_cent"] += b["betrag_cent"]
            elif k == KAT_VERSANDEINNAHME:
                g["versand_cent"] += b["betrag_cent"]
            elif k == KAT_GEBUEHREN:
                g["gebuehren_cent"] += b["betrag_cent"]
            elif k == KAT_PORTO:
                # Sofortkauf, der dieser Bestellung zugeordnet wurde.
                g["portokauf_cent"] += b["betrag_cent"]

    orders: List[dict] = []
    for g in groups.values():
        # Die Kontrolle gegen die Cardmarket-Auszahlung betrifft nur die drei
        # Übernahme-Buchungen; ein Portokauf gehört nicht in diese Rechnung.
        netto = g["warenverkauf_cent"] + g["versand_cent"] - g["gebuehren_cent"]
        g["netto_cent"] = netto
        g["einnahmen_cent"] = g["warenverkauf_cent"] + g["versand_cent"]
        g["ausgaben_cent"] = g["gebuehren_cent"] + g["portokauf_cent"]
        # Ergebnis nach tatsächlichem Portoaufwand (verbrauchte Marken).
        g["ergebnis_cent"] = netto - g["porto_cent"]
        aus = g["auszahlung_cent"]
        g["has_auszahlung"] = aus > 0
        g["reconcile_diff_cent"] = netto - aus
        g["reconcile_ok"] = (not g["has_auszahlung"]) or abs(netto - aus) <= 1
        orders.append(g)
    orders.sort(key=lambda x: (x["datum"] or "", x["order_id"]), reverse=True)
    return {"orders": orders, "sonstige": sonstige}


def _effective_date(row) -> Optional[str]:
    """Stichtag für die EÜR (Zufluss-/Abflussprinzip).

    Einnahmen zählen am Tag des Geldeingangs (``zahlungseingang_am`` aus der
    zugeordneten Auszahlung), Ausgaben am eingetragenen Buchungsdatum.
    """
    if row["art"] == "einnahme":
        return row["zahlungseingang_am"]
    return row["buchungsdatum"]


def summary(start: str, end: str, db_file: Optional[str] = None) -> dict:
    """Summen je Kategorie im Zeitraum, plus noch nicht zugeflossene Einnahmen.

    Stornierte Buchungen und die Stornozeilen selbst werden herausgerechnet.
    """
    with _connect(db_file) as conn:
        rows = conn.execute("SELECT * FROM journal").fetchall()

    einnahmen: Dict[str, int] = {}
    ausgaben: Dict[str, int] = {}
    offen_einnahme = 0
    offen_count = 0

    for r in rows:
        if r["art"] == "storno" or r["storniert_durch"] is not None:
            continue                      # Storno-Paare heben sich auf
        eff = _effective_date(r)
        if not eff:                       # Einnahme noch nicht zugeflossen
            offen_count += 1
            offen_einnahme += r["betrag_cent"]
            continue
        if not (start <= eff[:10] <= end):
            continue
        bucket = einnahmen if r["art"] == "einnahme" else ausgaben
        bucket[r["kategorie"]] = bucket.get(r["kategorie"], 0) + r["betrag_cent"]

    sum_ein = sum(einnahmen.values())
    sum_aus = sum(ausgaben.values())
    return {
        "einnahmen": dict(sorted(einnahmen.items())),
        "ausgaben": dict(sorted(ausgaben.items())),
        "summe_einnahmen": sum_ein,
        "summe_ausgaben": sum_aus,
        "ueberschuss": sum_ein - sum_aus,
        "offen_einnahme": offen_einnahme,
        "offen_count": offen_count,
    }


def versand_marge(start: str, end: str, db_file: Optional[str] = None) -> dict:
    """Betriebswirtschaftliche Auswertung (**keine** EÜR-Größe).

    Vereinnahmte Versandkosten (nach Buchungsdatum = Versandtag) gegen das
    tatsächlich verbrauchte Porto aus ``markenverbrauch``. Das verbrauchte
    Porto ist bereits beim Markenkauf als Ausgabe gebucht und wird hier nur
    nachrichtlich gegenübergestellt.
    """
    with _connect(db_file) as conn:
        ein = conn.execute(
            "SELECT COALESCE(SUM(betrag_cent), 0) AS s, COUNT(*) AS n FROM journal "
            "WHERE kategorie = ? AND art = 'einnahme' AND storniert_durch IS NULL "
            "AND buchungsdatum BETWEEN ? AND ?",
            (KAT_VERSANDEINNAHME, start, end),
        ).fetchone()
        verb = conn.execute(
            "SELECT COALESCE(SUM(portowert_cent * stueckzahl), 0) AS s, "
            "COUNT(DISTINCT COALESCE(bestellung_id, -id)) AS n "
            "FROM markenverbrauch WHERE datum BETWEEN ? AND ?",
            (start, end),
        ).fetchone()

    vereinnahmt = ein["s"]
    porto = verb["s"]
    sendungen = max(ein["n"], verb["n"])
    return {
        "vereinnahmt_cent": vereinnahmt,
        "porto_cent": porto,
        "marge_cent": vereinnahmt - porto,
        "sendungen": sendungen,
        "marge_je_sendung_cent": (vereinnahmt - porto) // sendungen if sendungen else 0,
    }


def summary_csv(result: dict, start: str, end: str) -> bytes:
    """Auswertung als deutsches CSV (Semikolon, UTF-8 mit BOM, Komma-Dezimal)."""
    buf = io.StringIO()
    w = csv.writer(buf, delimiter=CSV_DELIMITER, lineterminator="\r\n")
    w.writerow(["Zeitraum", f"{de_date(start)} - {de_date(end)}"])
    w.writerow([])
    w.writerow(["Art", "Kategorie", "Betrag"])
    for kat, cent in result["einnahmen"].items():
        w.writerow(["Einnahme", kat, cent_to_de(cent)])
    for kat, cent in result["ausgaben"].items():
        w.writerow(["Ausgabe", kat, cent_to_de(cent)])
    w.writerow([])
    w.writerow(["", "Summe Einnahmen", cent_to_de(result["summe_einnahmen"])])
    w.writerow(["", "Summe Ausgaben", cent_to_de(result["summe_ausgaben"])])
    w.writerow(["", "Überschuss", cent_to_de(result["ueberschuss"])])
    if result["offen_count"]:
        w.writerow([])
        w.writerow(["", "Noch nicht zugeflossen (Einnahmen)",
                    cent_to_de(result["offen_einnahme"])])
    return buf.getvalue().encode(CSV_ENCODING)
