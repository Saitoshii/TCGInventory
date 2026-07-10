"""Order ingestion service for processing Cardmarket emails."""

import sqlite3
import threading
import time
from datetime import datetime, time as dt_time
from pathlib import Path
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
from TCGInventory.email_parser import parse_cardmarket_email, parse_order_email
from TCGInventory.card_scanner import resolve_set_code

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
                
                # Parse the email with subject and date (lossless WP2a extraction)
                parsed = parse_order_email(email_body, message_id, subject=email_subject, email_date=email_date)

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

                # Idempotency: never insert the same message/order twice.
                cursor.execute(
                    "SELECT id FROM orders WHERE email_message_id = ?",
                    (parsed_order['message_id'],)
                )
                if cursor.fetchone():
                    print(f"Order {parsed_order['message_id']} already exists")
                    return False

                amounts = parsed_order.get('amounts', {})
                email_date = parsed_order.get('email_date') or datetime.now().isoformat()
                cursor.execute(
                    """
                    INSERT INTO orders (buyer_name, email_message_id, date_received, email_date,
                                        status, order_number, address, address_raw,
                                        amount_gesamtwert, amount_gebuehren, amount_auszahlung,
                                        amount_versand, amount_gesamt)
                    VALUES (?, ?, ?, ?, 'open', ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        parsed_order['buyer_name'],
                        parsed_order['message_id'],
                        datetime.now().isoformat(),
                        email_date,
                        parsed_order.get('order_number', ''),
                        parsed_order.get('address_raw', ''),
                        parsed_order.get('address_raw', ''),
                        amounts.get('gesamtwert'),
                        amounts.get('gebuehren'),
                        amounts.get('auszahlungsbetrag'),
                        amounts.get('versandkosten'),
                        amounts.get('gesamtbetrag'),
                    )
                )

                order_id = cursor.lastrowid

                for item in parsed_order['items']:
                    match = self._match_item(cursor, item)
                    cursor.execute(
                        """
                        INSERT INTO order_items
                            (order_id, card_name, quantity, image_url, storage_code,
                             card_id, match_status, set_name, set_code, language,
                             condition, foil, uncertain, unit_price, variant)
                        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                        """,
                        (
                            order_id,
                            item['name'],
                            item['quantity'],
                            match['image_url'],
                            match['storage_code'],
                            match['card_id'],
                            match['match_status'],
                            item.get('set_name'),
                            match['set_code'],
                            item.get('language'),
                            item.get('condition'),
                            1 if item.get('foil') else 0,
                            1 if item.get('uncertain') else 0,
                            item.get('unit_price'),
                            item.get('variant'),
                        )
                    )

                conn.commit()
                return True

        except sqlite3.Error as e:
            print(f"Database error saving order: {e}")
            return False

    def _match_item(self, cursor, item):
        """Match a parsed position against inventory by identity (WP1b/WP2a).

        Matches on ``name + set_code + language`` (foil if known). Exactly one
        available match -> ``matched`` with the card's storage/image. Zero,
        several, or an ``uncertain`` line -> ``ambiguous``/``unresolved`` with a
        fallback image only; the panel then offers manual candidate selection.
        No silent ``LIMIT 1``, no ``LIKE`` substring auto-match.

        Returns dict: card_id, match_status, storage_code, image_url, set_code.
        """
        name = item['name']
        set_code, confidence = resolve_set_code(item.get('set_name'))
        language = item.get('language')
        uncertain = bool(item.get('uncertain'))

        result = {
            "card_id": None,
            "match_status": "unresolved",
            "storage_code": None,
            "image_url": None,
            "set_code": set_code,
        }

        # Only auto-match when the line is clean AND the set resolved confidently.
        if not uncertain and set_code and confidence == "high":
            query = (
                "SELECT id, storage_code, image_url FROM cards "
                "WHERE LOWER(name) = LOWER(?) AND LOWER(set_code) = LOWER(?) "
            )
            params = [name, set_code]
            if language:
                query += "AND LOWER(language) = LOWER(?) "
                params.append(language)
            query += "AND status = 'verfügbar' AND quantity > 0"
            cursor.execute(query, params)
            rows = cursor.fetchall()

            if len(rows) == 1:
                result.update(
                    card_id=rows[0][0], match_status="matched",
                    storage_code=rows[0][1],
                    image_url=rows[0][2] or self._get_image_from_default_db(name),
                )
                return result
            if len(rows) > 1:
                result["match_status"] = "ambiguous"
            else:
                result["match_status"] = "unresolved"
        else:
            result["match_status"] = "ambiguous" if uncertain else "unresolved"

        # Fallback image for display only (does not imply a match).
        result["image_url"] = self._get_image_from_default_db(name)
        return result
    
    def _get_image_from_default_db(self, card_name):
        """
        Look up image_url from default-cards.db.
        
        Args:
            card_name: Name of the card to search for
            
        Returns:
            image_url or None
        """
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
