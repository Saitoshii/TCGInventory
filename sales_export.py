"""Read-only sales export for the accounting hand-over (WP3a).

This module does NOT implement bookkeeping: no posting logic, no receipts, no
VAT calculation, no profit determination. It only *reads* the order data that is
already stored and renders it as Excel-friendly German CSV, so it can be handed
to the accounting software / tax advisor.

Nothing here writes to the database.
"""

from __future__ import annotations

import csv
import io
import sqlite3
from datetime import date, datetime
from typing import Dict, List, Optional, Sequence, Tuple

# Column headers (order is part of the agreed export format).
ORDER_COLUMNS = [
    "Datum", "Bestellnummer", "Käufer", "Land", "Anzahl Artikel",
    "Warenwert", "Versandkosten", "Gesamtbetrag", "Gebühren", "Auszahlungsbetrag",
]
POSITION_COLUMNS = [
    "Datum", "Bestellnummer", "Menge", "Kartenname", "Set", "Zustand", "Foil", "Einzelpreis",
]

CSV_DELIMITER = ";"          # German Excel default
CSV_ENCODING = "utf-8-sig"   # UTF-8 *with BOM* so Excel shows ä/ö/ü/ß and € right

PRESETS = ("laufender_monat", "letzter_monat", "laufendes_jahr", "letztes_jahr")


# ---------------------------------------------------------------------------
# Period handling
# ---------------------------------------------------------------------------
def preset_range(preset: str, today: Optional[date] = None) -> Tuple[date, date]:
    """Return (start, end) for a quick-select preset."""
    t = today or date.today()
    if preset == "laufender_monat":
        start = t.replace(day=1)
        end = (start.replace(year=start.year + 1, month=1) if start.month == 12
               else start.replace(month=start.month + 1)) - _one_day()
    elif preset == "letzter_monat":
        first_this = t.replace(day=1)
        end = first_this - _one_day()
        start = end.replace(day=1)
    elif preset == "letztes_jahr":
        start = date(t.year - 1, 1, 1)
        end = date(t.year - 1, 12, 31)
    else:  # "laufendes_jahr" (default)
        start = date(t.year, 1, 1)
        end = date(t.year, 12, 31)
    return start, end


def _one_day():
    from datetime import timedelta
    return timedelta(days=1)


def parse_range(von: str = "", bis: str = "", preset: str = "",
                today: Optional[date] = None) -> Tuple[date, date]:
    """Resolve the requested period.

    An explicit von/bis wins; otherwise the preset is used; the documented
    default is the current year.
    """
    if preset in PRESETS:
        return preset_range(preset, today)
    start = _parse_iso(von)
    end = _parse_iso(bis)
    if start and end:
        return (start, end) if start <= end else (end, start)
    return preset_range("laufendes_jahr", today)


def _parse_iso(value: str) -> Optional[date]:
    try:
        return datetime.strptime((value or "").strip(), "%Y-%m-%d").date()
    except (ValueError, TypeError):
        return None


# ---------------------------------------------------------------------------
# Formatting (German / Excel)
# ---------------------------------------------------------------------------
def de_amount(value) -> str:
    """``3,90`` — comma as decimal separator, no thousand separators.

    ``None`` becomes an empty cell (amount not recorded), never a fake 0,00.
    """
    if value is None or value == "":
        return ""
    try:
        return f"{float(value):.2f}".replace(".", ",")
    except (TypeError, ValueError):
        return ""


def de_date(value) -> str:
    """ISO timestamp/date -> ``TT.MM.JJJJ`` (empty if unparsable)."""
    s = str(value or "").strip()
    if not s:
        return ""
    head = s.split("T")[0].split(" ")[0]
    try:
        return datetime.strptime(head, "%Y-%m-%d").strftime("%d.%m.%Y")
    except ValueError:
        return s


def country_from_address(address: str) -> str:
    """Country from the confirmed recipient address (its last line).

    A line containing digits (e.g. a postal code line) is not a country.
    """
    lines = [ln.strip() for ln in (address or "").splitlines() if ln.strip()]
    if not lines:
        return ""
    last = lines[-1]
    return last if not any(ch.isdigit() for ch in last) else ""


def _month_key(value) -> str:
    s = str(value or "")
    return s[:7]  # YYYY-MM


def _num(value) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return 0.0


# ---------------------------------------------------------------------------
# Queries (strictly read-only)
# ---------------------------------------------------------------------------
_ORDER_DATE = "COALESCE(o.email_date, o.date_received)"


