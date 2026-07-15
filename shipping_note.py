"""A4 shipping-note (Beileger) generator — "Zur Festung" design, DE/EN (WP2c).

Produces the branded order insert for a DIN-5008 Form B window envelope with the
project's PDF library (``fpdf2``). Layout, colours and geometry are named
constants below; sender/logo/badge come from :func:`get_shop_config` (central,
env-overridable). Two language variants (DE/EN) are chosen automatically from the
recipient's country and can be overridden. Only presentation lives here — no
order/matching/stock logic.

Unicode text (€, ×, ä/ö/ü/ß, –, unusual card names) is rendered with embedded
DejaVu fonts, since fpdf2 core fonts are limited to latin-1 (no €).
"""

from __future__ import annotations

import os
from datetime import datetime
from pathlib import Path
from typing import List, Optional, Sequence

from fpdf import FPDF
import re

_BASE = Path(__file__).resolve().parent
_FONT_DIR = _BASE / "static" / "fonts"
_DEFAULT_LOGO = _BASE / "static" / "img" / "logo.png"
_DEFAULT_BADGE = _BASE / "static" / "img" / "cardmarket_seal.png"

# Cardmarket "Private Seller" seal in the footer (aspect ratio ~247:285).
FOOTER_BADGE_HEIGHT_MM = 16
_BADGE_ASPECT = 247 / 285

# ---------------------------------------------------------------------------
# Page grid
# ---------------------------------------------------------------------------
PAGE_W_MM = 210
PAGE_H_MM = 297
PAGE_MARGIN_MM = 20          # left/right text margin
CONTENT_W_MM = PAGE_W_MM - 2 * PAGE_MARGIN_MM   # 170

# ---------------------------------------------------------------------------
# Address field — DIN 5008 Form B (window envelope). DO NOT CHANGE these, or the
# address no longer lines up with the envelope window.
# ---------------------------------------------------------------------------
ADDRESS_LEFT_MM = 20
ADDRESS_WIDTH_MM = 85
# ADDRESS_TOP_MM is THE single value to adjust after a test print so the address
# sits centred in the window. Sender line and recipient block derive from it.
ADDRESS_TOP_MM = 45
SENDER_LINE_MM = 45          # small grey return-address line (hairline below)
RECIPIENT_TOP_MM = 52        # recipient address starts here
RECIPIENT_LINE_H_MM = 5.6

# ---------------------------------------------------------------------------
# Letterhead + body vertical rhythm (mm from top)
# ---------------------------------------------------------------------------
LOGO_TOP_MM = 13
LOGO_WIDTH_MM = 46           # logo top-right, address field stays free on the left
SUBJECT_TOP_MM = 92
GREETING_TOP_MM = 104
TABLE_TOP_MM = 126
FOOTER_HAIRLINE_FROM_BOTTOM_MM = 41

# Fold / hole marks at the left sheet edge (DIN 676).
FOLD_MARKS_MM: Sequence[float] = (105.0, 210.0, 148.5)

# ---------------------------------------------------------------------------
# Colours (RGB)
# ---------------------------------------------------------------------------
INK = (28, 28, 26)           # #1C1C1A  body / headings
GOLD = (168, 132, 63)        # #A8843F  subject rule, table head, footer
GREY = (107, 104, 98)        # #6B6862  secondary text
HAIRLINE = (201, 196, 186)   # #C9C4BA  thin separators

# Table column geometry (x from left edge, mm)
_COL_QTY_X = PAGE_MARGIN_MM
_COL_CARD_X = PAGE_MARGIN_MM + 16
_COL_COND_X = 128
_PRICE_RIGHT_X = PAGE_W_MM - PAGE_MARGIN_MM   # 190, prices right-aligned here
_TOTALS_LABEL_RIGHT_X = 168

_SERIF = "NoteSerif"
_SANS = "NoteSans"

