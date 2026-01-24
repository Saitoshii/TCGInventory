import os
import sys
import sqlite3
import types

# Stub heavy dependencies
sys.modules.setdefault('cv2', types.SimpleNamespace())
pyzbar = types.ModuleType('pyzbar')
pyzbar.pyzbar = types.SimpleNamespace(decode=lambda *a, **k: [])
sys.modules.setdefault('pyzbar', pyzbar)
sys.modules.setdefault('pyzbar.pyzbar', pyzbar.pyzbar)

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(__file__))))

from TCGInventory import web
from TCGInventory.setup_db import initialize_database
from TCGInventory.lager_manager import add_card


def test_mark_order_sold_removes_from_inventory(tmp_path, monkeypatch):
    """Test that marking an order as sold removes cards from inventory."""
    db = tmp_path / "db.sqlite"
    monkeypatch.setattr(web, "DB_FILE", str(db))
    monkeypatch.setattr(sys.modules['TCGInventory'], 'DB_FILE', str(db))
    monkeypatch.setattr(sys.modules['TCGInventory.setup_db'], 'DB_FILE', str(db))
    monkeypatch.setattr(sys.modules['TCGInventory.lager_manager'], 'DB_FILE', str(db))
    initialize_database()

    # Add some cards to inventory
    add_card("Lightning Bolt", "LEA", "en", "NM", 10.0, quantity=5)
    add_card("Black Lotus", "LEA", "en", "NM", 1000.0, quantity=2)
    
    # Create an order
    with sqlite3.connect(str(db)) as conn:
        c = conn.cursor()
        c.execute(
            """
            INSERT INTO orders (buyer_name, email_message_id, date_received, status)
            VALUES (?, ?, ?, ?)
            """,
            ("Test Buyer", "test-msg-123", "2024-01-01T10:00:00", "open")
        )
        order_id = c.lastrowid
        
        # Add order items
        c.execute(
            """
            INSERT INTO order_items (order_id, card_name, quantity, image_url, storage_code)
            VALUES (?, ?, ?, ?, ?)
            """,
            (order_id, "Lightning Bolt", 2, None, None)
        )
        c.execute(
            """
            INSERT INTO order_items (order_id, card_name, quantity, image_url, storage_code)
            VALUES (?, ?, ?, ?, ?)
            """,
            (order_id, "Black Lotus", 1, None, None)
        )
        conn.commit()

    # Mark order as sold via web route
    app = web.app
    app.config['TESTING'] = True
    with app.test_client() as client:
        with client.session_transaction() as sess:
            sess['user'] = 'test'
        resp = client.post(f"/orders/{order_id}/mark_sold")
        assert resp.status_code == 302  # Redirect

    # Verify inventory was updated
    with sqlite3.connect(str(db)) as conn:
        c = conn.cursor()
        
        # Lightning Bolt should have 3 remaining (5 - 2)
        c.execute("SELECT quantity FROM cards WHERE name = 'Lightning Bolt'")
        bolt_qty = c.fetchone()[0]
        assert bolt_qty == 3, f"Expected Lightning Bolt quantity to be 3, got {bolt_qty}"
        
        # Black Lotus should have 1 remaining (2 - 1)
        c.execute("SELECT quantity FROM cards WHERE name = 'Black Lotus'")
        lotus_qty = c.fetchone()[0]
        assert lotus_qty == 1, f"Expected Black Lotus quantity to be 1, got {lotus_qty}"
        
        # Order should be marked as sold
        c.execute("SELECT status FROM orders WHERE id = ?", (order_id,))
        status = c.fetchone()[0]
        assert status == "sold", f"Expected order status to be 'sold', got {status}"


