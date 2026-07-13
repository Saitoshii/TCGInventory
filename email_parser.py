"""Parser for Cardmarket 'Bitte versenden' emails."""

import re
from typing import Dict, List, Optional, Tuple

from TCGInventory.dragonshield import normalize_language

# Blacklist of common email signatures and greetings that should not be used as buyer names
# All comparisons are case-insensitive (lowercase)
# Note: Short greetings like 'hallo', 'hello', 'hi' are safe to blacklist because:
# - They appear in the first 10 lines of emails (greeting section)
# - Real buyer names are extracted from subject line or body patterns (e.g., "X hat Bestellung")
# - These would never be valid buyer usernames on Cardmarket
BUYER_NAME_BLACKLIST = frozenset([
    'das cardmarket-team',
    'cardmarket-team',
    'cardmarket',
    'vielen dank',
    'thank you',
    'best regards',
    'mit freundlichen grüßen',
    'grüße',
    'hallo',
    'hello',
    'hi',
])


def parse_cardmarket_email(email_body: str, message_id: str, subject: str = '', email_date: str = None) -> Dict:
    """
    Parse a Cardmarket shipping notification email.
    
    The email format typically contains:
    - Buyer name (in subject or body)
    - List of cards in format like "1x Airbending Lesson" or "2x Card Name"
    - Email date
    
    Args:
        email_body: The email body text
        message_id: Gmail message ID
        subject: Email subject line (optional)
        email_date: Email date timestamp (optional)
        
    Returns:
        Dictionary with buyer_name, items (list of {qty, card_name}), message_id, and email_date
    """
    result = {
        'buyer_name': '',
        'items': [],
        'message_id': message_id,
        'email_date': email_date
    }
    
    # Extract buyer name - try subject line first (e.g., "Bestellung 1250416803 für KohlkopfKlaus: Bitte versenden")
    if subject:
        subject_patterns = [
            r'für\s+([^:\n]+):\s*Bitte versenden',  # "für KohlkopfKlaus: Bitte versenden"
            r'for\s+([^:\n]+):\s*Please ship',  # English variant
        ]
        
        for pattern in subject_patterns:
            match = re.search(pattern, subject, re.IGNORECASE)
            if match:
                result['buyer_name'] = match.group(1).strip()
                break
    
    # If not in subject, try body patterns
    if not result['buyer_name']:
        # Look for patterns like "KohlkopfKlaus hat Bestellung" or "Obi83 has paid for shipment"
        body_patterns = [
            r'([A-Za-z0-9äöüÄÖÜß_\-]+)\s+hat\s+Bestellung',  # "KohlkopfKlaus hat Bestellung"
            r'([A-Za-z0-9äöüÄÖÜß_\-]+)\s+has\s+paid\s+for\s+shipment',  # "Obi83 has paid for shipment"
            r'[Kk]äufer:\s*([^\n]+)',
            r'[Bb]uyer:\s*([^\n]+)',
            r'[Bb]estellung\s+von:\s*([^\n]+)',
            r'[Oo]rder\s+from:\s*([^\n]+)',
        ]
        
        for pattern in body_patterns:
            match = re.search(pattern, email_body, re.MULTILINE)
            if match:
                result['buyer_name'] = match.group(1).strip()
                break
    
    # If no buyer name found, try to extract from email header or use a default
    if not result['buyer_name']:
        # Look for a name in the first few lines
        lines = email_body.split('\n')[:10]
        for line in lines:
            line = line.strip()
            # Skip empty lines and common email headers
            if line and not line.startswith(('From:', 'To:', 'Subject:', 'Date:')):
                # Check if it looks like a name (contains letters and possibly spaces)
                if re.match(r'^[A-Za-zÄÖÜäöüß\s\-]+$', line) and 3 <= len(line) <= 50:
                    # Check against blacklist (case-insensitive)
                    if line.lower() not in BUYER_NAME_BLACKLIST:
                        result['buyer_name'] = line
                        break
    
    # Default buyer name if still not found
    if not result['buyer_name']:
        result['buyer_name'] = 'Unknown Buyer'
    
    # Parse card items - look for patterns like "1x Card Name" or "2x Another Card"
    # Be flexible with whitespace and case variations
    item_patterns = [
        r'(\d+)\s*[xX×]\s*([^\n]+)',  # Standard format: 1x Card Name
        r'(\d+)\s+[Ss]tück\s+([^\n]+)',  # German format: 1 Stück Card Name
        r'^([^\n]*?[A-Za-z][^\n]*?)\s*[xX×]\s*(\d+)\s*$',  # Reverse: Card Name x1 (must contain letter)
    ]
    
    found_items = []
    
    for pattern in item_patterns:
        matches = re.finditer(pattern, email_body, re.MULTILINE)
        for match in matches:
            if pattern == r'^([^\n]*?[A-Za-z][^\n]*?)\s*[xX×]\s*(\d+)\s*$':
                # Reversed pattern
                card_name = match.group(1).strip()
                qty = match.group(2).strip()
            else:
                qty = match.group(1).strip()
                card_name = match.group(2).strip()
            
            # Clean up the card name
            card_name = _clean_card_name(card_name)
            
            # Skip if card name is too short or looks like junk
            if len(card_name) < 3 or not re.search(r'[A-Za-z]', card_name):
                continue
            
            # Skip duplicates (same card name)
            if any(item['card_name'].lower() == card_name.lower() for item in found_items):
                continue
            
            try:
                quantity = int(qty)
                if quantity > 0:
                    found_items.append({
                        'quantity': quantity,
                        'card_name': card_name
                    })
            except ValueError:
                continue
    
    result['items'] = found_items
    return result


