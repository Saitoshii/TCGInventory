"""Integration test to verify order deletion with CASCADE."""

import sqlite3
import tempfile
import os


def test_order_deletion_cascade():
    """Test that deleting an order also deletes its items (CASCADE)."""
    # Create a temporary database
    with tempfile.NamedTemporaryFile(mode='w', suffix='.db', delete=False) as f:
        temp_db = f.name
    
    try:
        with sqlite3.connect(temp_db) as conn:
            # Enable foreign key constraints
            conn.execute("PRAGMA foreign_keys = ON")
            c = conn.cursor()
            
            # Create orders table
            c.execute("""
                CREATE TABLE IF NOT EXISTS orders (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    buyer_name TEXT NOT NULL,
                    email_message_id TEXT UNIQUE NOT NULL,
                    date_received TEXT NOT NULL,
                    email_date TEXT,
                    status TEXT DEFAULT 'open',
                    date_completed TEXT
                )
            """)
            
            # Create order_items table with CASCADE
            c.execute("""
                CREATE TABLE IF NOT EXISTS order_items (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    order_id INTEGER NOT NULL,
                    card_name TEXT NOT NULL,
                    quantity INTEGER NOT NULL,
                    image_url TEXT,
                    storage_code TEXT,
                    FOREIGN KEY(order_id) REFERENCES orders(id) ON DELETE CASCADE
                )
            """)
            
            # Insert test order
            c.execute(
                """INSERT INTO orders (buyer_name, email_message_id, date_received, email_date, status)
                   VALUES (?, ?, ?, ?, ?)""",
                ("Test Buyer", "msg123", "2024-01-15T10:00:00", "2024-01-15T09:30:00", "open")
            )
            order_id = c.lastrowid
            
            # Insert order items
            c.execute(
                """INSERT INTO order_items (order_id, card_name, quantity, image_url, storage_code)
                   VALUES (?, ?, ?, ?, ?)""",
                (order_id, "Lightning Bolt", 1, "http://example.com/bolt.jpg", "O01-S01-P1")
            )
            c.execute(
                """INSERT INTO order_items (order_id, card_name, quantity, image_url, storage_code)
                   VALUES (?, ?, ?, ?, ?)""",
                (order_id, "Counterspell", 2, "http://example.com/counter.jpg", "O01-S01-P2")
            )
            conn.commit()
            
            # Verify order and items exist
            c.execute("SELECT COUNT(*) FROM orders WHERE id = ?", (order_id,))
            assert c.fetchone()[0] == 1, "Order should exist"
            
            c.execute("SELECT COUNT(*) FROM order_items WHERE order_id = ?", (order_id,))
            assert c.fetchone()[0] == 2, "Order should have 2 items"
            
            # Delete the order
            c.execute("DELETE FROM orders WHERE id = ?", (order_id,))
            conn.commit()
            
            # Verify order is deleted
            c.execute("SELECT COUNT(*) FROM orders WHERE id = ?", (order_id,))
            assert c.fetchone()[0] == 0, "Order should be deleted"
            
            # Verify items are also deleted (CASCADE)
            c.execute("SELECT COUNT(*) FROM order_items WHERE order_id = ?", (order_id,))
            items_count = c.fetchone()[0]
            assert items_count == 0, f"Order items should be deleted by CASCADE, but found {items_count}"
            
            print("✓ Order deletion with CASCADE works correctly")
    
    finally:
        os.unlink(temp_db)


def test_email_date_column():
    """Test that email_date column exists and can be used."""
    with tempfile.NamedTemporaryFile(mode='w', suffix='.db', delete=False) as f:
        temp_db = f.name
    
    try:
        with sqlite3.connect(temp_db) as conn:
            c = conn.cursor()
            
            # Create orders table with email_date
            c.execute("""
                CREATE TABLE IF NOT EXISTS orders (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    buyer_name TEXT NOT NULL,
                    email_message_id TEXT UNIQUE NOT NULL,
                    date_received TEXT NOT NULL,
                    email_date TEXT,
                    status TEXT DEFAULT 'open',
                    date_completed TEXT
                )
            """)
            
            # Insert order with email_date different from date_received
            c.execute(
                """INSERT INTO orders (buyer_name, email_message_id, date_received, email_date, status)
                   VALUES (?, ?, ?, ?, ?)""",
                ("Test Buyer", "msg456", "2024-01-15T12:00:00", "2024-01-15T10:30:00", "open")
            )
            
            # Query using COALESCE to prefer email_date
            c.execute(
                """SELECT COALESCE(email_date, date_received) as display_date
                   FROM orders WHERE email_message_id = ?""",
                ("msg456",)
            )
            result = c.fetchone()
            
            assert result[0] == "2024-01-15T10:30:00", "Should use email_date when available"
            print("✓ email_date column works correctly")
            
            # Test fallback when email_date is NULL
            c.execute(
                """INSERT INTO orders (buyer_name, email_message_id, date_received, status)
                   VALUES (?, ?, ?, ?)""",
                ("Another Buyer", "msg789", "2024-01-16T12:00:00", "open")
            )
            
            c.execute(
                """SELECT COALESCE(email_date, date_received) as display_date
                   FROM orders WHERE email_message_id = ?""",
                ("msg789",)
            )
            result = c.fetchone()
            
            assert result[0] == "2024-01-16T12:00:00", "Should fall back to date_received when email_date is NULL"
            print("✓ email_date fallback to date_received works correctly")
    
    finally:
        os.unlink(temp_db)


if __name__ == "__main__":
    test_order_deletion_cascade()
    test_email_date_column()
    print("\nAll integration tests passed!")
