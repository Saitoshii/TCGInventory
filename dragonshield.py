"""Helpers for the CSV import (Dragonshield and compatible scan apps).

Pure, dependency-free functions for normalizing set codes, languages and
conditions to the conventions used in the inventory, deriving the foil flag from
the ``Printing`` column, and extracting the identity-relevant fields from a
parsed CSV row. Kept separate from ``web.py`` so the logic is easy to unit-test.

Two export layouts are supported; both use the same column names for the fields
that matter, so they share one code path:

* Dragonshield: ``Folder Name, Quantity, Trade Quantity, Card Name, Set Code,
  Set Name, Card Number, Condition, Printing, Language, Price Bought,
  Date Bought, LOW, MID, MARKET``
* Scan apps with list export: ``List Type, List Name, Collection, Format, Board,
  Quantity, Card Name, Set Code, Set Name, Card Number, Condition, Printing,
  Rarity, Language, Price Bought, Date Bought, Parent List Type,
  Parent List Name, Current Price (…), List Cover Image, …``

Extra columns are read when present and ignored when absent — no layout
detection, no guessing.
"""

from __future__ import annotations

import re
from typing import Dict, Optional, Tuple

# ---------------------------------------------------------------------------
# Set code normalization
# ---------------------------------------------------------------------------
# Dragonshield writes set codes in upper case (e.g. ``ACR``) while Scryfall uses
# lower case (``acr``). Most cases are handled by lower-casing alone. Genuine
# deviations (where the code itself differs) go into this small, extensible
# table, keyed by the lower-cased Dragonshield code -> lower-cased Scryfall code.
SET_CODE_ALIASES: Dict[str, str] = {
    # "example_ds": "example_scry",   # add real deviations here as they surface
}


def normalize_set_code(code: str) -> str:
    """Normalize a Dragonshield set code to the Scryfall convention.

    Lower-cases the code and applies any known alias. Returns ``""`` for empty
    input.
    """
    if not code:
        return ""
    c = code.strip().lower()
    return SET_CODE_ALIASES.get(c, c)


# ---------------------------------------------------------------------------
# Language normalization
# ---------------------------------------------------------------------------
# Dragonshield exports full language names ("English", "German", …); Scryfall
# uses ISO-ish codes ("en", "de", …). Unknown values are passed through
# lower-cased (best effort) rather than guessed away.
LANGUAGE_ALIASES: Dict[str, str] = {
    "english": "en", "englisch": "en", "en": "en",
    "german": "de", "deutsch": "de", "de": "de",
    "french": "fr", "französisch": "fr", "franzoesisch": "fr",
    "français": "fr", "francais": "fr", "fr": "fr",
    "italian": "it", "italienisch": "it", "italiano": "it", "it": "it",
    "spanish": "es", "spanisch": "es", "español": "es", "espanol": "es", "es": "es",
    "portuguese": "pt", "portugiesisch": "pt", "português": "pt", "portugues": "pt", "pt": "pt",
    "japanese": "ja", "japanisch": "ja", "jp": "ja", "ja": "ja",
    "korean": "ko", "koreanisch": "ko", "ko": "ko",
    "russian": "ru", "russisch": "ru", "ru": "ru",
    "chinese simplified": "zhs", "simplified chinese": "zhs", "zhs": "zhs",
    "chinese traditional": "zht", "traditional chinese": "zht", "zht": "zht",
    "chinesisch": "zhs",
}


def normalize_language(language: str) -> str:
    """Map a Dragonshield language name to a Scryfall language code.

    Unknown values are returned lower-cased and stripped (best effort).
    """
    if not language:
        return ""
    return LANGUAGE_ALIASES.get(language.strip().lower(), language.strip().lower())


# ---------------------------------------------------------------------------
# Condition normalization
# ---------------------------------------------------------------------------
# The inventory stores Cardmarket condition codes (see
# ``lager_manager.CONDITION_VALUES``); the filter in the card list matches them
# exactly. Exports write the long names instead ("NearMint", "Near Mint",
# "Excellent" …), which would never match the filter — so they are mapped here.
# The Cardmarket ladder MT/NM/EX/GD/LP/PL/PO maps 1:1 onto the long names used
# by Dragonshield and compatible scan apps.
CONDITION_ALIASES: Dict[str, str] = {
    "mint": "MT", "mt": "MT",
    "nearmint": "NM", "near mint": "NM", "nm": "NM",
    "excellent": "EX", "ex": "EX",
    "good": "GD", "gd": "GD", "gut": "GD",
    "lightplayed": "LP", "light played": "LP", "lightly played": "LP", "lp": "LP",
    "played": "PL", "pl": "PL", "gespielt": "PL",
    "poor": "PO", "po": "PO", "schlecht": "PO",
}


def normalize_condition(condition: str) -> str:
    """Map an exported condition name to a Cardmarket condition code.

    Unknown values are returned stripped but otherwise unchanged (best effort,
    never guessed): the five-step TCGplayer ladder ("Moderately Played",
    "Heavily Played", "Damaged") has no accepted 1:1 mapping onto the seven-step
    Cardmarket ladder, so such values are kept verbatim and stay visible in the
    upload queue instead of being silently reinterpreted.
    """
    if not condition:
        return ""
    c = condition.strip()
    return CONDITION_ALIASES.get(c.lower(), c)