def _clean_card_name(name: str) -> str:
    """
    Clean up a card name by removing extra whitespace and common artifacts.
    
    Handles formats like:
    - "Airbending Lesson (Magic: The Gathering | Avatar: The Last Airbe... 0,02 EUR)"
    - "Card Name (Set Name | ... 1,50 EUR)"
    - "Sothera, the Supervoid (Edge of Eternities) - M - English - NM 3,98 EUR"
    - "Lorwyn Eclipsed Play Booster Box (Lorwyn Eclipsed) - English 140,00 EUR"
    - "Annie Joins Up - R - Deuts" (truncated language)
    - "Kavaron Harrier - U - Englisch - NM" (variant language spelling)
    - "Card Name - R" (rarity-only suffix)
    
    Args:
        name: Raw card name
        
    Returns:
        Cleaned card name
    """
    # Remove leading/trailing whitespace
    name = name.strip()
    
    # Remove common email artifacts
    name = re.sub(r'[\r\n\t]+', ' ', name)
    
    # Remove multiple spaces
    name = re.sub(r'\s{2,}', ' ', name)
    
    # Remove price suffixes with EUR/€ (e.g., "0,02 EUR", "1.50 EUR", "5 EUR")
    # Handle both comma and dot decimals, and optional decimals
    name = re.sub(r'\s*[\d]+(?:[.,][\d]+)?\s*(EUR|€)\s*\)?$', '', name, flags=re.IGNORECASE)
    
    # Remove format suffixes with truncated/partial languages: "- <rarity> - <partial_language> [- <condition>] [trailing]"
    # Matches patterns like:
    # - "- R - Deuts" (truncated German)
    # - "- U - Englisch - NM" (German variant of English)
    # - "- M - Eng" (truncated English)
    # Language patterns (min 3 chars to avoid false positives):
    # Eng[lish|lisch]?, Deu[t|ts|tsch]?, German, Franz?, French, Ital?, Italian, Span?, Spanish, Port?, Portuguese, Jap?, Japanese, Chin?, Chinese, Kor?, Korean
    
    # Build language pattern from common truncations and full names
    lang_prefixes = [
        r'Eng(?:l(?:ish|isch)?)?',  # Eng, Engl, English, Englisch
        r'Deu(?:t(?:s(?:ch)?)?)?',  # Deu, Deut, Deuts, Deutsch
        r'German',
        r'Fran(?:z)?',               # Fran, Franz
        r'French',
        r'Ital(?:ian)?',            # Ital, Italian
        r'Span(?:ish)?',            # Span, Spanish
        r'Port(?:uguese)?',         # Port, Portuguese
        r'Jap(?:anese)?',           # Jap, Japanese
        r'Chin(?:ese)?',            # Chin, Chinese
        r'Kor(?:ean)?',             # Kor, Korean
    ]
    lang_pattern = r'(?:' + '|'.join(lang_prefixes) + r')'
    
    condition_pattern = r'(?:NM|EX|GD|LP|PL|HP|DMG|M|Near\s*Mint|Excellent|Good|Light\s*Played|Played|Heavily\s*Played|Damaged)'
    
    # Pattern for: "- <rarity> - <language> [- <condition>] [anything]"
    # Use specific single rarity codes [RUMC] rather than [A-Z]+ to avoid false positives
    name = re.sub(
        rf'\s*-\s*[RUMC]\s*-\s*{lang_pattern}(?:\s*-\s*{condition_pattern})?.*$',
        '',
        name,
        flags=re.IGNORECASE
    )
    
    # Remove English format suffixes: "- <rarity> - <language> - <condition> <price>"
    # Example: "- M - English - NM 3,98 EUR" or "- R - German - EX 1,00 EUR"
    # Keep this for full language names that might not match the pattern above
    name = re.sub(r'\s*-\s*[A-Z]+\s*-\s*(English|German|French|Italian|Spanish|Portuguese|Japanese|Chinese|Korean)\s*-\s*(NM|EX|GD|LP|PL|HP|DMG|M|Near Mint|Excellent|Good|Light Played|Played|Heavily Played|Damaged).*$', '', name, flags=re.IGNORECASE)
    
    # Remove rarity-only suffixes: "- <single_letter_rarity> [trailing]"
    # Example: "- R", "- U", "- M", "- C"
    # Only match single letters (common rarity codes: R=Rare, U=Uncommon, M=Mythic, C=Common)
    # Use word boundary to handle edge cases like "- R-something"
    name = re.sub(r'\s*-\s*[RUMC]\b.*$', '', name, flags=re.IGNORECASE)
    
    # Remove language-only suffixes: "- <language>"
    # Example: "- English" or "- German"
    name = re.sub(r'\s*-\s*(English|German|French|Italian|Spanish|Portuguese|Japanese|Chinese|Korean)\s*$', '', name, flags=re.IGNORECASE)
    
    # Remove set/expansion info in parentheses that contains a pipe (|)
    # This handles formats like "(Magic: The Gathering | Avatar: The Last Airbe..." 
    name = re.sub(r'\s*\([^)]*\|.*$', '', name)
    
    # Remove remaining set/expansion info in parentheses that might include ellipsis
    name = re.sub(r'\s*\([^)]*\.{3,}[^)]*$', '', name)
    
    # Remove parenthesized set names that are repeated (e.g., "Lorwyn Eclipsed Play Booster Box (Lorwyn Eclipsed)")
    # Match parentheses that contain words already in the card name
    # Only filter out very short words (2 chars or less) like 'of', 'in' from matching
    MIN_WORD_LENGTH_FOR_MATCHING = 3
    words = name.lower().split()
    if '(' in name:
        # Extract parenthetical content
        paren_match = re.search(r'\s*\(([^)]+)\)\s*$', name)
        if paren_match:
            paren_content = paren_match.group(1).lower()
            # Check if all words in parentheses are already in the card name
            paren_words = paren_content.split()
            if all(word in words for word in paren_words if len(word) > MIN_WORD_LENGTH_FOR_MATCHING):
                # Remove the redundant parenthetical
                name = re.sub(r'\s*\([^)]+\)\s*$', '', name)
    
    # Remove trailing punctuation that might be from email formatting
    name = re.sub(r'[,;:\.\-]+$', '', name)
    
    # Remove common suffixes that might appear in emails
    # (language, condition markers, etc.)
    name = re.sub(r'\s*[\(\[].*(EN|DE|FR|IT|ES|PT|JA).*[\)\]]', '', name, flags=re.IGNORECASE)
    name = re.sub(r'\s*[\(\[].*(NM|EX|GD|LP|PL|HP|DMG).*[\)\]]', '', name, flags=re.IGNORECASE)
    name = re.sub(r'\s*[\(\[].*(Near Mint|Excellent|Good|Light Played|Played|Heavily Played|Damaged).*[\)\]]', '', name, flags=re.IGNORECASE)
    
    return name.strip()


