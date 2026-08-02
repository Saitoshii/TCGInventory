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

# Tatsaechliche Portokosten (Ausgabe), die der Shop beim Versand zahlt — NICHT
# der vom Kunden ueber Cardmarket bezahlte Versand (der bleibt Einnahme aus der
# Mail). Cardmarket gibt dem Kunden einen festen Versandpreis vor; das echte
# Porto ist meist guenstiger, und genau dieser reale Wert muss in die Buchhaltung.
# Die Preise sind Vorschlaege (in Cent) und werden beim Uebernehmen ins
# Betragsfeld vorbefuellt, dort aber immer ueberschreibbar. Stand gelegentlich
# pruefen — Deutsche Post / DHL passen ihre Tarife an.
PORTO_OPTIONS = [
    ("Standardbrief (bis 20 g)", 95),
    ("Kompaktbrief (bis 50 g)", 110),
    ("Großbrief (bis 500 g)", 180),
    ("Maxibrief (bis 1000 g)", 275),
    ("Warenpost bis 500 g", 240),
    ("Einschreiben Einwurf", 320),
    ("DHL Päckchen S", 399),
    ("DHL Paket bis 2 kg", 555),
    ("Warenpost International (EU)", 400),
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
    """True if the order has an *active* (non-cancelled) booking.

    A booking that has been storno'd (``storniert_durch`` set) no longer counts —
    so a mistaken takeover can be corrected via Storno and then booked again.
    """
    with _connect(db_file) as conn:
        row = conn.execute(
            "SELECT 1 FROM journal WHERE bestellung_id = ? AND art <> 'storno' "
            "AND storniert_durch IS NULL LIMIT 1",
            (order_id,),
        ).fetchone()
    return row is not None


def book_order(order_id: int, porto_cent: int = 0, porto_methode: str = "",
               db_file: Optional[str] = None) -> List[int]:
    """Eine Bestellung als Einnahme uebernehmen.

    Erzeugt getrennte Buchungen aus den gespeicherten Maildaten, damit Brutto
    und Gebuehren sichtbar bleiben:
      * ``Warenverkauf`` (Einnahme)              = Gesamtbetrag - Versandkosten
      * ``Vereinnahmte Versandkosten`` (Einnahme) = vom Kunden gezahlter Versand
      * ``Cardmarket-Gebühren`` (Ausgabe)

    ``porto_cent`` ist das **tatsaechlich gezahlte** Porto (Ausgabe), das der
    Shop beim Versand aufwendet — unabhaengig vom Cardmarket-Versandpreis, den
    der Kunde zahlt. Ist es > 0, wird zusaetzlich ``Porto/Versand`` (Ausgabe)
    gebucht; ``porto_methode`` landet als Hinweis in der Beschreibung.

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
        items = conn.execute(
            "SELECT quantity, unit_price FROM order_items WHERE order_id = ?",
            (order_id,),
        ).fetchall()
    if order_already_booked(order_id, db_file):
        raise ValueError("Diese Bestellung wurde bereits übernommen")

    versand_cent = to_cent(o["amount_versand"])
    gebuehren_cent = to_cent(o["amount_gebuehren"])

    # Warenverkauf = Gesamtbetrag der Mail minus Versand. Fehlt der Gesamtbetrag
    # (z. B. bei aelteren, vor dem Versand-Parsing eingelesenen Bestellungen),
    # wird er aus den tatsaechlichen Positionspreisen der Mail gebildet — beides
    # sind Cardmarket-Maildaten, keine Dragonshield-Preise.
    if o["amount_gesamt"] is not None:
        waren_cent = to_cent(o["amount_gesamt"]) - versand_cent
    else:
        waren_cent = sum(to_cent(it["unit_price"]) * int(it["quantity"] or 1)
                         for it in items)

    if waren_cent <= 0 and versand_cent <= 0 and gebuehren_cent <= 0:
        raise ValueError(
            "Keine Beträge hinterlegt – bitte Versand und Cardmarket-Gebühren "
            "im Bestell-Panel eintragen (und ggf. die Kartenpreise prüfen), "
            "dann die Bestellung übernehmen."
        )

    datum = str(o["email_date"] or o["date_received"] or "")[:10]
    ref = f"Bestellung {o['order_number'] or order_id} ({o['buyer_name'] or ''})".strip()

    ids = []
    if waren_cent:
        ids.append(add_booking(datum, "einnahme", "Warenverkauf", waren_cent,
                               ref, bestellung_id=order_id, db_file=db_file))
    if versand_cent:
        ids.append(add_booking(datum, "einnahme", "Vereinnahmte Versandkosten", versand_cent,
                               ref, bestellung_id=order_id, db_file=db_file))
    if gebuehren_cent:
        ids.append(add_booking(datum, "ausgabe", "Cardmarket-Gebühren", gebuehren_cent,
                               ref, bestellung_id=order_id, db_file=db_file))
    # Tatsaechlich gezahltes Porto (Ausgabe) — der reale Versandaufwand des Shops.
    if porto_cent and int(porto_cent) > 0:
        porto_ref = ref
        if porto_methode and porto_methode.lower() != "manuell":
            porto_ref = f"{ref} – Porto: {porto_methode}"
        ids.append(add_booking(datum, "ausgabe", "Porto/Versand", int(porto_cent),
                               porto_ref, bestellung_id=order_id, db_file=db_file))
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
# Briefmarken-Vorrat (vorab gekaufte Marken)
# ---------------------------------------------------------------------------
def list_briefmarken(only_stock: bool = False, db_file: Optional[str] = None) -> List[dict]:
    """Briefmarken-Bestand je Wert (aufsteigend). ``only_stock`` blendet leere aus."""
    with _connect(db_file) as conn:
        q = "SELECT wert_cent, anzahl FROM briefmarken"
        if only_stock:
            q += " WHERE anzahl > 0"
        q += " ORDER BY wert_cent"
        rows = conn.execute(q).fetchall()
    return [dict(r) for r in rows]


def buy_stamps(wert_cent: int, anzahl: int, buchungsdatum: str,
               beleg_id: Optional[int] = None, db_file: Optional[str] = None) -> int:
    """Briefmarken im Voraus kaufen.

    Bucht **eine** Ausgabe ``Porto/Versand`` (Wert × Anzahl) — moeglichst mit
    Beleg — und legt die Marken in den Bestand. So werden die Portokosten genau
    einmal (beim Kauf) erfasst; beim Versand wird nur der Bestand reduziert, es
    entsteht keine Doppelbuchung.
    """
    wert_cent = int(wert_cent)
    anzahl = int(anzahl)
    if wert_cent <= 0 or anzahl <= 0:
        raise ValueError("Wert und Anzahl müssen größer als 0 sein.")
    betrag = wert_cent * anzahl
    beschr = f"Briefmarkenkauf {anzahl}× {cent_to_de(wert_cent)} €"
    booking_id = add_booking(buchungsdatum, "ausgabe", "Porto/Versand", betrag,
                             beschr, beleg_id=beleg_id, db_file=db_file)
    with _connect(db_file) as conn:
        conn.execute(
            "INSERT INTO briefmarken (wert_cent, anzahl) VALUES (?, ?) "
            "ON CONFLICT(wert_cent) DO UPDATE SET anzahl = anzahl + excluded.anzahl",
            (wert_cent, anzahl),
        )
        conn.commit()
    return booking_id


def use_stamp(wert_cent: int, db_file: Optional[str] = None) -> bool:
    """Eine Marke des Werts aus dem Vorrat abziehen (keine neue Buchung — schon
    beim Kauf bezahlt). Gibt ``True`` zurueck, wenn eine Marke da war."""
    with _connect(db_file) as conn:
        cur = conn.execute(
            "UPDATE briefmarken SET anzahl = anzahl - 1 WHERE wert_cent = ? AND anzahl > 0",
            (int(wert_cent),),
        )
        conn.commit()
        return cur.rowcount > 0


def set_order_stamp(order_id: int, wert_cent: int, db_file: Optional[str] = None) -> None:
    """Vermerken, dass eine Bestellung eine Vorrats-Briefmarke dieses Werts nutzt
    (fuer eine spaetere Rueckgabe beim Storno)."""
    with _connect(db_file) as conn:
        conn.execute("UPDATE orders SET porto_briefmarke_cent = ? WHERE id = ?",
                     (int(wert_cent), order_id))
        conn.commit()


def order_stamp(order_id: int, db_file: Optional[str] = None) -> Optional[int]:
    """Wert der von der Bestellung genutzten Vorrats-Briefmarke (oder ``None``)."""
    with _connect(db_file) as conn:
        row = conn.execute(
            "SELECT porto_briefmarke_cent FROM orders WHERE id = ?", (order_id,)
        ).fetchone()
    return row["porto_briefmarke_cent"] if row and row["porto_briefmarke_cent"] else None


def return_order_stamp(order_id: int, db_file: Optional[str] = None) -> Optional[int]:
    """Die von der Bestellung genutzte Vorrats-Briefmarke zurueck in den Vorrat
    legen (+1) und den Vermerk loeschen. Gibt den Wert zurueck oder ``None``,
    wenn keine (mehr) hinterlegt war."""
    with _connect(db_file) as conn:
        row = conn.execute(
            "SELECT porto_briefmarke_cent FROM orders WHERE id = ?", (order_id,)
        ).fetchone()
        wert = row["porto_briefmarke_cent"] if row else None
        if not wert:
            return None
        conn.execute(
            "INSERT INTO briefmarken (wert_cent, anzahl) VALUES (?, 1) "
            "ON CONFLICT(wert_cent) DO UPDATE SET anzahl = anzahl + 1",
            (int(wert),),
        )
        conn.execute("UPDATE orders SET porto_briefmarke_cent = NULL WHERE id = ?", (order_id,))
        conn.commit()
        return int(wert)


# ---------------------------------------------------------------------------
# Lesen / Auswertung
# ---------------------------------------------------------------------------
def list_bookings(db_file: Optional[str] = None, limit: int = 500) -> List[dict]:
    with _connect(db_file) as conn:
        rows = conn.execute(
            "SELECT * FROM journal ORDER BY lfd_nr DESC LIMIT ?", (limit,)
        ).fetchall()
    return [dict(r) for r in rows]


def journal_by_order(db_file: Optional[str] = None) -> dict:
    """Buchungen nach Bestellung gruppiert, plus ``sonstige`` (ohne Bestellung).

    Je Bestellung gibt es eine Zusammenfassung und eine Kontrolle: die Summe
    ``Warenverkauf + Versand - Cardmarket-Gebühren`` (nur aktive Buchungen)
    sollte der Cardmarket-Auszahlung (``amount_auszahlung`` = Net sale price)
    entsprechen. Weicht sie ab, ist die Bestellung fehlerhaft gebucht.
    """
    with _connect(db_file) as conn:
        rows = [dict(r) for r in conn.execute(
            "SELECT * FROM journal ORDER BY lfd_nr").fetchall()]
        omap = {r["id"]: dict(r) for r in conn.execute(
            "SELECT id, order_number, buyer_name, amount_auszahlung, porto_briefmarke_cent, "
            "COALESCE(email_date, date_received) AS datum FROM orders").fetchall()}

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
                "auszahlung_cent": to_cent(om.get("amount_auszahlung")),
                "stamp_cent": om.get("porto_briefmarke_cent"),
                "bookings": [],
                "warenverkauf_cent": 0, "versand_cent": 0,
                "gebuehren_cent": 0, "porto_cent": 0,
            }
        g["bookings"].append(b)
        if b["art"] != "storno" and b["storniert_durch"] is None:
            k = b["kategorie"]
            if k == "Warenverkauf":
                g["warenverkauf_cent"] += b["betrag_cent"]
            elif k == "Vereinnahmte Versandkosten":
                g["versand_cent"] += b["betrag_cent"]
            elif k == "Cardmarket-Gebühren":
                g["gebuehren_cent"] += b["betrag_cent"]
            elif k == "Porto/Versand":
                g["porto_cent"] += b["betrag_cent"]

    orders: List[dict] = []
    for g in groups.values():
        netto = g["warenverkauf_cent"] + g["versand_cent"] - g["gebuehren_cent"]
        g["netto_cent"] = netto
        g["ergebnis_cent"] = netto - g["porto_cent"]
        g["einnahmen_cent"] = g["warenverkauf_cent"] + g["versand_cent"]
        g["ausgaben_cent"] = g["gebuehren_cent"] + g["porto_cent"]
        aus = g["auszahlung_cent"]
        g["has_auszahlung"] = aus > 0
        g["reconcile_diff_cent"] = netto - aus
        g["reconcile_ok"] = (not g["has_auszahlung"]) or abs(netto - aus) <= 1
        orders.append(g)
    orders.sort(key=lambda x: (x["datum"] or "", x["order_id"]), reverse=True)
    return {"orders": orders, "sonstige": sonstige}


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
                   o.amount_gesamt, o.amount_versand, o.amount_gebuehren,
                   o.amount_auszahlung
            FROM orders o
            WHERE o.status = 'sold' AND o.address_confirmed = 1
              AND NOT EXISTS (SELECT 1 FROM journal j
                              WHERE j.bestellung_id = o.id AND j.art <> 'storno'
                                AND j.storniert_durch IS NULL)
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
