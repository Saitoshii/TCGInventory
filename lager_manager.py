from __future__ import annotations

import sqlite3
from datetime import datetime
from tabulate import tabulate
import csv

from . import DB_FILE

__all__ = [
    "add_card",
    "add_storage_slot",
    "create_binder",
    "list_all_cards",
    "update_card",
    "delete_card",
    "sell_card",
    "get_next_free_slot",
    "add_folder",
    "edit_folder",
    "rename_folder",
    "delete_folder",
    "list_folders",
    "export_inventory_csv",
]

# Valid columns in the ``cards`` table that can be updated via ``update_card``
ALLOWED_FIELDS = {
    "name",
    "set_code",
    "language",
    "condition",
    "price",
    "quantity",
    "storage_code",
    "cardmarket_id",
    "folder_id",
    "status",
    "date_added",
    "collector_number",
    "scryfall_id",
    "image_url",
    "foil",
}

# 📦 Funktion: Karte hinzufügen
def add_card(
    name,
    set_code,
    language,
    condition,
    price,
    quantity=1,
    storage_code=None,
    cardmarket_id="",
    folder_id=None,
    collector_number="",
    scryfall_id="",
    image_url="",
    foil=False,
):
    """Add a card and reserve a storage slot if available."""
    if not storage_code:
        prefix = f"O{int(folder_id):02d}-" if folder_id else f"{set_code}-"
        storage_code = get_next_free_slot(prefix)
        if not storage_code:
            target = f"Ordner {folder_id}" if folder_id else f"Set {set_code}"
            print(
                f"ℹ️ Kein freier Lagerplatz für {target}. Karte wird ohne Lagerplatz gespeichert."
            )

    with sqlite3.connect(DB_FILE) as conn:
        cursor = conn.cursor()

        # Lagerplatz als belegt markieren
        if storage_code:
            cursor.execute(
                "UPDATE storage_slots SET is_occupied = 1 WHERE code = ?",
                (storage_code,),
            )

        cursor.execute(
            """
        INSERT INTO cards (name, set_code, language, condition, price, quantity, storage_code,
                           cardmarket_id, date_added, folder_id, collector_number,
                           scryfall_id, image_url, foil)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
            (
                name,
                set_code,
                language,
                condition,
                price,
                quantity,
                storage_code,
                cardmarket_id,
                datetime.now().isoformat(),
                folder_id,
                collector_number,
                scryfall_id,
                image_url,
                int(bool(foil)),
            ),
        )

    message = f"✅ Karte '{name}' erfolgreich hinzugefügt"
    if storage_code:
        message += f" und auf '{storage_code}' abgelegt."
    else:
        message += "."
    print(message)
    return True

# 📍 Funktion: Lagerplatz hinzufügen
def add_storage_slot(code):
    with sqlite3.connect(DB_FILE) as conn:
        cursor = conn.cursor()

        cursor.execute('''
        INSERT OR IGNORE INTO storage_slots (code, is_occupied)
        VALUES (?, 0)
        ''', (code,))

    print(f"📁 Lagerplatz '{code}' hinzugefügt oder bereits vorhanden.")


def create_binder(folder_id: int, pages: int) -> None:
    """Create storage slots for a binder consisting of several pages."""
    prefix = f"O{int(folder_id):02d}-"
    for page in range(1, pages + 1):
        for slot in range(1, 10):
            code = f"{prefix}S{page:02d}-P{slot}"
            add_storage_slot(code)


def get_next_free_slot(prefix: str) -> str | None:
    """Return the first free slot for a given prefix."""
    with sqlite3.connect(DB_FILE) as conn:
        cursor = conn.cursor()
        cursor.execute(
            "SELECT code FROM storage_slots WHERE code LIKE ? AND is_occupied = 0 ORDER BY code LIMIT 1",
            (f"{prefix}%",),
        )
        result = cursor.fetchone()
        return result[0] if result else None

# 🔍 Funktion: Alle Karten anzeigen
def list_all_cards():
    with sqlite3.connect(DB_FILE) as conn:
        cursor = conn.cursor()

        cursor.execute(
            """
            SELECT cards.collector_number, cards.name, cards.set_code, cards.language,
                   cards.condition, cards.price, cards.quantity, cards.storage_code,
                   COALESCE(folders.name, ''), cards.status
            FROM cards
            LEFT JOIN folders ON cards.folder_id = folders.id
            """
        )
        cards = cursor.fetchall()

    print("\n📋 Aktuelle Karten im Lager:")
    if cards:
        headers = [
            "Collector Number",
            "Name",
            "Set",
            "Sprache",
            "Zustand",
            "Preis (€)",
            "Anzahl",
            "Lagerplatz",
            "Ordner",
            "Status",
        ]
        print(tabulate(cards, headers=headers, tablefmt="github"))
    else:
        print("Keine Karten gefunden.")


def export_inventory_csv(path: str, folder: str | None = None) -> None:
    """Write the current card list to a CSV file, optionally filtered by folder."""
    with sqlite3.connect(DB_FILE) as conn, open(path, "w", newline="", encoding="utf-8") as f:
        cursor = conn.cursor()
        query = (
            """
            SELECT cards.collector_number, cards.name, cards.set_code, cards.language,
                   cards.condition, cards.price, cards.quantity, cards.storage_code,
                   COALESCE(folders.name, ''), cards.status
            FROM cards
            LEFT JOIN folders ON cards.folder_id = folders.id
            """
        )
        params: tuple = ()
        if folder:
            query += " WHERE folders.name = ?"
            params = (folder,)
        cursor.execute(query, params)
        writer = csv.writer(f, delimiter=";")
        writer.writerow([
            "Collector Number",
            "Name",
            "Set",
            "Sprache",
            "Zustand",
            "Preis (€)",
            "Anzahl",
            "Lagerplatz",
            "Ordner",
            "Status",
        ])
        for row in cursor.fetchall():
            writer.writerow([
                row[0],
                row[1],
                row[2],
                row[3],
                row[4],
                row[5],
                row[6],
                row[7],
                row[8],
                row[9],
            ])
    print(f"📤 Kartenexport gespeichert unter '{path}'.")

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
            if storage_code:
                cursor.execute(
                    "UPDATE storage_slots SET is_occupied = 0 WHERE code = ?",
                    (storage_code,),
                )
            cursor.execute("DELETE FROM cards WHERE id = ?", (card_id,))
            if storage_code:
                print(
                    f"🗑️ Karte mit ID {card_id} wurde gelöscht und Lagerplatz '{storage_code}' freigegeben."
                )
            else:
                print(f"🗑️ Karte mit ID {card_id} wurde gelöscht.")
        else:
            print(f"⚠️ Keine Karte mit ID {card_id} gefunden.")


def sell_card(card_id: int) -> bool:
    """Decrease quantity of a card or delete it when none remain."""
    with sqlite3.connect(DB_FILE) as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT quantity FROM cards WHERE id = ?", (card_id,))
        row = cursor.fetchone()
        if not row:
            print(f"⚠️ Keine Karte mit ID {card_id} gefunden.")
            return False
        qty = row[0] or 0
        if qty > 1:
            cursor.execute(
                "UPDATE cards SET quantity = ? WHERE id = ?",
                (qty - 1, card_id),
            )
            conn.commit()
            print(f"🛒 Karte verkauft. {qty - 1} verbleibend.")
            return True

    delete_card(card_id)
    return True


# ---------------------------------------------------------------------------
# Folder helpers
# ---------------------------------------------------------------------------

def _next_folder_id(cursor: sqlite3.Cursor) -> int:
    """Return the lowest free folder id starting at 1."""
    cursor.execute("SELECT id FROM folders ORDER BY id")
    used_ids = [row[0] for row in cursor.fetchall()]
    next_id = 1
    for fid in used_ids:
        if fid == next_id:
            next_id += 1
        elif fid > next_id:
            break
    return next_id


def add_folder(name: str, pages: int = 0) -> int | None:
    """Create a folder entry if it does not exist and return its ID."""
    with sqlite3.connect(DB_FILE) as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT id FROM folders WHERE name = ?", (name,))
        row = cursor.fetchone()
        if row:
            cursor.execute("UPDATE folders SET pages = ? WHERE id = ?", (pages, row[0]))
            conn.commit()
            print(f"📁 Ordner '{name}' angelegt.")
            return row[0]

        folder_id = _next_folder_id(cursor)
        cursor.execute(
            "INSERT INTO folders (id, name, pages) VALUES (?, ?, ?)",
            (folder_id, name, pages),
        )
        conn.commit()
        print(f"📁 Ordner '{name}' angelegt.")
        return folder_id


def list_folders():
    """Return a list of all folders."""
    with sqlite3.connect(DB_FILE) as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT id, name, pages FROM folders ORDER BY name")
        return cursor.fetchall()


def rename_folder(folder_id: int, new_name: str) -> bool:
    """Rename a folder without touching its cards."""
    return edit_folder(folder_id, new_name)


def edit_folder(
    folder_id: int, new_name: str, pages: int | None = None, new_id: int | None = None
) -> bool:
    """Update folder name, page count and optionally its id."""
    with sqlite3.connect(DB_FILE) as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT pages FROM folders WHERE id = ?", (folder_id,))
        row = cursor.fetchone()
        if not row:
            print(f"⚠️ Kein Ordner mit ID {folder_id} gefunden.")
            return False

        current_pages = row[0] or 0
        new_pages = pages if pages is not None else current_pages

        target_id = new_id if new_id is not None else folder_id

        if new_id is not None and new_id != folder_id:
            cursor.execute("SELECT 1 FROM folders WHERE id = ?", (new_id,))
            if cursor.fetchone():
                print(f"⚠️ Ordner-ID {new_id} bereits vergeben.")
                return False
            old_prefix = f"O{int(folder_id):02d}-"
            new_prefix = f"O{int(new_id):02d}-"
            cursor.execute(
                "UPDATE storage_slots SET code = REPLACE(code, ?, ?) WHERE code LIKE ?",
                (old_prefix, new_prefix, f"{old_prefix}%"),
            )
            cursor.execute(
                "UPDATE cards SET storage_code = REPLACE(storage_code, ?, ?) WHERE storage_code LIKE ?",
                (old_prefix, new_prefix, f"{old_prefix}%"),
            )
            cursor.execute(
                "UPDATE cards SET folder_id = ? WHERE folder_id = ?",
                (new_id, folder_id),
            )
            cursor.execute(
                "UPDATE folders SET id = ?, name = ?, pages = ? WHERE id = ?",
                (new_id, new_name, new_pages, folder_id),
            )
        else:
            cursor.execute(
                "UPDATE folders SET name = ?, pages = ? WHERE id = ?",
                (new_name, new_pages, folder_id),
            )
        conn.commit()

        if new_pages > current_pages:
            create_binder(target_id, new_pages - current_pages)

        if cursor.rowcount:
            print(f"📁 Ordner {target_id} aktualisiert.")
            return True

        print(f"⚠️ Kein Ordner mit ID {folder_id} gefunden.")
        return False


def delete_folder(folder_id: int) -> bool:
    """Delete a folder along with its cards and storage slots."""
    prefix = f"O{int(folder_id):02d}-"

    # Collect card IDs inside the folder
    with sqlite3.connect(DB_FILE) as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT id FROM cards WHERE folder_id = ?", (folder_id,))
        card_ids = [row[0] for row in cursor.fetchall()]

    # Remove cards and free their slots
    for card_id in card_ids:
        delete_card(card_id)

    with sqlite3.connect(DB_FILE) as conn:
        cursor = conn.cursor()
        cursor.execute("DELETE FROM storage_slots WHERE code LIKE ?", (f"{prefix}%",))
        cursor.execute("DELETE FROM folders WHERE id = ?", (folder_id,))
        conn.commit()
        if cursor.rowcount:
            print(f"🗑️ Ordner {folder_id} gelöscht.")
            return True

    print(f"⚠️ Kein Ordner mit ID {folder_id} gefunden.")
    return False