# ---------------------------------------------------------------------------
# Language: all user-visible strings live here (no strings in the layout code).
# ---------------------------------------------------------------------------
TEXTS = {
    "de": {
        "subject": "Bestellung {number}",
        "greeting": "Hallo {buyer},",
        "greeting_plain": "Hallo,",
        "thanks": "vielen Dank für deine Bestellung – wir freuen uns, dass du bei uns gekauft hast.",
        "note": "Die Karten wurden sorgfältig geprüft und geschützt verpackt. Viel Freude damit!",
        "col_qty": "MENGE", "col_card": "KARTE", "col_cond": "ZUSTAND", "col_price": "PREIS",
        "subtotal": "Zwischensumme", "shipping": "Versand", "total": "Gesamt",
        "review": "Über eine Bewertung auf Cardmarket freuen wir uns sehr.",
    },
    "en": {
        "subject": "Order {number}",
        "greeting": "Hello {buyer},",
        "greeting_plain": "Hello,",
        "thanks": "thank you very much for your order – we are glad you shopped with us.",
        "note": "All cards have been carefully checked and securely packaged. Enjoy!",
        "col_qty": "QTY", "col_card": "CARD", "col_cond": "CONDITION", "col_price": "PRICE",
        "subtotal": "Subtotal", "shipping": "Shipping", "total": "Total",
        "review": "We would greatly appreciate a rating on Cardmarket.",
    },
}

_MONTHS_EN = ["January", "February", "March", "April", "May", "June", "July",
              "August", "September", "October", "November", "December"]

# Synonyms for the domestic country -> language DE, and the DIN 5008 rule that
# the country line is omitted for domestic recipients.
_GERMANY = {"deutschland", "germany", "de", "deu", "ger", "allemagne",
            "duitsland", "germania", "tyskland", "alemania"}


# ---------------------------------------------------------------------------
# Configuration — sender / logo / badge in ONE place (env-overridable).
# ---------------------------------------------------------------------------
def get_shop_config() -> dict:
    """Return the shop's branding/sender config for the note.

    Override any value via environment: ``SHOP_NAME``, ``SHOP_SHORT_NAME``,
    ``SHOP_STREET``, ``SHOP_ZIP_CITY``, ``SHOP_CITY``, ``SHOP_COUNTRY``,
    ``SHOP_LOGO``, ``SHOP_BADGE``.
    """
    name = os.environ.get("SHOP_NAME", "Zur Festung – Cardmarket & Spielewelt")
    street = os.environ.get("SHOP_STREET", "Iltisweg 7")
    zip_city = os.environ.get("SHOP_ZIP_CITY", "24983 Handewitt")
    city = os.environ.get("SHOP_CITY", "Handewitt")
    country = os.environ.get("SHOP_COUNTRY", "Deutschland")
    # Short brand used in the (window-fitting) return address; defaults to the
    # part before the first en-dash in the full name.
    short_name = os.environ.get("SHOP_SHORT_NAME", name.split(" – ")[0].strip())
    logo_path = os.environ.get("SHOP_LOGO", str(_DEFAULT_LOGO))
    badge_path = os.environ.get("SHOP_BADGE", str(_DEFAULT_BADGE))
    return {
        "name": name,
        "short_name": short_name,
        "street": street,
        "zip_city": zip_city,
        "city": city,
        "country": country,
        "logo_path": logo_path,
        "badge_path": badge_path,
        # Short sender line so it fits the envelope window.
        "sender_line": f"{short_name} · {street} · {zip_city}",
        "footer_sender_line": f"{short_name} · {street} · {zip_city} · {country}",
    }


# ---------------------------------------------------------------------------
# Language / country helpers
# ---------------------------------------------------------------------------
def _country_from_lines(recipient_lines: Sequence[str]) -> str:
    """Return the country line if the last address line looks like a country.

    Cardmarket appends the country as the last line. A line containing digits
    (e.g. a postal code) is not treated as a country line.
    """
    if not recipient_lines:
        return ""
    last = str(recipient_lines[-1]).strip()
    if last and not any(ch.isdigit() for ch in last):
        return last
    return ""


def is_germany(country: str) -> bool:
    return (country or "").strip().lower().rstrip(".") in _GERMANY


def detect_language(recipient_lines: Sequence[str]) -> str:
    """Auto-detect the note language from the recipient's country.

    Germany (or no explicit country line) -> ``"de"`` (documented default, since
    domestic is the norm); any other recognized country -> ``"en"``.
    """
    country = _country_from_lines(recipient_lines)
    if not country:
        return "de"
    return "de" if is_germany(country) else "en"


# Valid Cardmarket buyer handle: a single token (no spaces, no sentence).
_HANDLE_RE = re.compile(r"[\wäöüÄÖÜß.\-]{2,30}$")


