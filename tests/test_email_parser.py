"""Tests for the Cardmarket email parser."""

try:
    from TCGInventory.email_parser import parse_cardmarket_email, _clean_card_name
except ImportError:
    # Fallback for running tests directly
    import sys
    import os
    sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(__file__))))
    from TCGInventory.email_parser import parse_cardmarket_email, _clean_card_name


def test_parse_buyer_from_subject():
    """Test parsing buyer name from email subject line."""
    email_body = """
    1x Lightning Bolt
    2x Counterspell
    """
    subject = "Bestellung 1250416803 für KohlkopfKlaus: Bitte versenden"
    
    result = parse_cardmarket_email(email_body, "msg123", subject=subject)
    
    assert result['buyer_name'] == 'KohlkopfKlaus'
    assert result['message_id'] == 'msg123'
    assert len(result['items']) == 2


def test_parse_buyer_from_body_pattern():
    """Test parsing buyer name from body when it starts with 'Name hat Bestellung'."""
    email_body = """
    KohlkopfKlaus hat Bestellung 1250416803 bezahlt.
    
    1x Airbending Lesson
    2x Waterbending Master
    """
    
    result = parse_cardmarket_email(email_body, "msg456", subject="", email_date="2024-01-15T10:30:00")
    
    assert result['buyer_name'] == 'KohlkopfKlaus'
    assert result['email_date'] == "2024-01-15T10:30:00"
    assert len(result['items']) == 2


def test_parse_basic_order():
    """Test parsing a basic order email with standard format."""
    email_body = """
    Käufer: John Smith
    
    Ihre Bestellung:
    1x Lightning Bolt
    2x Counterspell
    3x Island
    """
    
    result = parse_cardmarket_email(email_body, "msg123")
    
    assert result['buyer_name'] == 'John Smith'
    assert result['message_id'] == 'msg123'
    assert len(result['items']) == 3
    
    # Check items
    assert result['items'][0]['quantity'] == 1
    assert result['items'][0]['card_name'] == 'Lightning Bolt'
    assert result['items'][1]['quantity'] == 2
    assert result['items'][1]['card_name'] == 'Counterspell'
    assert result['items'][2]['quantity'] == 3
    assert result['items'][2]['card_name'] == 'Island'


def test_parse_with_variations():
    """Test parsing with whitespace and case variations."""
    email_body = """
    Buyer: Jane Doe
    
    Items:
    1X   Mountain Goat
    2 x Forest Spirit  
    3×  Plains Walker
    """
    
    result = parse_cardmarket_email(email_body, "msg456")
    
    assert result['buyer_name'] == 'Jane Doe'
    assert len(result['items']) == 3
    assert result['items'][0]['card_name'] == 'Mountain Goat'
    assert result['items'][1]['card_name'] == 'Forest Spirit'
    assert result['items'][2]['card_name'] == 'Plains Walker'


def test_parse_german_format():
    """Test parsing German format emails."""
    email_body = """
    Bestellung von: Max Mustermann
    
    1 Stück Airbending Lesson
    2 Stück Waterbending Master
    """
    
    result = parse_cardmarket_email(email_body, "msg789")
    
    assert result['buyer_name'] == 'Max Mustermann'
    assert len(result['items']) == 2
    assert result['items'][0]['quantity'] == 1
    assert result['items'][0]['card_name'] == 'Airbending Lesson'


def test_clean_card_name():
    """Test card name cleaning function."""
    assert _clean_card_name("  Lightning Bolt  ") == "Lightning Bolt"
    assert _clean_card_name("Card Name,") == "Card Name"
    assert _clean_card_name("Card (EN)") == "Card"
    assert _clean_card_name("Card [NM]") == "Card"
    assert _clean_card_name("Multiple   Spaces") == "Multiple Spaces"
    # Test price suffix removal
    assert _clean_card_name("Airbending Lesson (Magic: The Gathering | Avatar: The Last Airbe... 0,02 EUR)") == "Airbending Lesson"
    assert _clean_card_name("Card Name 1,50 EUR") == "Card Name"
    assert _clean_card_name("Card Name 0.99 EUR") == "Card Name"
    assert _clean_card_name("Another Card (Set Name | ... 2,00 EUR)") == "Another Card"


def test_parse_cardmarket_format_with_prices():
    """Test parsing Cardmarket email format with prices and set info."""
    email_body = """
    KohlkopfKlaus hat Bestellung 1250416803 bezahlt.
    
    1x Airbending Lesson (Magic: The Gathering | Avatar: The Last Airbe... 0,02 EUR)
    2x Waterbending Master (Magic: The Gathering | ... 1,50 EUR)
    1x Fire Nation Soldier 0,25 EUR
    """
    subject = "Bestellung 1250416803 für KohlkopfKlaus: Bitte versenden"
    
    result = parse_cardmarket_email(email_body, "msg789", subject=subject)
    
    assert result['buyer_name'] == 'KohlkopfKlaus'
    assert len(result['items']) == 3
    # Check that card names are cleaned
    assert result['items'][0]['card_name'] == 'Airbending Lesson'
    assert result['items'][1]['card_name'] == 'Waterbending Master'
    assert result['items'][2]['card_name'] == 'Fire Nation Soldier'


