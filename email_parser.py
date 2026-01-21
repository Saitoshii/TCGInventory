"""Parser for Cardmarket 'Bitte versenden' emails."""

import re
from typing import Dict, List, Tuple

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
        r'^([A-Za-z][^\n]*?)\s*[xX×]\s*(\d+)\s*$',  # Reverse: Card Name x1 (must start line with letter, end line)
    ]
    
    found_items = []
    
    for pattern in item_patterns:
        matches = re.finditer(pattern, email_body, re.MULTILINE)
        for match in matches:
            if pattern == r'^([A-Za-z][^\n]*?)\s*[xX×]\s*(\d+)\s*$':
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
    
    # Remove English format suffixes: "- <rarity> - <language> - <condition> <price>"
    # Example: "- M - English - NM 3,98 EUR" or "- R - German - EX 1,00 EUR"
    name = re.sub(r'\s*-\s*[A-Z]+\s*-\s*(English|German|French|Italian|Spanish|Portuguese|Japanese|Chinese|Korean)\s*-\s*(NM|EX|GD|LP|PL|HP|DMG|M|Near Mint|Excellent|Good|Light Played|Played|Heavily Played|Damaged).*$', '', name, flags=re.IGNORECASE)
    
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
    words = name.lower().split()
    if '(' in name:
        # Extract parenthetical content
        paren_match = re.search(r'\s*\(([^)]+)\)\s*$', name)
        if paren_match:
            paren_content = paren_match.group(1).lower()
            # Check if all words in parentheses are already in the card name
            paren_words = paren_content.split()
            if all(word in words for word in paren_words if len(word) > 2):
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