def _greeting(buyer_name: str, lang: str = "de") -> str:
    """Greeting in ``lang``; a garbage/empty buyer falls back to a plain greeting."""
    t = TEXTS.get(lang, TEXTS["de"])
    handle = (buyer_name or "").strip()
    if handle and " " not in handle and _HANDLE_RE.fullmatch(handle):
        return t["greeting"].format(buyer=handle)
    return t["greeting_plain"]


def _eur(value, lang: str = "de") -> str:
    """Format an amount: DE ``3,90 €`` (comma), EN ``€3.90`` (dot). Null-safe."""
    try:
        v = float(value)
    except (TypeError, ValueError):
        v = 0.0
    neg = v < 0
    v = abs(v)
    whole = int(v)
    frac = int(round((v - whole) * 100))
    if frac == 100:
        whole += 1
        frac = 0
    digits = str(whole)
    groups = []
    while len(digits) > 3:
        groups.insert(0, digits[-3:])
        digits = digits[:-3]
    groups.insert(0, digits)
    if lang == "en":
        body = "€" + ",".join(groups) + "." + f"{frac:02d}"
    else:
        body = ".".join(groups) + "," + f"{frac:02d}" + " €"
    return ("-" + body) if neg else body


def _format_date(date, lang: str = "de") -> str:
    """DE ``11.07.2026`` / EN ``11 July 2026``. A given string is used verbatim."""
    if isinstance(date, str) and date:
        return date
    d = date if hasattr(date, "year") else datetime.now()
    if lang == "en":
        return f"{d.day} {_MONTHS_EN[d.month - 1]} {d.year}"
    return d.strftime("%d.%m.%Y")


def _position_fields(pos):
    """Normalize a position into (quantity, name, set_name, condition, unit_price, foil)."""
    if isinstance(pos, dict):
        return (
            pos.get("quantity", 1),
            pos.get("name") or pos.get("card_name") or "",
            pos.get("set_name") or "",
            pos.get("condition") or "",
            pos.get("unit_price"),
            bool(pos.get("foil")),
        )
    qty = pos[0]
    name = pos[1]
    set_name = pos[2] if len(pos) > 2 else ""
    condition = pos[3] if len(pos) > 3 else ""
    unit_price = pos[4] if len(pos) > 4 else None
    foil = pos[5] if len(pos) > 5 else False
    return qty, name, set_name, condition, unit_price, foil


def _register_fonts(pdf: FPDF) -> None:
    pdf.add_font(_SERIF, "", str(_FONT_DIR / "DejaVuSerif.ttf"))
    pdf.add_font(_SERIF, "B", str(_FONT_DIR / "DejaVuSerif-Bold.ttf"))
    pdf.add_font(_SERIF, "I", str(_FONT_DIR / "DejaVuSerif-Italic.ttf"))
    pdf.add_font(_SANS, "", str(_FONT_DIR / "DejaVuSans.ttf"))
    pdf.add_font(_SANS, "B", str(_FONT_DIR / "DejaVuSans-Bold.ttf"))


