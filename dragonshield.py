"""Helpers for the Dragonshield CSV import.

Pure, dependency-free functions for normalizing set codes and languages to the
Scryfall convention, deriving the foil flag from the ``Printing`` column, and
extracting the identity-relevant fields from a parsed CSV row. Kept separate
from ``web.py`` so the logic is easy to unit-test.
"""

from __future__ import annotations

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


def extract_row(row: Dict[str, str]) -> Tuple[dict, Optional[str]]:
    """Extract and normalize the identity fields from a parsed CSV row.

    ``row`` keys are expected already lower-cased with spaces replaced by
    underscores (e.g. ``card_name``, ``set_code``, ``card_number``,
    ``printing``, ``language``), as produced by the bulk import.

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

    price_raw = _first(row, "price_bought", "price")
    try:
        price = float(price_raw.replace(",", ".")) if price_raw else 0.0
    except ValueError:
        price = 0.0

    fields = {
        "name": name,
        "set_code": normalize_set_code(set_raw),
        "set_code_raw": set_raw,
        "collector_number": collector,
        "language": normalize_language(_first(row, "language", "lang")),
        "foil": derive_foil(_first(row, "printing")),
        "condition": _first(row, "condition"),
        "quantity": quantity,
        "price": price,
    }

    # Structural validation — do not guess. A row needs at least the identity
    # fields (set code + collector number) to be enriched.
    if not name and not set_raw and not collector:
        return fields, "Leere oder unlesbare Zeile"
    if not fields["set_code"] or not collector:
        return fields, "Set-Code oder Kartennummer fehlt"
    return fields, None
