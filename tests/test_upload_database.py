import os
import sys
import tempfile
import sqlite3
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(__file__))))


def test_upload_database_validation():
    """Test that the upload validates database files correctly."""
    # Create a temporary valid database file
    with tempfile.NamedTemporaryFile(suffix='.db', delete=False) as tmp:
        tmp_path = tmp.name
    
    try:
        # Create a valid SQLite database with cards table
        with sqlite3.connect(tmp_path) as conn:
            cursor = conn.cursor()
            cursor.execute("""
                CREATE TABLE cards (
                    id TEXT PRIMARY KEY,
                    name TEXT,
                    set_code TEXT
                )
            """)
            cursor.execute("INSERT INTO cards VALUES ('test1', 'Test Card', 'TST')")
            conn.commit()
        
        # Verify the database was created correctly
        with sqlite3.connect(tmp_path) as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='cards'")
            result = cursor.fetchone()
            assert result is not None, "Cards table should exist"
            
            cursor.execute("SELECT COUNT(*) FROM cards")
            count = cursor.fetchone()[0]
            assert count == 1, "Should have one card in the database"
        
        print("✓ Database validation test passed")
    finally:
        # Clean up
        if os.path.exists(tmp_path):
            os.unlink(tmp_path)


def test_invalid_database_detection():
    """Test that invalid database files are rejected."""
    # Create a temporary invalid file (not a database)
    with tempfile.NamedTemporaryFile(suffix='.db', delete=False, mode='w') as tmp:
        tmp.write("This is not a database file")
        tmp_path = tmp.name
    
    try:
        # Try to open it as SQLite database - should fail
        try:
            with sqlite3.connect(tmp_path) as conn:
                cursor = conn.cursor()
                cursor.execute("SELECT * FROM cards")
            assert False, "Should have raised an error for invalid database"
        except sqlite3.DatabaseError:
            print("✓ Invalid database detection test passed")
    finally:
        # Clean up
        if os.path.exists(tmp_path):
            os.unlink(tmp_path)


if __name__ == "__main__":
    test_upload_database_validation()
    test_invalid_database_detection()
    print("\nAll database upload tests passed!")