def render_shipping_note(
    recipient_lines: Sequence[str],
    order_number: str,
    positions: Sequence,
    buyer_name: str = "",
    totals: Optional[dict] = None,
    date=None,
    lang: Optional[str] = None,
    config: Optional[dict] = None,
    logo_path: Optional[str] = None,
    badge_path: Optional[str] = None,
    compress: bool = True,
) -> bytes:
    """Render the branded A4 shipping note and return the PDF as bytes.

    Args:
        recipient_lines: confirmed recipient address, one entry per line
            (the last line may be the country).
        order_number: Cardmarket order number.
        positions: order items (dict with quantity/name/set_name/condition/
            unit_price/foil, or a matching sequence).
        buyer_name: Cardmarket buyer handle for the greeting.
        totals: {"subtotal", "shipping", "total"}; missing values are derived
            from the item prices.
        date: a date/datetime (formatted per language) or a preformatted string;
            defaults to today.
        lang: force ``"de"`` / ``"en"``; otherwise auto-detected from the country.
        config/logo_path/badge_path: branding overrides.
        compress: set ``False`` in tests so text is greppable in the raw PDF.
    """
    cfg = config or get_shop_config()
    logo = logo_path or cfg.get("logo_path")
    totals = dict(totals or {})

    # --- Language + recipient country line (DIN 5008) ---
    country = _country_from_lines(recipient_lines)
    domestic = bool(country) and is_germany(country)
    lang = (lang or detect_language(recipient_lines))
    lang = lang if lang in TEXTS else "de"
    t = TEXTS[lang]

    if country:
        body_lines = list(recipient_lines[:-1])
        if not domestic:                      # foreign: country in UPPERCASE
            body_lines.append(country.upper())
        # domestic: drop the country line entirely
    else:
        body_lines = list(recipient_lines)

    date_str = _format_date(date, lang)

    pdf = FPDF(orientation="P", unit="mm", format="A4")
    pdf.set_auto_page_break(auto=False)
    pdf.set_compression(compress)
    pdf.set_margins(PAGE_MARGIN_MM, LOGO_TOP_MM, PAGE_MARGIN_MM)
    _register_fonts(pdf)
    pdf.add_page()

    def text(x, y, s, family=_SERIF, style="", size=11, color=INK, w=0, align="L"):
        pdf.set_xy(x, y)
        pdf.set_font(family, style, size)
        pdf.set_text_color(*color)
        pdf.cell(w or (PAGE_W_MM - PAGE_MARGIN_MM - x), size * 0.42 + 2, s, align=align)

    # --- Logo top-right (transparent PNG -> no visible box) ---
    if logo and Path(logo).exists():
        try:
            pdf.image(logo, x=PAGE_W_MM - PAGE_MARGIN_MM - LOGO_WIDTH_MM,
                      y=LOGO_TOP_MM, w=LOGO_WIDTH_MM)
        except Exception:
            pass

    # --- Return-address line (small, grey) + hairline ---
    text(ADDRESS_LEFT_MM, SENDER_LINE_MM, cfg["sender_line"], _SANS, "", 6.8, GREY,
         w=ADDRESS_WIDTH_MM + 20)
    pdf.set_draw_color(*HAIRLINE)
    pdf.set_line_width(0.2)
    pdf.line(ADDRESS_LEFT_MM, SENDER_LINE_MM + 3.4, ADDRESS_LEFT_MM + ADDRESS_WIDTH_MM,
             SENDER_LINE_MM + 3.4)

    # --- Recipient address in the window ---
    y = RECIPIENT_TOP_MM
    for line in body_lines:
        text(ADDRESS_LEFT_MM, y, line, _SANS, "", 11, INK, w=ADDRESS_WIDTH_MM)
        y += RECIPIENT_LINE_H_MM

    # --- Subject + city/date + gold rule ---
    text(PAGE_MARGIN_MM, SUBJECT_TOP_MM, t["subject"].format(number=order_number),
         _SERIF, "B", 14, INK, w=120)
    text(PAGE_MARGIN_MM, SUBJECT_TOP_MM + 1.5, f"{cfg['city']}, {date_str}",
         _SANS, "", 9.5, GREY, w=CONTENT_W_MM, align="R")
    pdf.set_draw_color(*GOLD)
    pdf.set_line_width(0.4)
    pdf.line(PAGE_MARGIN_MM, SUBJECT_TOP_MM + 8, PAGE_W_MM - PAGE_MARGIN_MM, SUBJECT_TOP_MM + 8)

    # --- Greeting + thank-you + note ---
    text(PAGE_MARGIN_MM, GREETING_TOP_MM, _greeting(buyer_name, lang), _SERIF, "", 11, INK)
    text(PAGE_MARGIN_MM, GREETING_TOP_MM + 6, t["thanks"], _SERIF, "", 11, INK, w=CONTENT_W_MM)
    text(PAGE_MARGIN_MM, GREETING_TOP_MM + 12, t["note"], _SERIF, "I", 10.5, GREY, w=CONTENT_W_MM)

    # --- Positions table header ---
    y = TABLE_TOP_MM
    pdf.set_font(_SANS, "B", 7.5)
    pdf.set_text_color(*GOLD)
    pdf.set_char_spacing(0.6)
    pdf.set_xy(_COL_QTY_X, y); pdf.cell(16, 5, t["col_qty"])
    pdf.set_xy(_COL_CARD_X, y); pdf.cell(70, 5, t["col_card"])
    pdf.set_xy(_COL_COND_X, y); pdf.cell(35, 5, t["col_cond"])
    pdf.set_xy(_PRICE_RIGHT_X - 30, y); pdf.cell(30, 5, t["col_price"], align="R")
    pdf.set_char_spacing(0)
    y += 6
    pdf.set_draw_color(*HAIRLINE)
    pdf.line(PAGE_MARGIN_MM, y, PAGE_W_MM - PAGE_MARGIN_MM, y)
    y += 2

    subtotal = 0.0
    for pos in positions:
        qty, name, set_name, condition, unit_price, foil = _position_fields(pos)
        if unit_price is not None:
            subtotal += float(unit_price) * float(qty or 1)
        display_name = f"{name} — Foil" if foil else name
        text(_COL_QTY_X, y, f"{qty}×", _SERIF, "", 11, INK, w=16)
        text(_COL_CARD_X, y, display_name, _SERIF, "", 11, INK, w=88)
        text(_COL_COND_X, y, condition or "", _SERIF, "", 11, INK, w=34)
        if unit_price is not None:
            text(_PRICE_RIGHT_X - 34, y, _eur(unit_price, lang), _SERIF, "", 11, INK, w=34, align="R")
        if set_name:
            text(_COL_CARD_X, y + 4.6, set_name, _SERIF, "I", 8.5, GREY, w=88)
        y += 11
        pdf.set_draw_color(*HAIRLINE)
        pdf.line(PAGE_MARGIN_MM, y - 2, PAGE_W_MM - PAGE_MARGIN_MM, y - 2)

    # --- Totals (right-aligned) ---
    sub = totals.get("subtotal")
    if sub is None:
        sub = subtotal
    shipping = totals.get("shipping") or 0.0
    total = totals.get("total")
    if total is None:
        total = (sub or 0.0) + shipping

    y += 3

    def total_row(yy, label, value, bold=False):
        style = "B" if bold else ""
        text(_TOTALS_LABEL_RIGHT_X - 60, yy, label, _SERIF, style, 11,
             INK if bold else GREY, w=60, align="R")
        text(_TOTALS_LABEL_RIGHT_X, yy, _eur(value, lang), _SERIF, style, 11, INK,
             w=_PRICE_RIGHT_X - _TOTALS_LABEL_RIGHT_X, align="R")

    total_row(y, t["subtotal"], sub)
    total_row(y + 6, t["shipping"], shipping)
    pdf.set_draw_color(*INK)
    pdf.set_line_width(0.4)
    pdf.line(_TOTALS_LABEL_RIGHT_X - 60, y + 13.5, _PRICE_RIGHT_X, y + 13.5)
    total_row(y + 15, t["total"], total, bold=True)

    # --- Footer: hairline, Cardmarket seal, review sentence, return address ---
    hairline_y = PAGE_H_MM - FOOTER_HAIRLINE_FROM_BOTTOM_MM
    pdf.set_draw_color(*HAIRLINE)
    pdf.set_line_width(0.2)
    pdf.line(PAGE_MARGIN_MM, hairline_y, PAGE_W_MM - PAGE_MARGIN_MM, hairline_y)

    badge = badge_path or cfg.get("badge_path")
    badge_y = hairline_y + 2
    if badge and Path(badge).exists():
        try:
            bw = FOOTER_BADGE_HEIGHT_MM * _BADGE_ASPECT
            pdf.image(badge, x=(PAGE_W_MM - bw) / 2, y=badge_y, h=FOOTER_BADGE_HEIGHT_MM)
        except Exception:
            pass
    sentence_y = badge_y + FOOTER_BADGE_HEIGHT_MM + 2
    text(PAGE_MARGIN_MM, sentence_y, t["review"], _SERIF, "I", 10, GOLD,
         w=CONTENT_W_MM, align="C")
    text(PAGE_MARGIN_MM, sentence_y + 6, cfg.get("footer_sender_line", cfg["sender_line"]),
         _SANS, "", 8.5, GREY, w=CONTENT_W_MM, align="C")

    # --- Fold / hole marks ---
    pdf.set_draw_color(*HAIRLINE)
    pdf.set_line_width(0.2)
    for fy in FOLD_MARKS_MM:
        pdf.line(0, fy, 5, fy)

    return bytes(pdf.output())
