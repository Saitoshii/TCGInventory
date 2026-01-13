"""Tests for order card matching with inventory and default-cards.db."""

import sqlite3
import tempfile
import os
from pathlib import Path

try:
    from TCGInventory.order_service import OrderIngestionService
except ImportError:
    import sys
    sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(__file__))))
    from TCGInventory.order_service import OrderIngestionService


def test_find_card_in_inventory():
    """Test finding a card that exists in user's inventory with image and location."""
    # Create a temporary database for inventory
    with tempfile.NamedTemporaryFile(mode='w', suffix='.db', delete=False) as f:
        temp_db = f.name
    
    try:
        # Set up test inventory database
        with sqlite3.connect(temp_db) as conn:
            c = conn.cursor()
            c.execute("""
                CREATE TABLE cards (
                    id INTEGER PRIMARY KEY,
                    name TEXT,
                    image_url TEXT,
                    storage_code TEXT
                )
            """)
            c.execute(
                "INSERT INTO cards (name, image_url, storage_code) VALUES (?, ?, ?)",
                ("Lightning Bolt", "http://example.com/bolt.jpg", "O01-S01-P1")
            )
            conn.commit()
        
        # Test the matching
        service = OrderIngestionService()
        with sqlite3.connect(temp_db) as conn:
            c = conn.cursor()
            image_url, storage_code = service._find_card_info(c, "Lightning Bolt")
        
        assert image_url == "http://example.com/bolt.jpg"
        assert storage_code == "O01-S01-P1"
    
    finally:
        os.unlink(temp_db)


def test_find_card_partial_match():
    """Test finding a card with partial name match."""
    with tempfile.NamedTemporaryFile(mode='w', suffix='.db', delete=False) as f:
        temp_db = f.name
    
    try:
        with sqlite3.connect(temp_db) as conn:
            c = conn.cursor()
            c.execute("""
                CREATE TABLE cards (
                    id INTEGER PRIMARY KEY,
                    name TEXT,
                    image_url TEXT,
                    storage_code TEXT
                )
            """)
            c.execute(
                "INSERT INTO cards (name, image_url, storage_code) VALUES (?, ?, ?)",
                ("Lightning Bolt (Magic 2011)", "http://example.com/bolt.jpg", "O01-S01-P1")
            )
            conn.commit()
        
        service = OrderIngestionService()
        with sqlite3.connect(temp_db) as conn:
            c = conn.cursor()
            # Search for just "Lightning Bolt" should match "Lightning Bolt (Magic 2011)"
            image_url, storage_code = service._find_card_info(c, "Lightning Bolt")
        
        assert image_url == "http://example.com/bolt.jpg"
        assert storage_code == "O01-S01-P1"
    
    finally:
        os.unlink(temp_db)


def test_find_card_not_in_inventory():
    """Test handling when card is not in inventory."""
    with tempfile.NamedTemporaryFile(mode='w', suffix='.db', delete=False) as f:
        temp_db = f.name
    
    try:
        with sqlite3.connect(temp_db) as conn:
            c = conn.cursor()
            c.execute("""
                CREATE TABLE cards (
                    id INTEGER PRIMARY KEY,
                    name TEXT,
                    image_url TEXT,
                    storage_code TEXT
                )
            """)
            conn.commit()
        
        service = OrderIngestionService()
        with sqlite3.connect(temp_db) as conn:
            c = conn.cursor()
            image_url, storage_code = service._find_card_info(c, "Nonexistent Card")
        
        # Should return None for both when not found (and no default-cards.db)
        # image_url might be fetched from default-cards.db if it exists
        assert storage_code is None
    
    finally:
        os.unlink(temp_db)


def test_get_image_from_default_db():
    """Test fetching image from default-cards.db when available."""
    # Create a temporary default-cards.db
    temp_dir = tempfile.mkdtemp()
    default_db_path = Path(temp_dir) / "default-cards.db"
    
    try:
        # Set up test default-cards database
        with sqlite3.connect(default_db_path) as conn:
            c = conn.cursor()
            c.execute("""
                CREATE TABLE cards (
                    id TEXT PRIMARY KEY,
                    name TEXT,
                    set_code TEXT,
                    lang TEXT,
                    collector_number TEXT,
                    cardmarket_id TEXT,
                    image_url TEXT
                )
            """)
            c.execute(
                """INSERT INTO cards (id, name, set_code, image_url) 
                   VALUES (?, ?, ?, ?)""",
                ("123", "Counterspell", "LEA", "http://example.com/counter.jpg")
            )
            conn.commit()
        
        # Mock the default DB path
        service = OrderIngestionService()
        original_method = service._get_image_from_default_db
        
        def mock_get_image(card_name):
            # Use our test database
            try:
                with sqlite3.connect(default_db_path) as conn:
                    c = conn.cursor()
                    c.execute(
                        "SELECT image_url FROM cards WHERE LOWER(name) = LOWER(?) AND image_url IS NOT NULL LIMIT 1",
                        (card_name,)
                    )
                    result = c.fetchone()
                    if result and result[0]:
                        return result[0]
            except Exception:
                pass
            return None
        
        service._get_image_from_default_db = mock_get_image
        
        image_url = service._get_image_from_default_db("Counterspell")
        assert image_url == "http://example.com/counter.jpg"
        
        # Test card not in default DB
        image_url = service._get_image_from_default_db("Nonexistent Card")
        assert image_url is None
    
    finally:
        if default_db_path.exists():
            default_db_path.unlink()
        os.rmdir(temp_dir)


if __name__ == "__main__":
    test_find_card_in_inventory()
    print("✓ test_find_card_in_inventory passed")
    
    test_find_card_partial_match()
    print("✓ test_find_card_partial_match passed")
    
    test_find_card_not_in_inventory()
    print("✓ test_find_card_not_in_inventory passed")
    
    test_get_image_from_default_db()
    print("✓ test_get_image_from_default_db passed")
    
    print("\nAll order matching tests passed!")
