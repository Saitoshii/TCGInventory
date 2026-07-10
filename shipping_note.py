"""A4 shipping-note (Beileger) generator for a DIN-5008 Form B window envelope.

Produces a PDF with the project's own PDF library (``fpdf2``): a letterhead with
company name + logo, a small underlined return-address line, the recipient
address positioned in the envelope window, a thank-you note and the order
overview. No postage/label here (that stays parked for a later package).

The address geometry follows DIN 5008 Form B. All measurements are millimetres
from the top/left edge of the sheet and are kept as named constants below so a
single value can be nudged after a test print.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import List, Optional, Sequence

from fpdf import FPDF

# ---------------------------------------------------------------------------
# DIN 5008 Form B — address field geometry (mm from the top/left sheet edge).
# ---------------------------------------------------------------------------
ADDRESS_LEFT_MM = 20        # left edge of the address field
ADDRESS_WIDTH_MM = 85       # width of the address field

# ADDRESS_TOP_MM is THE single value to adjust after a test print: increase or
# decrease it so the recipient address sits centred in the envelope window.
# Everything else (sender line, recipient block) is derived from it, so nudging
# this one number moves the whole address zone up/down.
ADDRESS_TOP_MM = 45

SENDER_LINE_MM = ADDRESS_TOP_MM         # small underlined return-address (~45 mm)
RECIPIENT_TOP_MM = ADDRESS_TOP_MM + 7   # recipient address starts (~52 mm)

# Fold / hole marks at the left sheet edge (DIN 676): first fold, second fold
# and the centre punch-hole mark. Drawn as short ticks to help folding.
FOLD_MARKS_MM: Sequence[float] = (105.0, 210.0, 148.5)

# Letterhead / body layout.
_HEADER_TOP_MM = 12
_LOGO_HEIGHT_MM = 18
_BODY_TOP_MM = 110  # below the address window

_DEFAULT_LOGO = Path(__file__).resolve().parent / "static" / "img" / "logo.png"


# ---------------------------------------------------------------------------
# Configuration — sender + logo maintained in ONE place (env-overridable).
# ---------------------------------------------------------------------------
def get_shop_config() -> dict:
    """Return the shop's sender/branding config.

    Central place to maintain company name, the one-line return address and the
    logo path. Override via environment (``SHOP_NAME``, ``SHOP_SENDER_LINE``,
    ``SHOP_LOGO``) so nothing is hard-coded inside the generator.
    """
    name = os.environ.get("SHOP_NAME", "TCG Inventory")
    sender_line = os.environ.get(
        "SHOP_SENDER_LINE",
        f"{name} · Musterstraße 1 · 12345 Musterstadt",
    )
    logo_path = os.environ.get("SHOP_LOGO", str(_DEFAULT_LOGO))
    return {"name": name, "sender_line": sender_line, "logo_path": logo_path}


def _format_position(pos) -> str:
    """Format one order position for the overview line."""
    if isinstance(pos, dict):
        qty = pos.get("quantity", 1)
        name = pos.get("name") or pos.get("card_name") or ""
        set_code = pos.get("set_code")
        foil = pos.get("foil")
    else:  # (quantity, name) tuple/sequence
        qty, name = pos[0], pos[1]
        set_code = pos[2] if len(pos) > 2 else None
        foil = pos[3] if len(pos) > 3 else None
    line = f"{qty}x {name}"
    if set_code:
        line += f" ({str(set_code).upper()})"
    if foil:
        line += " · Foil"
    return line


def render_shipping_note(
    recipient_lines: Sequence[str],
    order_number: str,
    positions: Sequence,
    config: Optional[dict] = None,
    logo_path: Optional[str] = None,
    compress: bool = True,
) -> bytes:
    """Render the A4 shipping note and return the PDF as bytes.

    Args:
        recipient_lines: the confirmed recipient address, one entry per line.
        order_number: the Cardmarket order number.
        positions: order items (dicts with quantity/name/set_code/foil, or
            (quantity, name[, set_code[, foil]]) sequences).
        config: sender/branding config (defaults to :func:`get_shop_config`).
        logo_path: override the logo path from the config.
        compress: set ``False`` in tests so text is greppable in the raw PDF.
    """
    cfg = config or get_shop_config()
    logo = logo_path or cfg.get("logo_path")

    pdf = FPDF(orientation="P", unit="mm", format="A4")
    pdf.set_auto_page_break(auto=True, margin=15)
    pdf.set_compression(compress)
    pdf.set_margins(ADDRESS_LEFT_MM, _HEADER_TOP_MM, ADDRESS_LEFT_MM)
    pdf.add_page()

    # --- Letterhead: logo (optional) + company name ---
    header_text_x = ADDRESS_LEFT_MM
    if logo and Path(logo).exists():
        try:
            pdf.image(logo, x=ADDRESS_LEFT_MM, y=_HEADER_TOP_MM, h=_LOGO_HEIGHT_MM)
            header_text_x = ADDRESS_LEFT_MM + _LOGO_HEIGHT_MM * 1.6 + 4
        except Exception:
            # Missing Pillow / unreadable image -> letterhead falls back to text.
            header_text_x = ADDRESS_LEFT_MM
    pdf.set_xy(header_text_x, _HEADER_TOP_MM + 3)
    pdf.set_font("Helvetica", "B", 20)
    pdf.cell(0, 10, cfg.get("name", ""), new_x="LMARGIN", new_y="NEXT")

    # --- Return-address line (small, underlined) inside the address column ---
    pdf.set_xy(ADDRESS_LEFT_MM, SENDER_LINE_MM)
    pdf.set_font("Helvetica", "U", 8)
    pdf.cell(ADDRESS_WIDTH_MM, 4, cfg.get("sender_line", ""))

    # --- Recipient address (positioned in the envelope window) ---
    pdf.set_xy(ADDRESS_LEFT_MM, RECIPIENT_TOP_MM)
    pdf.set_font("Helvetica", "", 11)
    for line in recipient_lines:
        pdf.set_x(ADDRESS_LEFT_MM)
        pdf.multi_cell(ADDRESS_WIDTH_MM, 5.5, line, new_x="LMARGIN", new_y="NEXT")

    # --- Fold / hole marks at the left sheet edge ---
    pdf.set_line_width(0.2)
    for y in FOLD_MARKS_MM:
        pdf.line(0, y, 5, y)

    # --- Body: thank-you + order overview ---
    pdf.set_xy(ADDRESS_LEFT_MM, _BODY_TOP_MM)
    pdf.set_font("Helvetica", "B", 14)
    pdf.cell(0, 8, f"Beileger zur Bestellung {order_number}", new_x="LMARGIN", new_y="NEXT")
    pdf.ln(2)
    pdf.set_font("Helvetica", "", 11)
    pdf.multi_cell(
        0, 6,
        "Vielen Dank für deinen Einkauf! Anbei deine Bestellung. "
        "Bei Fragen kannst du dich jederzeit über Cardmarket bei uns melden.",
        new_x="LMARGIN", new_y="NEXT",
    )
    pdf.ln(3)
    pdf.set_font("Helvetica", "B", 12)
    pdf.cell(0, 7, "Bestellübersicht", new_x="LMARGIN", new_y="NEXT")
    pdf.set_font("Helvetica", "", 11)
    if positions:
        for pos in positions:
            pdf.set_x(ADDRESS_LEFT_MM)
            pdf.multi_cell(0, 6, _format_position(pos), new_x="LMARGIN", new_y="NEXT")
    else:
        pdf.cell(0, 6, "(keine Positionen)", new_x="LMARGIN", new_y="NEXT")

    return bytes(pdf.output())
