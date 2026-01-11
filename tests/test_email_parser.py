"""Tests for the Cardmarket email parser."""

import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(__file__))))

from TCGInventory.email_parser import parse_cardmarket_email, _clean_card_name


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


if __name__ == "__main__":
    test_parse_basic_order()
    test_parse_with_variations()
    test_parse_german_format()
    test_clean_card_name()
    test_parse_no_items()
    test_parse_fallback_buyer_name()
    test_skip_duplicates()
    print("All tests passed!")
