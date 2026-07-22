"""Buchungsjournal, Ausgaben und Belege (WP3b).

Das Journal ist **append-only**: eine erfasste Buchung wird nie geaendert oder
geloescht — Korrekturen erfolgen ausschliesslich per Stornobuchung, die auf die
urspruengliche Buchung verweist. Erzwungen wird das per Trigger in
``setup_db.py``; dieses Modul kapselt nur die erlaubten Operationen.

Datenherkunft: Einnahmen entstehen **ausschliesslich** aus den in WP2a
gespeicherten Bestelldaten der Cardmarket-Mail. Preisdaten aus dem
Dragonshield-Import (``Price Bought``) werden hier bewusst **nicht** verwendet —
weder direkt noch indirekt.

Betraege werden durchgaengig als Integer in Cent gefuehrt (keine Floats).
Keine Steuerberechnung, keine Umsatzsteuer-Logik.
"""

from __future__ import annotations

import csv
import hashlib
import io
import re
import sqlite3
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional, Sequence

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
    "Verpackungsmaterial",
    "Porto/Versand",
    "Cardmarket-Gebühren",
    "Bürobedarf",
    "Sonstige Ausgaben",
]

BELEGE_DIR = Path(__file__).resolve().parent / "data" / "belege"
ALLOWED_RECEIPT_EXT = {".pdf", ".jpg", ".jpeg", ".png"}

CSV_DELIMITER = ";"
CSV_ENCODING = "utf-8-sig"