# ===========================================================================
# WP2a — lossless extraction (order header, address block, positions).
# This supersedes the lossy _clean_card_name pipeline for order ingestion:
# set / language / condition are KEPT (structured), never stripped away.
# ===========================================================================

# Amounts to pull from the order header, mapped to canonical keys.
_AMOUNT_LABELS = {
    "gesamtwert": "gesamtwert",
    "gebühren": "gebuehren",
    "gebuehren": "gebuehren",
    "auszahlungsbetrag": "auszahlungsbetrag",
    "versandkosten": "versandkosten",
    "gesamtbetrag": "gesamtbetrag",
}

# Anchors delimiting the address block (German primary, English fallback).
_ADDR_START_ANCHORS = [r"Status:\s*Bezahlt", r"Status:\s*Paid"]
_ADDR_END_ANCHORS = [r"Sendungsverfolgung\s*:", r"Shipment\s+tracking\s*:"]


def _parse_amount(text: str) -> Optional[float]:
    """Parse a monetary amount like '3,90' or '1.234,56' into a float."""
    if not text:
        return None
    t = text.strip().replace(" ", "")
    # German style: thousands '.', decimal ','
    if "," in t and "." in t:
        t = t.replace(".", "").replace(",", ".")
    elif "," in t:
        t = t.replace(",", ".")
    try:
        return float(t)
    except ValueError:
        return None


