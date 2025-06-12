import sqlite3
from datetime import datetime
from tabulate import tabulate

DB_FILE = "mtg_lager.db"

# Valid columns in the ``cards`` table that can be updated via ``update_card``
ALLOWED_FIELDS = {
    "name",
    "set_code",
    "language",
    "condition",
    "price",
    "storage_code",
    "cardmarket_id",
    "status",
    "date_added",
}

# 📦 Funktion: Karte hinzufügen
def add_card(name, set_code, language, condition, price, storage_code, cardmarket_id):
    with sqlite3.connect(DB_FILE) as conn:
        cursor = conn.cursor()

        # Lagerplatz als belegt markieren
        cursor.execute("UPDATE storage_slots SET is_occupied = 1 WHERE code = ?", (storage_code,))

        cursor.execute('''
        INSERT INTO cards (name, set_code, language, condition, price, storage_code, cardmarket_id, date_added)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        ''', (name, set_code, language, condition, price, storage_code, cardmarket_id, datetime.now().isoformat()))

    print(f"✅ Karte '{name}' erfolgreich hinzugefügt.")

# 📍 Funktion: Lagerplatz hinzufügen
def add_storage_slot(code):
    with sqlite3.connect(DB_FILE) as conn:
        cursor = conn.cursor()

        cursor.execute('''
        INSERT OR IGNORE INTO storage_slots (code, is_occupied)
        VALUES (?, 0)
        ''', (code,))

    print(f"📁 Lagerplatz '{code}' hinzugefügt oder bereits vorhanden.")

# 🔍 Funktion: Alle Karten anzeigen
def list_all_cards():
    with sqlite3.connect(DB_FILE) as conn:
        cursor = conn.cursor()

        cursor.execute(
            "SELECT id, name, set_code, language, condition, price, storage_code, status FROM cards"
        )
        cards = cursor.fetchall()

    print("\n📋 Aktuelle Karten im Lager:")
    if cards:
        headers = [
            "ID",
            "Name",
            "Set",
            "Sprache",
            "Zustand",
            "Preis (€)",
            "Lagerplatz",
            "Status",
        ]
        print(tabulate(cards, headers=headers, tablefmt="github"))
    else:
        print("Keine Karten gefunden.")

# ✏️ Funktion: Karte aktualisieren
def update_card(card_id, **kwargs):
    """Update fields of a card if the field names are valid."""
    invalid_fields = [key for key in kwargs if key not in ALLOWED_FIELDS]
    if invalid_fields:
        print(f"❌ Ungültige Felder: {', '.join(invalid_fields)}. Aktualisierung abgebrochen.")
        return

    if not kwargs:
        print("⚠️ Keine Felder zum Aktualisieren angegeben.")
        return

    with sqlite3.connect(DB_FILE) as conn:
        cursor = conn.cursor()

        fields = []
        values = []

        for key, value in kwargs.items():
            fields.append(f"{key} = ?")
            values.append(value)

        values.append(card_id)

        query = f"UPDATE cards SET {', '.join(fields)} WHERE id = ?"
        cursor.execute(query, values)

    print(f"📝 Karte mit ID {card_id} wurde aktualisiert.")

# ❌ Funktion: Karte löschen
def delete_card(card_id):
    with sqlite3.connect(DB_FILE) as conn:
        cursor = conn.cursor()

        # Lagerplatz freigeben
        cursor.execute("SELECT storage_code FROM cards WHERE id = ?", (card_id,))
        result = cursor.fetchone()

        if result:
            storage_code = result[0]
            cursor.execute("UPDATE storage_slots SET is_occupied = 0 WHERE code = ?", (storage_code,))
            cursor.execute("DELETE FROM cards WHERE id = ?", (card_id,))
            print(f"🗑️ Karte mit ID {card_id} wurde gelöscht und Lagerplatz '{storage_code}' freigegeben.")
        else:
            print(f"⚠️ Keine Karte mit ID {card_id} gefunden.")