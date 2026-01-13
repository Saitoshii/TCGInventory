"""Order ingestion service for processing Cardmarket emails."""

import sqlite3
import threading
import time
from datetime import datetime, time as dt_time
from typing import Set

from TCGInventory import DB_FILE
from TCGInventory.gmail_auth import (
    get_gmail_service,
    fetch_cardmarket_emails,
    mark_message_processed,
    get_email_body,
    get_email_subject,
    get_email_date
)
from TCGInventory.email_parser import parse_cardmarket_email

# Constants
SECONDS_PER_MINUTE = 60


class OrderIngestionService:
    """Background service to poll Gmail for new Cardmarket orders."""
    
    def __init__(self, poll_interval_minutes=10):
        """
        Initialize the order ingestion service.
        
        Args:
            poll_interval_minutes: How often to check for new emails (default: 10)
        """
        self.poll_interval = poll_interval_minutes * SECONDS_PER_MINUTE
        self.running = False
        self.thread = None
        self.processed_message_ids: Set[str] = set()
        self._enabled = True
        
    def is_within_operating_hours(self):
        """Check if current time is within 11:00-22:00."""
        now = datetime.now().time()
        start_time = dt_time(11, 0)
        end_time = dt_time(22, 0)
        return start_time <= now <= end_time
    
    def enable(self):
        """Enable the polling service."""
        self._enabled = True
        
    def disable(self):
        """Disable the polling service."""
        self._enabled = False
        
    def is_enabled(self):
        """Check if polling is enabled."""
        return self._enabled
    
    def start(self):
        """Start the background polling thread."""
        if self.running:
            print("Order ingestion service already running")
            return
        
        self.running = True
        self.thread = threading.Thread(target=self._poll_loop, daemon=True)
        self.thread.start()
        print("Order ingestion service started")
    
    def stop(self):
        """Stop the background polling thread."""
        self.running = False
        if self.thread:
            self.thread.join(timeout=5)
        print("Order ingestion service stopped")
    
    def _poll_loop(self):
        """Main polling loop that runs in the background."""
        # Do an initial sync on startup
        self.sync_orders()
        
        while self.running:
            try:
                # Only poll during operating hours and if enabled
                if self.is_within_operating_hours() and self._enabled:
                    self.sync_orders()
                
                # Sleep for the poll interval
                time.sleep(self.poll_interval)
                
            except Exception as e:
                print(f"Error in order ingestion loop: {e}")
                time.sleep(SECONDS_PER_MINUTE)  # Wait a minute before retrying on error
    
    def sync_orders(self):
        """
        Manually trigger a sync to fetch and process new orders.
        
        Returns:
            Tuple of (success: bool, message: str, new_orders_count: int)
        """
        try:
            # Get Gmail service
            service = get_gmail_service()
            if not service:
                return False, "Gmail authentication failed. Check credentials.", 0
            
            # Fetch unprocessed messages
            messages = fetch_cardmarket_emails(service, self.processed_message_ids)
            
            if not messages:
                return True, "No new orders found", 0
            
            new_orders_count = 0
            
            # Process each message
            for message in messages:
                message_id = message['id']
                
                # Extract email body, subject, and date
                email_body = get_email_body(message)
                email_subject = get_email_subject(message)
                email_date = get_email_date(message)
                
                if not email_body:
                    print(f"Could not extract body from message {message_id}")
                    continue
                
                # Parse the email with subject and date
                parsed = parse_cardmarket_email(email_body, message_id, subject=email_subject, email_date=email_date)
                
                if not parsed['items']:
                    print(f"No items found in message {message_id}")
                    # Still mark as processed to avoid reprocessing
                    mark_message_processed(service, message_id)
                    self.processed_message_ids.add(message_id)
                    continue
                
                # Save order to database
                success = self._save_order(parsed)
                
                if success:
                    # Mark email as processed
                    mark_message_processed(service, message_id)
                    self.processed_message_ids.add(message_id)
                    new_orders_count += 1
                    print(f"Processed order from {parsed['buyer_name']} with {len(parsed['items'])} items")
            
            if new_orders_count > 0:
                return True, f"Successfully imported {new_orders_count} new order(s)", new_orders_count
            else:
                return True, "No new orders to import", 0
                
        except Exception as e:
            print(f"Error syncing orders: {e}")
            return False, f"Error syncing orders: {str(e)}", 0
    
    def _save_order(self, parsed_order):
        """
        Save a parsed order to the database.
        
        Args:
            parsed_order: Dictionary with buyer_name, items, and message_id
            
        Returns:
            True if successful, False otherwise
        """
        try:
            with sqlite3.connect(DB_FILE) as conn:
                cursor = conn.cursor()
                
                # Check if this message was already processed
                cursor.execute(
                    "SELECT id FROM orders WHERE email_message_id = ?",
                    (parsed_order['message_id'],)
                )
                if cursor.fetchone():
                    print(f"Order {parsed_order['message_id']} already exists")
                    return False
                
                # Insert order with email_date
                email_date = parsed_order.get('email_date') or datetime.now().isoformat()
                cursor.execute(
                    """
                    INSERT INTO orders (buyer_name, email_message_id, date_received, email_date, status)
                    VALUES (?, ?, ?, ?, 'open')
                    """,
                    (
                        parsed_order['buyer_name'],
                        parsed_order['message_id'],
                        datetime.now().isoformat(),
                        email_date
                    )
                )
                
                order_id = cursor.lastrowid
                
                # Insert order items
                for item in parsed_order['items']:
                    # Try to find matching card in inventory for image and location
                    image_url, storage_code = self._find_card_info(
                        cursor, item['card_name']
                    )
                    
                    cursor.execute(
                        """
                        INSERT INTO order_items (order_id, card_name, quantity, image_url, storage_code)
                        VALUES (?, ?, ?, ?, ?)
                        """,
                        (
                            order_id,
                            item['card_name'],
                            item['quantity'],
                            image_url,
                            storage_code
                        )
                    )
                
                conn.commit()
                return True
                
        except sqlite3.Error as e:
            print(f"Database error saving order: {e}")
            return False
    
    def _find_card_info(self, cursor, card_name):
        """
        Find image URL and storage location for a card.
        
        First checks the user's inventory for both image and storage location.
        If no image is found, falls back to default-cards.db for image_url.
        
        Args:
            cursor: Database cursor (for user's inventory)
            card_name: Name of the card to search for
            
        Returns:
            Tuple of (image_url, storage_code)
        """
        # Search for exact match in user's inventory first
        cursor.execute(
            "SELECT image_url, storage_code FROM cards WHERE LOWER(name) = LOWER(?) LIMIT 1",
            (card_name,)
        )
        result = cursor.fetchone()
        
        if result:
            image_url, storage_code = result[0], result[1]
            # If we have an image from inventory, use it
            if image_url:
                return image_url, storage_code
            # Otherwise, try to get image from default-cards.db but keep storage_code
            fallback_image = self._get_image_from_default_db(card_name)
            return fallback_image, storage_code
        
        # Try partial match in inventory
        cursor.execute(
            "SELECT image_url, storage_code FROM cards WHERE LOWER(name) LIKE LOWER(?) LIMIT 1",
            (f"%{card_name}%",)
        )
        result = cursor.fetchone()
        
        if result:
            image_url, storage_code = result[0], result[1]
            if image_url:
                return image_url, storage_code
            fallback_image = self._get_image_from_default_db(card_name)
            return fallback_image, storage_code
        
        # Not in inventory - try to get image from default-cards.db
        image_url = self._get_image_from_default_db(card_name)
        return image_url, None
    
    def _get_image_from_default_db(self, card_name):
        """
        Look up image_url from default-cards.db.
        
        Args:
            card_name: Name of the card to search for
            
        Returns:
            image_url or None
        """
        from pathlib import Path
        default_db_path = Path(__file__).resolve().parent / "data" / "default-cards.db"
        
        if not default_db_path.exists():
            return None
        
        try:
            with sqlite3.connect(default_db_path) as conn:
                c = conn.cursor()
                
                # Try exact match first
                c.execute(
                    "SELECT image_url FROM cards WHERE LOWER(name) = LOWER(?) AND image_url IS NOT NULL LIMIT 1",
                    (card_name,)
                )
                result = c.fetchone()
                if result and result[0]:
                    return result[0]
                
                # Try partial match
                c.execute(
                    "SELECT image_url FROM cards WHERE LOWER(name) LIKE LOWER(?) AND image_url IS NOT NULL LIMIT 1",
                    (f"%{card_name}%",)
                )
                result = c.fetchone()
                if result and result[0]:
                    return result[0]
                
        except sqlite3.Error as e:
            print(f"Error querying default-cards.db: {e}")
        
        return None


# Global service instance
_service_instance = None


def get_order_service():
    """Get or create the global order ingestion service instance."""
    global _service_instance
    if _service_instance is None:
        _service_instance = OrderIngestionService()
    return _service_instance