def parse_order_header(email_body: str) -> Dict:
    """Extract order number, buyer handle, status and the monetary amounts."""
    header: Dict = {"order_number": "", "buyer_handle": "", "status": "", "amounts": {}}

    m = re.search(r"Bestellnummer\s*:?\s*(\S+)", email_body, re.IGNORECASE) \
        or re.search(r"Order\s+number\s*:?\s*(\S+)", email_body, re.IGNORECASE)
    if m:
        header["order_number"] = m.group(1).strip()

    m = re.search(r"K[äa]ufer\s*:?\s*([^\n]+)", email_body, re.IGNORECASE) \
        or re.search(r"Buyer\s*:?\s*([^\n]+)", email_body, re.IGNORECASE)
    if m:
        header["buyer_handle"] = m.group(1).strip()

    m = re.search(r"Status\s*:?\s*([^\n]+)", email_body, re.IGNORECASE)
    if m:
        header["status"] = m.group(1).strip()

    for label, key in _AMOUNT_LABELS.items():
        m = re.search(
            rf"{label}\s*:?\s*([\d.,]+)\s*(?:EUR|€)", email_body, re.IGNORECASE
        )
        if m:
            header["amounts"][key] = _parse_amount(m.group(1))

    return header


def parse_address_block(email_body: str) -> Tuple[List[str], str]:
    """Return the address as (non-empty lines, raw block).

    Positional extraction: everything between the ``Status: Bezahlt`` and
    ``Sendungsverfolgung:`` anchors (English fallbacks allowed). The address is
    intentionally not hard-parsed into fields — the user confirms it later.
    """
    lines = email_body.splitlines()
    start_idx = end_idx = None

    for i, line in enumerate(lines):
        if start_idx is None and any(
            re.search(a, line, re.IGNORECASE) for a in _ADDR_START_ANCHORS
        ):
            start_idx = i
            continue
        if start_idx is not None and any(
            re.search(a, line, re.IGNORECASE) for a in _ADDR_END_ANCHORS
        ):
            end_idx = i
            break

    if start_idx is None or end_idx is None or end_idx <= start_idx + 1:
        return [], ""

    block = lines[start_idx + 1:end_idx]
    non_empty = [ln.strip() for ln in block if ln.strip()]
    raw = "\n".join(non_empty)
    return non_empty, raw


def parse_position_line(line: str) -> Optional[Dict]:
    """Parse a single Cardmarket position line into structured fields.

    Example: ``1x Rumble Arena (Avatar: The Last Airbender) - C - Englisch - NM 0,03 EUR``
    -> qty=1, name='Rumble Arena', set_name='Avatar: The Last Airbender',
       rarity='C', language='en', condition='NM', uncertain=False.

    With several parenthesis groups the set is the LAST one; earlier groups
    (e.g. ``(V.1)``) are treated as variants, not the set. A line containing an
    ellipsis / an unclosed set parenthesis is flagged ``uncertain`` so it goes to
    manual candidate selection instead of being auto-matched.
    """
    m = re.match(r"^\s*(\d+)\s*[xX×]\s*(.+)$", line)
    if not m:
        return None
    qty = int(m.group(1))
    rest = m.group(2).strip()
    raw = line.strip()

    # Strip a trailing price ("3,90 EUR" / "1,00 €") off the end.
    unit_price = None
    pm = re.search(r"\s+([\d.,]+)\s*(?:EUR|€)\s*$", rest)
    if pm:
        unit_price = _parse_amount(pm.group(1))
        rest = rest[: pm.start()].strip()

    uncertain = "..." in rest or "…" in rest

    name = rest
    set_name = None
    variant = None
    rarity = language = condition = None

    li = rest.rfind("(")
    if li != -1:
        before = rest[:li].strip()
        after = rest[li + 1:]
        ri = after.find(")")
        if ri == -1:
            # Unclosed set parenthesis -> truncated, uncertain.
            set_name = after.strip().rstrip(".").strip() or None
            suffix = ""
            uncertain = True
        else:
            set_name = after[:ri].strip() or None
            suffix = after[ri + 1:].strip()

        # A leading variant group like "(V.1)" stays with the name text.
        vm = re.search(r"\(([^)]*)\)\s*$", before)
        if vm:
            variant = vm.group(1).strip()
            before = before[: vm.start()].strip()
        name = before

        # Parse the "- C - Englisch - NM" suffix (all parts optional).
        if suffix:
            parts = [p.strip() for p in suffix.split("-") if p.strip()]
            if len(parts) >= 1:
                rarity = parts[0]
            if len(parts) >= 2:
                language = normalize_language(parts[1])
            if len(parts) >= 3:
                condition = parts[2]

    if set_name and ("..." in set_name or "…" in set_name):
        uncertain = True
    if name and ("..." in name or "…" in name):
        uncertain = True

    name = name.strip(" -")
    if not name or not re.search(r"[A-Za-z]", name):
        return None

    return {
        "quantity": qty,
        "name": name,
        "set_name": set_name,
        "variant": variant,
        "rarity": rarity,
        "language": language,
        "condition": condition,
        "unit_price": unit_price,
        "uncertain": uncertain,
        "raw": raw,
    }