def fetch_orders(db_file: str, start: date, end: date) -> List[Dict]:
    """Return one record per order in the period, sorted by date ascending."""
    with sqlite3.connect(db_file) as conn:
        conn.row_factory = sqlite3.Row
        rows = conn.execute(
            f"""
            SELECT {_ORDER_DATE} AS order_date, o.order_number, o.buyer_name, o.address,
                   o.amount_gesamtwert, o.amount_versand, o.amount_gesamt,
                   o.amount_gebuehren, o.amount_auszahlung,
                   (SELECT COALESCE(SUM(oi.quantity), 0) FROM order_items oi
                     WHERE oi.order_id = o.id) AS item_count
            FROM orders o
            WHERE date({_ORDER_DATE}) BETWEEN ? AND ?
            ORDER BY {_ORDER_DATE} ASC, o.id ASC
            """,
            (start.isoformat(), end.isoformat()),
        ).fetchall()
    return [
        {
            "date": r["order_date"],
            "order_number": r["order_number"] or "",
            "buyer": r["buyer_name"] or "",
            "country": country_from_address(r["address"]),
            "item_count": r["item_count"] or 0,
            "warenwert": r["amount_gesamtwert"],
            "versand": r["amount_versand"],
            "gesamt": r["amount_gesamt"],
            "gebuehren": r["amount_gebuehren"],
            "auszahlung": r["amount_auszahlung"],
        }
        for r in rows
    ]


def fetch_positions(db_file: str, start: date, end: date) -> List[Dict]:
    """Return one record per sold card in the period (own analysis, not tax)."""
    with sqlite3.connect(db_file) as conn:
        conn.row_factory = sqlite3.Row
        rows = conn.execute(
            f"""
            SELECT {_ORDER_DATE} AS order_date, o.order_number,
                   oi.quantity, oi.card_name,
                   COALESCE(NULLIF(oi.set_name, ''), oi.set_code, '') AS set_label,
                   oi.condition, oi.foil, oi.unit_price
            FROM order_items oi
            JOIN orders o ON oi.order_id = o.id
            WHERE date({_ORDER_DATE}) BETWEEN ? AND ?
            ORDER BY {_ORDER_DATE} ASC, o.id ASC, oi.card_name ASC
            """,
            (start.isoformat(), end.isoformat()),
        ).fetchall()
    return [
        {
            "date": r["order_date"],
            "order_number": r["order_number"] or "",
            "quantity": r["quantity"] or 0,
            "card_name": r["card_name"] or "",
            "set": r["set_label"] or "",
            "condition": r["condition"] or "",
            "foil": bool(r["foil"]),
            "unit_price": r["unit_price"],
        }
        for r in rows
    ]


def monthly_summary(orders: Sequence[Dict]) -> List[Dict]:
    """Aggregate the orders per calendar month for the overview table."""
    buckets: Dict[str, Dict] = {}
    for o in orders:
        key = _month_key(o["date"])
        b = buckets.setdefault(key, {"month": key, "count": 0,
                                     "gesamt": 0.0, "gebuehren": 0.0, "auszahlung": 0.0})
        b["count"] += 1
        b["gesamt"] += _num(o["gesamt"])
        b["gebuehren"] += _num(o["gebuehren"])
        b["auszahlung"] += _num(o["auszahlung"])
    out = []
    for key in sorted(buckets):
        b = buckets[key]
        y, m = (key.split("-") + ["", ""])[:2]
        b["label"] = f"{m}.{y}" if y and m else key
        out.append(b)
    return out


# ---------------------------------------------------------------------------
# CSV builders
# ---------------------------------------------------------------------------
def _to_csv(header: Sequence[str], rows: Sequence[Sequence[str]]) -> bytes:
    buf = io.StringIO()
    writer = csv.writer(buf, delimiter=CSV_DELIMITER, lineterminator="\r\n")
    writer.writerow(header)
    for row in rows:
        writer.writerow(row)
    return buf.getvalue().encode(CSV_ENCODING)


def build_orders_csv(orders: Sequence[Dict]) -> bytes:
    """Main export: one line per order, plus a totals line for cross-checking."""
    rows = []
    sum_gesamt = sum_gebuehren = sum_auszahlung = 0.0
    for o in orders:
        sum_gesamt += _num(o["gesamt"])
        sum_gebuehren += _num(o["gebuehren"])
        sum_auszahlung += _num(o["auszahlung"])
        rows.append([
            de_date(o["date"]), o["order_number"], o["buyer"], o["country"],
            o["item_count"], de_amount(o["warenwert"]), de_amount(o["versand"]),
            de_amount(o["gesamt"]), de_amount(o["gebuehren"]), de_amount(o["auszahlung"]),
        ])
    rows.append([
        "Summe", "", "", "", "", "", "",
        de_amount(sum_gesamt), de_amount(sum_gebuehren), de_amount(sum_auszahlung),
    ])
    return _to_csv(ORDER_COLUMNS, rows)


def build_positions_csv(positions: Sequence[Dict]) -> bytes:
    """Additional export: one line per sold card (set / condition / foil)."""
    rows = [
        [
            de_date(p["date"]), p["order_number"], p["quantity"], p["card_name"],
            p["set"], p["condition"], "Ja" if p["foil"] else "Nein",
            de_amount(p["unit_price"]),
        ]
        for p in positions
    ]
    return _to_csv(POSITION_COLUMNS, rows)


def export_filename(prefix: str, start: date, end: date) -> str:
    """e.g. ``verkaeufe_2026-01-01_bis_2026-12-31.csv``."""
    return f"{prefix}_{start.isoformat()}_bis_{end.isoformat()}.csv"