# ---------------------------------------------------------------------------
# Betrags-Helfer (immer Cent)
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
    """Cent -> deutsches Format ohne Waehrungszeichen: ``1234`` -> ``12,34``."""
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
    """Neue Buchung anhaengen und deren ``id`` zurueckgeben.

    ``lfd_nr`` wird systemvergeben und ist fortlaufend und lueckenlos (es gibt
    keine Loeschungen).
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

    Die urspruengliche Buchung bleibt unveraendert; nur ihr Rueckverweis
    ``storniert_durch`` wird einmalig nachgetragen.
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
        # Einmaliges Nachtragen des Rueckverweises (vom Trigger erlaubt).
        conn.execute("UPDATE journal SET storniert_durch = ? WHERE id = ?",
                     (storno_id, buchung_id))
        conn.commit()
    return storno_id


# ---------------------------------------------------------------------------
# Einnahmen aus Bestellungen (nur Maildaten aus WP2a)
# ---------------------------------------------------------------------------
def order_already_booked(order_id: int, db_file: Optional[str] = None) -> bool:
    with _connect(db_file) as conn:
        row = conn.execute(
            "SELECT 1 FROM journal WHERE bestellung_id = ? AND art <> 'storno' LIMIT 1",
            (order_id,),
        ).fetchone()
    return row is not None


def book_order(order_id: int, db_file: Optional[str] = None) -> List[int]:
    """Eine Bestellung als Einnahme uebernehmen.

    Erzeugt drei getrennte Buchungen aus den gespeicherten Maildaten, damit
    Brutto und Gebuehren sichtbar bleiben:
      * ``Warenverkauf`` (Einnahme)          = Gesamtbetrag - Versandkosten
      * ``Vereinnahmte Versandkosten`` (Einnahme)
      * ``Cardmarket-Gebühren`` (Ausgabe)

    Eine Bestellung kann nur einmal uebernommen werden (zusaetzlich per
    UNIQUE-Index abgesichert).
    """
    with _connect(db_file) as conn:
        o = conn.execute(
            "SELECT id, order_number, buyer_name, status, address_confirmed, "
            "email_date, date_received, amount_gesamt, amount_gesamtwert, "
            "amount_versand, amount_gebuehren FROM orders WHERE id = ?",
            (order_id,),
        ).fetchone()
    if not o:
        raise ValueError("Bestellung nicht gefunden")
    if order_already_booked(order_id, db_file):
        raise ValueError("Diese Bestellung wurde bereits übernommen")

    # Gesamtbetrag der Mail; amount_gesamt ist der belastbare Wert.
    gesamt_cent = to_cent(o["amount_gesamt"] if o["amount_gesamt"] is not None
                          else o["amount_gesamtwert"])
    versand_cent = to_cent(o["amount_versand"])
    gebuehren_cent = to_cent(o["amount_gebuehren"])
    waren_cent = gesamt_cent - versand_cent

    datum = str(o["email_date"] or o["date_received"] or "")[:10]
    ref = f"Bestellung {o['order_number'] or order_id} ({o['buyer_name'] or ''})".strip()

    ids = []
    ids.append(add_booking(datum, "einnahme", "Warenverkauf", waren_cent,
                           ref, bestellung_id=order_id, db_file=db_file))
    if versand_cent:
        ids.append(add_booking(datum, "einnahme", "Vereinnahmte Versandkosten", versand_cent,
                               ref, bestellung_id=order_id, db_file=db_file))
    if gebuehren_cent:
        ids.append(add_booking(datum, "ausgabe", "Cardmarket-Gebühren", gebuehren_cent,
                               ref, bestellung_id=order_id, db_file=db_file))
    return ids


def assign_payment_date(order_ids: Sequence[int], datum: str,
                        db_file: Optional[str] = None) -> int:
    """Mehreren Bestellungen gemeinsam ein Auszahlungsdatum zuweisen.

    Setzt ``zahlungseingang_am`` auf allen zugehoerigen Buchungen, die noch
    keinen Wert haben (einmaliges Nachtragen, vom Trigger erlaubt).
    """
    if not order_ids or not datum:
        return 0
    with _connect(db_file) as conn:
        placeholders = ",".join("?" for _ in order_ids)
        cur = conn.execute(
            f"UPDATE journal SET zahlungseingang_am = ? "
            f"WHERE bestellung_id IN ({placeholders}) AND zahlungseingang_am IS NULL",
            (datum, *order_ids),
        )
        conn.commit()
        return cur.rowcount


# ---------------------------------------------------------------------------
# Belege
# ---------------------------------------------------------------------------
def save_receipt(filename: str, data: bytes, mime: str = "",
                 db_file: Optional[str] = None) -> int:
    """Belegdatei unveraendert speichern und mit SHA-256 registrieren."""
    ext = Path(filename or "").suffix.lower()
    if ext not in ALLOWED_RECEIPT_EXT:
        raise ValueError("Nur PDF, JPG oder PNG erlaubt")
    BELEGE_DIR.mkdir(parents=True, exist_ok=True)

    digest = hashlib.sha256(data).hexdigest()
    safe = re.sub(r"[^A-Za-z0-9._-]", "_", Path(filename).name)
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    target = BELEGE_DIR / f"{stamp}_{safe}"
    counter = 1
    while target.exists():          # niemals einen vorhandenen Beleg ueberschreiben
        target = BELEGE_DIR / f"{stamp}_{counter}_{safe}"
        counter += 1
    target.write_bytes(data)        # unveraendert, keine Re-Komprimierung

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
def list_bookings(db_file: Optional[str] = None, limit: int = 500) -> List[dict]:
    with _connect(db_file) as conn:
        rows = conn.execute(
            "SELECT * FROM journal ORDER BY lfd_nr DESC LIMIT ?", (limit,)
        ).fetchall()
    return [dict(r) for r in rows]


def _effective_date(row) -> Optional[str]:
    """Stichtag fuer die EUER-Auswertung (Zufluss-/Abflussprinzip).

    Buchungen aus Bestellungen zaehlen am Auszahlungstag (``zahlungseingang_am``);
    manuell erfasste Buchungen am eingetragenen Buchungsdatum.
    """
    if row["bestellung_id"] is not None:
        return row["zahlungseingang_am"]
    return row["buchungsdatum"]


def summary(start: str, end: str, db_file: Optional[str] = None) -> dict:
    """Summen je Kategorie im Zeitraum, plus noch nicht zugeflossene Betraege.

    Stornierte Buchungen und die Stornozeilen selbst werden herausgerechnet.
    """
    with _connect(db_file) as conn:
        rows = conn.execute("SELECT * FROM journal").fetchall()

    einnahmen: Dict[str, int] = {}
    ausgaben: Dict[str, int] = {}
    offen_einnahme = offen_ausgabe = 0
    offen_count = 0

    for r in rows:
        if r["art"] == "storno" or r["storniert_durch"] is not None:
            continue                      # Storno-Paare heben sich auf
        eff = _effective_date(r)
        if not eff:                       # Bestellung noch nicht ausgezahlt
            offen_count += 1
            if r["art"] == "einnahme":
                offen_einnahme += r["betrag_cent"]
            else:
                offen_ausgabe += r["betrag_cent"]
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
        "offen_ausgabe": offen_ausgabe,
        "offen_count": offen_count,
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


def bookable_orders(db_file: Optional[str] = None) -> List[dict]:
    """Versendete Bestellungen mit bestaetigter Adresse, die noch nicht gebucht sind."""
    with _connect(db_file) as conn:
        rows = conn.execute(
            """
            SELECT o.id, o.order_number, o.buyer_name,
                   COALESCE(o.email_date, o.date_received) AS datum,
                   o.amount_gesamt, o.amount_versand, o.amount_gebuehren
            FROM orders o
            WHERE o.status = 'sold' AND o.address_confirmed = 1
              AND NOT EXISTS (SELECT 1 FROM journal j
                              WHERE j.bestellung_id = o.id AND j.art <> 'storno')
            ORDER BY datum DESC
            """
        ).fetchall()
    return [dict(r) for r in rows]


def open_payment_orders(db_file: Optional[str] = None) -> List[dict]:
    """Gebuchte Bestellungen, denen noch kein Auszahlungsdatum zugewiesen ist."""
    with _connect(db_file) as conn:
        rows = conn.execute(
            """
            SELECT o.id, o.order_number, o.buyer_name,
                   COALESCE(o.email_date, o.date_received) AS datum,
                   SUM(CASE WHEN j.art = 'einnahme' THEN j.betrag_cent ELSE 0 END) AS einnahme_cent
            FROM journal j JOIN orders o ON o.id = j.bestellung_id
            WHERE j.zahlungseingang_am IS NULL AND j.art <> 'storno'
              AND j.storniert_durch IS NULL
            GROUP BY o.id
            ORDER BY datum ASC
            """
        ).fetchall()
    return [dict(r) for r in rows]
