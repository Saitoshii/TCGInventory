from __future__ import annotations

import sqlite3
from datetime import datetime
from tabulate import tabulate

from . import DB_FILE

__all__ = [
    "add_card",
    "add_storage_slot",
    "create_binder",
    "list_all_cards",
    "update_card",
    "delete_card",
    "get_next_free_slot",
    "add_folder",
    "list_folders",
]

# Valid columns in the ``cards`` table that can be updated via ``update_card``
ALLOWED_FIELDS = {
    "name",
    "set_code",
    "language",
    "condition",
    "price",
    "storage_code",
    "cardmarket_id",
    "folder_id",
    "status",
    "date_added",
}

# 📦 Funktion: Karte hinzufügen
def add_card(
    name,
    set_code,
    language,
    condition,
    price,
    storage_code=None,
    cardmarket_id="",
    folder_id=None,
):
    """Add a card and automatically reserve a storage slot if none is given."""
    if not storage_code:
        storage_code = get_next_free_slot(set_code)
        if not storage_code:
            print(f"⚠️ Kein freier Lagerplatz für Set {set_code} vorhanden.")
            return

    with sqlite3.connect(DB_FILE) as conn:
        cursor = conn.cursor()

        # Lagerplatz als belegt markieren
        cursor.execute(
            "UPDATE storage_slots SET is_occupied = 1 WHERE code = ?",
            (storage_code,),
        )

        cursor.execute(
            """
        INSERT INTO cards (name, set_code, language, condition, price, storage_code, cardmarket_id, date_added, folder_id)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
            (
                name,
                set_code,
                language,
                condition,
                price,
                storage_code,
                cardmarket_id,
                datetime.now().isoformat(),
                folder_id,
            ),
        )

    print(f"✅ Karte '{name}' erfolgreich hinzugefügt und auf '{storage_code}' abgelegt.")

# 📍 Funktion: Lagerplatz hinzufügen
def add_storage_slot(code):
    with sqlite3.connect(DB_FILE) as conn:
        cursor = conn.cursor()

        cursor.execute('''
        INSERT OR IGNORE INTO storage_slots (code, is_occupied)
        VALUES (?, 0)
        ''', (code,))

    print(f"📁 Lagerplatz '{code}' hinzugefügt oder bereits vorhanden.")


def create_binder(set_code: str, pages: int) -> None:
    """Create storage slots for a binder consisting of several pages."""
    for page in range(1, pages + 1):
        for slot in range(1, 10):
            code = f"{set_code}-P{page:02d}-S{slot:02d}"
            add_storage_slot(code)


def get_next_free_slot(set_code: str) -> str | None:
    """Return the first free slot for a given set code."""
    with sqlite3.connect(DB_FILE) as conn:
        cursor = conn.cursor()
        cursor.execute(
            "SELECT code FROM storage_slots WHERE code LIKE ? AND is_occupied = 0 ORDER BY code LIMIT 1",
            (f"{set_code}-%",),
        )
        result = cursor.fetchone()
        return result[0] if result else None

# 🔍 Funktion: Alle Karten anzeigen
def list_all_cards():
    with sqlite3.connect(DB_FILE) as conn:
        cursor = conn.cursor()

        cursor.execute(
            """
            SELECT cards.id, cards.name, cards.set_code, cards.language,
                   cards.condition, cards.price, cards.storage_code,
                   COALESCE(folders.name, ''), cards.status
            FROM cards
            LEFT JOIN folders ON cards.folder_id = folders.id
            """
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
            "Ordner",
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


# ---------------------------------------------------------------------------
# Folder helpers
# ---------------------------------------------------------------------------

def add_folder(name: str) -> int | None:
    """Create a folder entry if it does not exist and return its ID."""
    with sqlite3.connect(DB_FILE) as conn:
        cursor = conn.cursor()
        cursor.execute("INSERT OR IGNORE INTO folders (name) VALUES (?)", (name,))
        conn.commit()
        cursor.execute("SELECT id FROM folders WHERE name = ?", (name,))
        row = cursor.fetchone()
        if row:
            print(f"📁 Ordner '{name}' angelegt.")
            return row[0]
    return None


def list_folders():
    """Return a list of all folders."""
    with sqlite3.connect(DB_FILE) as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT id, name FROM folders ORDER BY name")
        return cursor.fetchall()