def parse_positions(email_body: str) -> List[Dict]:
    """Extract all structured position lines from an order email."""
    items: List[Dict] = []
    for line in email_body.splitlines():
        if not re.match(r"^\s*\d+\s*[xX×]\s*\S", line):
            continue
        parsed = parse_position_line(line)
        if parsed:
            items.append(parsed)
    return items


# A Cardmarket buyer handle is a single token (letters/digits/_/-/.), not a phrase.
_BUYER_HANDLE_RE = re.compile(r"[\wäöüÄÖÜß.\-]{2,30}$")


def is_valid_buyer_handle(value: str) -> bool:
    """True if ``value`` looks like a real Cardmarket username (single token)."""
    v = (value or "").strip()
    return bool(v) and " " not in v and bool(_BUYER_HANDLE_RE.fullmatch(v))


def parse_order_email(
    email_body: str, message_id: str, subject: str = "", email_date: str = None
) -> Dict:
    """Full lossless extraction of a Cardmarket order email.

    Returns order number, buyer, status, amounts, the address block (lines + raw)
    and the structured positions. Nothing is guessed: truncated / ambiguous data
    is flagged for confirmation downstream.
    """
    header = parse_order_header(email_body)
    address_lines, address_raw = parse_address_block(email_body)
    items = parse_positions(email_body)

    # Buyer handle: the Cardmarket username is a single token. Prefer the clean
    # handle from the subject ("… für X: Bitte versenden"), then the "Käufer:"
    # header, then the body fallback — and only accept a plausible handle so a
    # sentence fragment (e.g. "die Bestellung stornieren.") is never used.
    subj_handle = ""
    if subject:
        sm = (re.search(r"für\s+([^:\n]+):\s*Bitte\s+versenden", subject, re.IGNORECASE)
              or re.search(r"for\s+([^:\n]+):\s*Please\s+ship", subject, re.IGNORECASE))
        if sm:
            subj_handle = sm.group(1).strip()
    body_buyer = parse_cardmarket_email(email_body, message_id, subject).get("buyer_name", "")
    buyer = next(
        (h for h in (subj_handle, header.get("buyer_handle", ""), body_buyer)
         if is_valid_buyer_handle(h)),
        "",
    )

    return {
        "order_number": header.get("order_number", ""),
        "buyer_name": buyer,
        "status": header.get("status", ""),
        "amounts": header.get("amounts", {}),
        "address_lines": address_lines,
        "address_raw": address_raw,
        "items": items,
        "message_id": message_id,
        "email_date": email_date,
    }


def extract_items_table(email_body: str) -> List[Dict]:
    """
    Try to extract items from a table-like structure in the email.
    
    This is a fallback parser for emails with tabular formats.
    
    Args:
        email_body: Email body text
        
    Returns:
        List of items with quantity and card_name
    """
    items = []
    
    # Look for table-like structures with pipes or tabs
    lines = email_body.split('\n')
    
    for line in lines:
        # Skip empty lines
        if not line.strip():
            continue
        
        # Try to parse table rows with separators
        if '|' in line or '\t' in line:
            parts = re.split(r'[\|\t]', line)
            parts = [p.strip() for p in parts if p.strip()]
            
            # Look for quantity and name in the parts
            for i, part in enumerate(parts):
                if re.match(r'^\d+$', part):
                    # Found a number, next part might be the card name
                    if i + 1 < len(parts):
                        try:
                            qty = int(part)
                            card_name = _clean_card_name(parts[i + 1])
                            if len(card_name) >= 3 and qty > 0:
                                items.append({
                                    'quantity': qty,
                                    'card_name': card_name
                                })
                        except ValueError:
                            continue
    
    return items