def test_parse_no_items():
    """Test parsing email with no valid items."""
    email_body = """
    Käufer: Test User
    
    Thank you for your order!
    """
    
    result = parse_cardmarket_email(email_body, "msg999")
    
    assert result['buyer_name'] == 'Test User'
    assert len(result['items']) == 0


def test_parse_fallback_buyer_name():
    """Test that a default buyer name is provided when none found."""
    email_body = """
    1x Card One
    2x Card Two
    """
    
    result = parse_cardmarket_email(email_body, "msg000")
    
    assert result['buyer_name'] == 'Unknown Buyer'
    assert len(result['items']) == 2


def test_skip_duplicates():
    """Test that duplicate card names are not added twice."""
    email_body = """
    Käufer: Test Buyer
    
    1x Lightning Bolt
    2x Lightning Bolt
    """
    
    result = parse_cardmarket_email(email_body, "msg111")
    
    # Should only have one entry (first one encountered)
    assert len(result['items']) == 1
    assert result['items'][0]['card_name'] == 'Lightning Bolt'


def test_blacklist_cardmarket_team():
    """Test that 'Das Cardmarket-Team' signature is not used as buyer name."""
    email_body = """
    Hallo Flemming,
    
    Ihre Bestellung ist bezahlt.
    
    1x Lightning Bolt
    2x Counterspell
    
    Vielen Dank!
    Das Cardmarket-Team
    """
    
    result = parse_cardmarket_email(email_body, "msg222", subject="")
    
    # Should fall back to 'Unknown Buyer' instead of picking up signature
    assert result['buyer_name'] == 'Unknown Buyer'
    assert len(result['items']) == 2


def test_extract_buyer_with_signature_present():
    """Test that buyer is correctly extracted even when signature is present."""
    email_body = """
    Hallo Flemming,
    
    KohlkopfKlaus hat Bestellung 1250416803 bezahlt.
    
    1x Lightning Bolt
    2x Counterspell
    
    Vielen Dank!
    Das Cardmarket-Team
    """
    
    result = parse_cardmarket_email(email_body, "msg333", subject="")
    
    # Should extract KohlkopfKlaus, not Das Cardmarket-Team
    assert result['buyer_name'] == 'KohlkopfKlaus'
    assert len(result['items']) == 2


def test_english_email_subject_format():
    """Test parsing English email with buyer in subject line."""
    email_body = """
    Hello Flemming,
    
    Obi83 has paid for shipment 1252132280.
    
    1x Sothera, the Supervoid (Edge of Eternities) - M - English - NM 3,98 EUR
    1x Lorwyn Eclipsed Play Booster Box (Lorwyn Eclipsed) - English 140,00 EUR
    """
    subject = "Shipment 1252132280 for Obi83: Please ship"
    
    result = parse_cardmarket_email(email_body, "msg444", subject=subject)
    
    # Should extract Obi83 from subject
    assert result['buyer_name'] == 'Obi83'
    assert len(result['items']) == 2
    # Check that card names are properly cleaned
    assert result['items'][0]['card_name'] == 'Sothera, the Supervoid'
    assert result['items'][1]['card_name'] == 'Lorwyn Eclipsed Play Booster Box'


def test_english_email_body_pattern():
    """Test parsing English email with buyer extracted from body."""
    email_body = """
    Hello Flemming,
    
    TestUser83 has paid for shipment 1234567890.
    
    Your order:
    1x Lightning Bolt - R - English - NM 5,00 EUR
    2x Counterspell - C - English - EX 2,50 EUR
    """
    
    result = parse_cardmarket_email(email_body, "msg555", subject="")
    
    # Should extract TestUser83 from body pattern
    assert result['buyer_name'] == 'TestUser83'
    assert len(result['items']) == 2
    assert result['items'][0]['card_name'] == 'Lightning Bolt'
    assert result['items'][1]['card_name'] == 'Counterspell'


def test_clean_card_name_english_format():
    """Test card name cleaning for English email format with conditions."""
    # Test English format with rarity, language, and condition
    assert _clean_card_name("Sothera, the Supervoid (Edge of Eternities) - M - English - NM 3,98 EUR") == "Sothera, the Supervoid"
    assert _clean_card_name("Lightning Bolt - R - English - NM 5,00 EUR") == "Lightning Bolt"
    assert _clean_card_name("Lorwyn Eclipsed Play Booster Box (Lorwyn Eclipsed) - English 140,00 EUR") == "Lorwyn Eclipsed Play Booster Box"
    assert _clean_card_name("Card Name - M - German - EX 1,00 EUR") == "Card Name"
    assert _clean_card_name("Test Card - C - French - GD 0,50 EUR") == "Test Card"


if __name__ == "__main__":
    test_parse_buyer_from_subject()
    test_parse_buyer_from_body_pattern()
    test_parse_basic_order()
    test_parse_with_variations()
    test_parse_german_format()
    test_clean_card_name()
    test_parse_cardmarket_format_with_prices()
    test_parse_no_items()
    test_parse_fallback_buyer_name()
    test_skip_duplicates()
    test_blacklist_cardmarket_team()
    test_extract_buyer_with_signature_present()
    test_english_email_subject_format()
    test_english_email_body_pattern()
    test_clean_card_name_english_format()
    print("All tests passed!")