# ---------------------------------------------------------------------------
# Rarity / date / price helpers
# ---------------------------------------------------------------------------
# Scan-app exports write Scryfall rarities in lower case ("common", "mythic");
# Dragonshield has no rarity column at all. Values are kept as-is apart from
# case, so nothing is invented.
RARITY_VALUES = {"common", "uncommon", "rare", "mythic", "special", "bonus"}

_ISO_DATE = re.compile(r"^(\d{4})-(\d{2})-(\d{2})")
_DE_DATE = re.compile(r"^(\d{1,2})\.(\d{1,2})\.(\d{4})$")


def normalize_rarity(rarity: str) -> str:
    """Lower-case a rarity value. Unknown values pass through unchanged."""
    if not rarity:
        return ""
    return rarity.strip().lower()


def normalize_date(value: str) -> str:
    """Normalize a purchase date to ``YYYY-MM-DD``.

    Accepts ISO dates (optionally with a time part) and the German
    ``DD.MM.YYYY`` form. Anything else is returned stripped but unchanged rather
    than reinterpreted.
    """
    if not value:
        return ""
    v = value.strip()
    m = _ISO_DATE.match(v)
    if m:
        return f"{m.group(1)}-{m.group(2)}-{m.group(3)}"
    m = _DE_DATE.match(v)
    if m:
        return f"{m.group(3)}-{int(m.group(2)):02d}-{int(m.group(1)):02d}"
    return v


def parse_price(value: str) -> Optional[float]:
    """Parse a price cell. Returns ``None`` when empty or unparseable."""
    if not value:
        return None
    try:
        return float(value.strip().replace(",", "."))
    except ValueError:
        return None


def derive_foil(printing: str) -> bool:
    """Derive the foil flag from the Dragonshield ``Printing`` column.

    ``"Foil"`` -> ``True``; everything else (``"Normal"``, empty, unknown) ->
    ``False``. This replaces any manual foil entry in the bulk-add flow.
    """
    return (printing or "").strip().lower() == "foil"


def _first(row: Dict[str, str], *keys: str) -> str:
    """Return the first non-empty value among ``keys`` from ``row``."""
    for k in keys:
        v = row.get(k)
        if v:
            return v.strip()
    return ""


def _first_prefix(row: Dict[str, str], prefix: str) -> str:
    """Return the first non-empty value whose column name starts with ``prefix``.

    Used for columns whose header carries the configured price source in
    parentheses, e.g. ``current_price_(cardmarket_avgsellprice)`` or
    ``current_price_(cardmarket_trendprice)``. Keys are iterated in the CSV's own
    column order, so the result is deterministic.
    """
    for k, v in row.items():
        if k.startswith(prefix) and v:
            return v.strip()
    return ""


def extract_row(row: Dict[str, str]) -> Tuple[dict, Optional[str]]:
    """Extract and normalize the identity fields from a parsed CSV row.

    ``row`` keys are expected already lower-cased with spaces replaced by
    underscores (e.g. ``card_name``, ``set_code``, ``card_number``,
    ``printing``, ``language``), as produced by the bulk import.

    Beyond the identity fields the optional columns of the scan-app layout are
    read when present: ``rarity``, ``date_bought``, ``set_name``, ``list_name``
    (the source list/folder of the export) and ``current_price_…`` (the market
    price at export time). Absent columns simply yield empty values.

    Returns ``(fields, error_reason)``. ``error_reason`` is ``None`` when the row
    is structurally usable; otherwise it names why the row cannot be resolved and
    must go to Needs-Review (never guessed).
    """
    name = _first(row, "card_name", "name")
    set_raw = _first(row, "set_code", "set")
    collector = _first(row, "card_number", "collector_number")

    qty_raw = _first(row, "quantity") or "1"
    try:
        quantity = max(1, int(float(qty_raw)))
    except ValueError:
        quantity = 1

    price = parse_price(_first(row, "price_bought", "price"))
    # Market price at export time. Dragonshield writes MARKET, scan apps write
    # "Current Price (<source>)". Kept separate from the purchase price.
    market_price = parse_price(
        _first(row, "market") or _first_prefix(row, "current_price")
    )

    condition_raw = _first(row, "condition")

    fields = {
        "name": name,
        "set_code": normalize_set_code(set_raw),
        "set_code_raw": set_raw,
        "set_name": _first(row, "set_name"),
        "collector_number": collector,
        "language": normalize_language(_first(row, "language", "lang")),
        "foil": derive_foil(_first(row, "printing")),
        "condition": normalize_condition(condition_raw),
        "condition_raw": condition_raw,
        "quantity": quantity,
        "price": price if price is not None else 0.0,
        "market_price": market_price,
        "rarity": normalize_rarity(_first(row, "rarity")),
        "date_bought": normalize_date(_first(row, "date_bought")),
        # Source list/folder of the export ("Folder Name" / "List Name"). Only
        # informational — the target folder is always chosen in the import form.
        "source_list": _first(row, "list_name", "folder_name"),
    }

    # Structural validation — do not guess. A row needs at least the identity
    # fields (set code + collector number) to be enriched.
    if not name and not set_raw and not collector:
        return fields, "Leere oder unlesbare Zeile"
    if not fields["set_code"] or not collector:
        return fields, "Set-Code oder Kartennummer fehlt"
    return fields, None
