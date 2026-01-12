"""Tests for database schema migration and initialization."""

import os
import sys
import sqlite3
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(__file__))))

import TCGInventory  # noqa: E402
import TCGInventory.setup_db as setup_db  # noqa: E402
import TCGInventory.auth as auth  # noqa: E402


def test_initialize_database_creates_orders_tables():
    """Test that initialize_database creates orders and order_items tables."""
    # Use a temporary database file
    with tempfile.NamedTemporaryFile(delete=False, suffix='.db') as tmp:
        tmp_db = tmp.name
    
    try:
        # Override the DB_FILE temporarily in all modules
        original_db = TCGInventory.DB_FILE
        TCGInventory.DB_FILE = tmp_db
        setup_db.DB_FILE = tmp_db
        auth.DB_FILE = tmp_db
        
        # Initialize the database
        setup_db.initialize_database()
        
        # Verify tables exist
        with sqlite3.connect(tmp_db) as conn:
            cursor = conn.cursor()
            
            # Check for orders table
            cursor.execute(
                "SELECT name FROM sqlite_master WHERE type='table' AND name='orders'"
            )
            assert cursor.fetchone() is not None, "orders table should exist"
            
            # Check for order_items table
            cursor.execute(
                "SELECT name FROM sqlite_master WHERE type='table' AND name='order_items'"
            )
            assert cursor.fetchone() is not None, "order_items table should exist"
            
            # Verify orders table schema
            cursor.execute("PRAGMA table_info(orders)")
            orders_columns = {row[1] for row in cursor.fetchall()}
            expected_orders_cols = {'id', 'buyer_name', 'email_message_id', 'date_received', 'status', 'date_completed'}
            assert expected_orders_cols.issubset(orders_columns), f"orders table missing columns: {expected_orders_cols - orders_columns}"
            
            # Verify order_items table schema
            cursor.execute("PRAGMA table_info(order_items)")
            items_columns = {row[1] for row in cursor.fetchall()}
            expected_items_cols = {'id', 'order_id', 'card_name', 'quantity', 'image_url', 'storage_code'}
            assert expected_items_cols.issubset(items_columns), f"order_items table missing columns: {expected_items_cols - items_columns}"
    
    finally:
        # Restore original DB_FILE
        TCGInventory.DB_FILE = original_db
        setup_db.DB_FILE = original_db
        auth.DB_FILE = original_db
        # Clean up temp file
        if os.path.exists(tmp_db):
            os.unlink(tmp_db)


def test_initialize_database_is_idempotent():
    """Test that initialize_database can be called multiple times safely."""
    # Use a temporary database file
    with tempfile.NamedTemporaryFile(delete=False, suffix='.db') as tmp:
        tmp_db = tmp.name
    
    try:
        # Override the DB_FILE temporarily in all modules
        original_db = TCGInventory.DB_FILE
        TCGInventory.DB_FILE = tmp_db
        setup_db.DB_FILE = tmp_db
        auth.DB_FILE = tmp_db
        
        # Initialize the database twice
        setup_db.initialize_database()
        setup_db.initialize_database()
        
        # Verify tables still exist
        with sqlite3.connect(tmp_db) as conn:
            cursor = conn.cursor()
            
            cursor.execute(
                "SELECT name FROM sqlite_master WHERE type='table' AND name='orders'"
            )
            assert cursor.fetchone() is not None, "orders table should exist after second init"
            
            cursor.execute(
                "SELECT name FROM sqlite_master WHERE type='table' AND name='order_items'"
            )
            assert cursor.fetchone() is not None, "order_items table should exist after second init"
    
    finally:
        # Restore original DB_FILE
        TCGInventory.DB_FILE = original_db
        setup_db.DB_FILE = original_db
        auth.DB_FILE = original_db
        # Clean up temp file
        if os.path.exists(tmp_db):
            os.unlink(tmp_db)


def test_migration_preserves_existing_data():
    """Test that running initialize_database on existing DB preserves data."""
    # Use a temporary database file
    with tempfile.NamedTemporaryFile(delete=False, suffix='.db') as tmp:
        tmp_db = tmp.name
    
    try:
        # Override the DB_FILE temporarily in all modules
        original_db = TCGInventory.DB_FILE
        TCGInventory.DB_FILE = tmp_db
        setup_db.DB_FILE = tmp_db
        auth.DB_FILE = tmp_db
        
        # Create an old-style database with just the cards table
        with sqlite3.connect(tmp_db) as conn:
            cursor = conn.cursor()
            cursor.execute(
                """
                CREATE TABLE IF NOT EXISTS cards (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    name TEXT NOT NULL,
                    set_code TEXT NOT NULL,
                    language TEXT,
                    condition TEXT,
                    price REAL,
                    quantity INTEGER DEFAULT 1
                )
                """
            )
            # Insert test data
            cursor.execute(
                "INSERT INTO cards (name, set_code, language, condition, price, quantity) VALUES (?, ?, ?, ?, ?, ?)",
                ("Black Lotus", "LEA", "English", "Near Mint", 10000.0, 1)
            )
            conn.commit()
        
        # Run initialize_database (migration)
        setup_db.initialize_database()
        
        # Verify the old data is preserved
        with sqlite3.connect(tmp_db) as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT name, set_code, price FROM cards WHERE name = 'Black Lotus'")
            row = cursor.fetchone()
            assert row is not None, "Original card data should be preserved"
            assert row[0] == "Black Lotus"
            assert row[1] == "LEA"
            assert row[2] == 10000.0
            
            # Verify new tables were added
            cursor.execute(
                "SELECT name FROM sqlite_master WHERE type='table' AND name='orders'"
            )
            assert cursor.fetchone() is not None, "orders table should be added"
            
            cursor.execute(
                "SELECT name FROM sqlite_master WHERE type='table' AND name='order_items'"
            )
            assert cursor.fetchone() is not None, "order_items table should be added"
    
    finally:
        # Restore original DB_FILE
        TCGInventory.DB_FILE = original_db
        setup_db.DB_FILE = original_db
        auth.DB_FILE = original_db
        # Clean up temp file
        if os.path.exists(tmp_db):
            os.unlink(tmp_db)


if __name__ == "__main__":
    test_initialize_database_creates_orders_tables()
    print("✓ test_initialize_database_creates_orders_tables passed")
    
    test_initialize_database_is_idempotent()
    print("✓ test_initialize_database_is_idempotent passed")
    
    test_migration_preserves_existing_data()
    print("✓ test_migration_preserves_existing_data passed")
    
    print("\nAll tests passed!")
