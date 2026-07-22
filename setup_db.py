import sqlite3

from . import DB_FILE
from .auth import init_user_db


def initialize_database() -> None:
    """Create the SQLite database and all required tables."""
    with sqlite3.connect(DB_FILE) as conn:
        cursor = conn.cursor()
        init_user_db()

        # Tabelle 1: Karten
        cursor.execute(
            """
            CREATE TABLE IF NOT EXISTS cards (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT NOT NULL,
                set_code TEXT NOT NULL,
                language TEXT,
                condition TEXT,
                price REAL,
                quantity INTEGER DEFAULT 1,
                storage_code TEXT,
                cardmarket_id TEXT,
                folder_id INTEGER,
                status TEXT DEFAULT 'verfügbar',
                date_added TEXT,
                collector_number TEXT,
                scryfall_id TEXT,
                image_url TEXT,
                foil INTEGER DEFAULT 0
            )
            """
        )

        # create folders table and column if missing
        cursor.execute(
            """
            CREATE TABLE IF NOT EXISTS folders (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT UNIQUE,
                pages INTEGER DEFAULT 0
            )
            """
        )

        cursor.execute("PRAGMA table_info(folders)")
        folder_columns = [row[1] for row in cursor.fetchall()]
        if "pages" not in folder_columns:
            cursor.execute("ALTER TABLE folders ADD COLUMN pages INTEGER DEFAULT 0")

        cursor.execute("PRAGMA table_info(cards)")
        columns = [row[1] for row in cursor.fetchall()]
        if "folder_id" not in columns:
            cursor.execute("ALTER TABLE cards ADD COLUMN folder_id INTEGER")
        if "collector_number" not in columns:
            cursor.execute("ALTER TABLE cards ADD COLUMN collector_number TEXT")
        if "scryfall_id" not in columns:
            cursor.execute("ALTER TABLE cards ADD COLUMN scryfall_id TEXT")
        if "image_url" not in columns:
            cursor.execute("ALTER TABLE cards ADD COLUMN image_url TEXT")
        if "quantity" not in columns:
            cursor.execute("ALTER TABLE cards ADD COLUMN quantity INTEGER DEFAULT 1")
        if "foil" not in columns:
            cursor.execute("ALTER TABLE cards ADD COLUMN foil INTEGER DEFAULT 0")
        # New fields for improved data model
        if "item_type" not in columns:
            cursor.execute("ALTER TABLE cards ADD COLUMN item_type TEXT DEFAULT 'card'")
        if "reserved_until" not in columns:
            cursor.execute("ALTER TABLE cards ADD COLUMN reserved_until TEXT")
        if "location_hint" not in columns:
            cursor.execute("ALTER TABLE cards ADD COLUMN location_hint TEXT")
        # Identity-path columns (safety net for very old databases). These are
        # part of the CREATE TABLE above for fresh installs; the guards below
        # add them non-destructively as nullable columns if an older schema is
        # missing them. See CLAUDE.md for the canonical identity path.
        if "set_code" not in columns:
            cursor.execute("ALTER TABLE cards ADD COLUMN set_code TEXT")
        if "language" not in columns:
            cursor.execute("ALTER TABLE cards ADD COLUMN language TEXT")
        if "cardmarket_id" not in columns:
            cursor.execute("ALTER TABLE cards ADD COLUMN cardmarket_id TEXT")
        if "storage_code" not in columns:
            cursor.execute("ALTER TABLE cards ADD COLUMN storage_code TEXT")

        # Indexes for the identity path and common lookups. CREATE INDEX
        # IF NOT EXISTS keeps this idempotent and non-destructive. All columns
        # referenced here are guaranteed to exist by the migration above.
        cursor.execute(
            "CREATE INDEX IF NOT EXISTS idx_cards_cardmarket_id ON cards(cardmarket_id)"
        )
        cursor.execute(
            "CREATE INDEX IF NOT EXISTS idx_cards_scryfall_id ON cards(scryfall_id)"
        )
        cursor.execute(
            "CREATE INDEX IF NOT EXISTS idx_cards_identity "
            "ON cards(set_code, collector_number, language, foil)"
        )
        cursor.execute(
            "CREATE INDEX IF NOT EXISTS idx_cards_storage_code ON cards(storage_code)"
        )
        cursor.execute(
            "CREATE INDEX IF NOT EXISTS idx_cards_name ON cards(name)"
        )

        # Tabelle 2: Lagerplätze
        cursor.execute(
            """
            CREATE TABLE IF NOT EXISTS storage_slots (
                code TEXT PRIMARY KEY,
                is_occupied INTEGER DEFAULT 0
            )
            """
        )

        # Tabelle 3: Verkaufslog
        cursor.execute(
            """
            CREATE TABLE IF NOT EXISTS sales_log (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                card_id INTEGER,
                buyer_name TEXT,
                buyer_address TEXT,
                price REAL,
                date_sold TEXT,
                FOREIGN KEY(card_id) REFERENCES cards(id)
            )
            """
        )

        # Tabelle 4: Orders from Cardmarket emails
        cursor.execute(
            """
            CREATE TABLE IF NOT EXISTS orders (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                buyer_name TEXT NOT NULL,
                email_message_id TEXT UNIQUE NOT NULL,
                date_received TEXT NOT NULL,
                status TEXT DEFAULT 'open',
                date_completed TEXT
            )
            """
        )

        # Add email_date column to orders if missing (for email date vs. insertion date)
        cursor.execute("PRAGMA table_info(orders)")
        order_columns = [row[1] for row in cursor.fetchall()]
        if "email_date" not in order_columns:
            cursor.execute("ALTER TABLE orders ADD COLUMN email_date TEXT")
        # WP2a: order number, confirmed address (raw + editable) and amounts.
        order_new_cols = {
            "order_number": "TEXT",
            "address": "TEXT",
            "address_raw": "TEXT",
            # WP2b: set to 1 once the user has confirmed/saved the address in the
            # panel; the shipping note is only printed for a confirmed address.
            "address_confirmed": "INTEGER DEFAULT 0",
            # WP2c: manual language override for the shipping note ('de'/'en');
            # NULL means auto-detect from the recipient country.
            "print_language": "TEXT",
            "amount_gesamtwert": "REAL",
            "amount_gebuehren": "REAL",
            "amount_auszahlung": "REAL",
            "amount_versand": "REAL",
            "amount_gesamt": "REAL",
        }
        for col, coltype in order_new_cols.items():
            if col not in order_columns:
                cursor.execute(f"ALTER TABLE orders ADD COLUMN {col} {coltype}")

        # Tabelle 5: Order items (cards in orders)
        cursor.execute(
            """
            CREATE TABLE IF NOT EXISTS order_items (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                order_id INTEGER NOT NULL,
                card_name TEXT NOT NULL,
                quantity INTEGER NOT NULL,
                image_url TEXT,
                storage_code TEXT,
                FOREIGN KEY(order_id) REFERENCES orders(id) ON DELETE CASCADE
            )
            """
        )

        # WP2a: link the resolved inventory card (FK) and record the match state
        # plus the structured position fields extracted from the email.
        cursor.execute("PRAGMA table_info(order_items)")
        item_columns = [row[1] for row in cursor.fetchall()]
        item_new_cols = {
            "card_id": "INTEGER",
            "match_status": "TEXT DEFAULT 'unresolved'",
            "set_name": "TEXT",
            "set_code": "TEXT",
            "language": "TEXT",
            "condition": "TEXT",
            "foil": "INTEGER DEFAULT 0",
            "uncertain": "INTEGER DEFAULT 0",
            "unit_price": "REAL",
            "variant": "TEXT",
        }
        for col, coltype in item_new_cols.items():
            if col not in item_columns:
                cursor.execute(f"ALTER TABLE order_items ADD COLUMN {col} {coltype}")

        # Tabelle 6: Audit log for tracking changes
        cursor.execute(
            """
            CREATE TABLE IF NOT EXISTS audit_log (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                card_id INTEGER,
                user TEXT,
                action TEXT NOT NULL,
                field_name TEXT,
                old_value TEXT,
                new_value TEXT,
                timestamp TEXT NOT NULL,
                FOREIGN KEY(card_id) REFERENCES cards(id)
            )
            """
        )

        # -------------------------------------------------------------------
        # Tabelle 7+8: Belege und Buchungsjournal (WP3b).
        # Das Journal ist APPEND-ONLY: Buchungen werden nie geaendert oder
        # geloescht, Korrekturen erfolgen ausschliesslich per Stornobuchung.
        # Das wird unten per Trigger auf DB-Ebene erzwungen.
        # -------------------------------------------------------------------
        cursor.execute(
            """
            CREATE TABLE IF NOT EXISTS belege (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                original_name TEXT NOT NULL,
                gespeichert_als TEXT NOT NULL,
                mime TEXT,
                groesse INTEGER,
                sha256 TEXT NOT NULL,
                hochgeladen_am TEXT NOT NULL
            )
            """
        )
        cursor.execute(
            """
            CREATE TABLE IF NOT EXISTS journal (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                lfd_nr INTEGER NOT NULL UNIQUE,
                erfasst_am TEXT NOT NULL,
                buchungsdatum TEXT NOT NULL,
                art TEXT NOT NULL CHECK (art IN ('einnahme', 'ausgabe', 'storno')),
                kategorie TEXT NOT NULL,
                betrag_cent INTEGER NOT NULL,
                beschreibung TEXT,
                bestellung_id INTEGER,
                beleg_id INTEGER,
                storniert_buchung_id INTEGER,
                storniert_durch INTEGER,
                zahlungseingang_am TEXT,
                FOREIGN KEY(bestellung_id) REFERENCES orders(id),
                FOREIGN KEY(beleg_id) REFERENCES belege(id),
                FOREIGN KEY(storniert_buchung_id) REFERENCES journal(id)
            )
            """
        )
        # Eine Bestellung kann je Kategorie nur einmal uebernommen werden
        # (Stornozeilen sind ausgenommen).
        cursor.execute(
            "CREATE UNIQUE INDEX IF NOT EXISTS idx_journal_bestellung_kategorie "
            "ON journal(bestellung_id, kategorie) "
            "WHERE bestellung_id IS NOT NULL AND art <> 'storno'"
        )
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_journal_datum ON journal(buchungsdatum)")
        cursor.execute(
            "CREATE INDEX IF NOT EXISTS idx_journal_zahlung ON journal(zahlungseingang_am)"
        )

        # Loeschen ist grundsaetzlich verboten.
        cursor.execute(
            """
            CREATE TRIGGER IF NOT EXISTS journal_no_delete
            BEFORE DELETE ON journal
            BEGIN
                SELECT RAISE(ABORT, 'Buchungen duerfen nicht geloescht werden (Storno verwenden)');
            END
            """
        )
        # Aendern ist verboten — einzige Ausnahme: das einmalige Nachtragen von
        # zahlungseingang_am bzw. storniert_durch (NULL -> Wert). Alle
        # finanzrelevanten Felder bleiben unveraenderlich.
        cursor.execute(
            """
            CREATE TRIGGER IF NOT EXISTS journal_no_update
            BEFORE UPDATE ON journal
            FOR EACH ROW
            WHEN (
                OLD.lfd_nr <> NEW.lfd_nr
                OR OLD.erfasst_am <> NEW.erfasst_am
                OR OLD.buchungsdatum <> NEW.buchungsdatum
                OR OLD.art <> NEW.art
                OR OLD.kategorie <> NEW.kategorie
                OR OLD.betrag_cent <> NEW.betrag_cent
                OR IFNULL(OLD.beschreibung, '') <> IFNULL(NEW.beschreibung, '')
                OR IFNULL(OLD.bestellung_id, -1) <> IFNULL(NEW.bestellung_id, -1)
                OR IFNULL(OLD.beleg_id, -1) <> IFNULL(NEW.beleg_id, -1)
                OR IFNULL(OLD.storniert_buchung_id, -1) <> IFNULL(NEW.storniert_buchung_id, -1)
                OR (OLD.zahlungseingang_am IS NOT NULL
                    AND IFNULL(NEW.zahlungseingang_am, '') <> OLD.zahlungseingang_am)
                OR (OLD.storniert_durch IS NOT NULL
                    AND IFNULL(NEW.storniert_durch, -1) <> OLD.storniert_durch)
            )
            BEGIN
                SELECT RAISE(ABORT, 'Buchungen sind unveraenderlich (Storno verwenden)');
            END
            """
        )
        # Belege werden nicht geloescht.
        cursor.execute(
            """
            CREATE TRIGGER IF NOT EXISTS belege_no_delete
            BEFORE DELETE ON belege
            BEGIN
                SELECT RAISE(ABORT, 'Belege duerfen nicht geloescht werden');
            END
            """
        )


if __name__ == "__main__":
    initialize_database()