def test_mark_order_sold_with_missing_cards(tmp_path, monkeypatch):
    """Test that marking an order as sold handles missing cards gracefully."""
    db = tmp_path / "db.sqlite"
    monkeypatch.setattr(web, "DB_FILE", str(db))
    monkeypatch.setattr(sys.modules['TCGInventory'], 'DB_FILE', str(db))
    monkeypatch.setattr(sys.modules['TCGInventory.setup_db'], 'DB_FILE', str(db))
    monkeypatch.setattr(sys.modules['TCGInventory.lager_manager'], 'DB_FILE', str(db))
    initialize_database()

    # Add only one card to inventory
    add_card("Lightning Bolt", "LEA", "en", "NM", 10.0, quantity=1)
    
    # Create an order with a card not in inventory
    with sqlite3.connect(str(db)) as conn:
        c = conn.cursor()
        c.execute(
            """
            INSERT INTO orders (buyer_name, email_message_id, date_received, status)
            VALUES (?, ?, ?, ?)
            """,
            ("Test Buyer", "test-msg-456", "2024-01-01T10:00:00", "open")
        )
        order_id = c.lastrowid
        
        # Add order items
        c.execute(
            """
            INSERT INTO order_items (order_id, card_name, quantity, image_url, storage_code)
            VALUES (?, ?, ?, ?, ?)
            """,
            (order_id, "Lightning Bolt", 1, None, None)
        )
        c.execute(
            """
            INSERT INTO order_items (order_id, card_name, quantity, image_url, storage_code)
            VALUES (?, ?, ?, ?, ?)
            """,
            (order_id, "Mox Ruby", 1, None, None)
        )
        conn.commit()

    # Mark order as sold via web route
    app = web.app
    app.config['TESTING'] = True
    with app.test_client() as client:
        with client.session_transaction() as sess:
            sess['user'] = 'test'
        resp = client.post(f"/orders/{order_id}/mark_sold")
        assert resp.status_code == 302  # Redirect

    # Verify inventory was updated
    with sqlite3.connect(str(db)) as conn:
        c = conn.cursor()
        
        # Lightning Bolt should be archived (quantity 0)
        c.execute("SELECT quantity, status FROM cards WHERE name = 'Lightning Bolt'")
        result = c.fetchone()
        assert result[0] == 0, f"Expected Lightning Bolt quantity to be 0, got {result[0]}"
        assert result[1] == "archiviert", f"Expected Lightning Bolt to be archived"
        
        # Mox Ruby was never in inventory, so no card should exist
        c.execute("SELECT COUNT(*) FROM cards WHERE name = 'Mox Ruby'")
        count = c.fetchone()[0]
        assert count == 0, f"Expected no Mox Ruby in inventory"
        
        # Order should still be marked as sold
        c.execute("SELECT status FROM orders WHERE id = ?", (order_id,))
        status = c.fetchone()[0]
        assert status == "sold", f"Expected order status to be 'sold', got {status}"


def test_mark_order_sold_case_insensitive(tmp_path, monkeypatch):
    """Test that card name matching is case-insensitive."""
    db = tmp_path / "db.sqlite"
    monkeypatch.setattr(web, "DB_FILE", str(db))
    monkeypatch.setattr(sys.modules['TCGInventory'], 'DB_FILE', str(db))
    monkeypatch.setattr(sys.modules['TCGInventory.setup_db'], 'DB_FILE', str(db))
    monkeypatch.setattr(sys.modules['TCGInventory.lager_manager'], 'DB_FILE', str(db))
    initialize_database()

    # Add card with specific casing
    add_card("Lightning Bolt", "LEA", "en", "NM", 10.0, quantity=3)
    
    # Create an order with different casing
    with sqlite3.connect(str(db)) as conn:
        c = conn.cursor()
        c.execute(
            """
            INSERT INTO orders (buyer_name, email_message_id, date_received, status)
            VALUES (?, ?, ?, ?)
            """,
            ("Test Buyer", "test-msg-789", "2024-01-01T10:00:00", "open")
        )
        order_id = c.lastrowid
        
        # Add order item with different casing
        c.execute(
            """
            INSERT INTO order_items (order_id, card_name, quantity, image_url, storage_code)
            VALUES (?, ?, ?, ?, ?)
            """,
            (order_id, "LIGHTNING BOLT", 2, None, None)
        )
        conn.commit()

    # Mark order as sold via web route
    app = web.app
    app.config['TESTING'] = True
    with app.test_client() as client:
        with client.session_transaction() as sess:
            sess['user'] = 'test'
        resp = client.post(f"/orders/{order_id}/mark_sold")
        assert resp.status_code == 302  # Redirect

    # Verify inventory was updated despite casing difference
    with sqlite3.connect(str(db)) as conn:
        c = conn.cursor()
        c.execute("SELECT quantity FROM cards WHERE name = 'Lightning Bolt'")
        qty = c.fetchone()[0]
        assert qty == 1, f"Expected Lightning Bolt quantity to be 1, got {qty}"
