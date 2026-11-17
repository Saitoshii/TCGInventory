from __future__ import annotations

import re
import sqlite3
from datetime import datetime
from tabulate import tabulate
import csv

from . import DB_FILE

__all__ = [
    "add_card",
    "add_or_increment_card",
    "find_card_by_identity",
    "add_storage_slot",
    "create_binder",
    "list_all_cards",
    "update_card",
    "delete_card",
    "sell_card",
    "buche_aus",
    "cleanup_archived",
    "count_archived",
    "reconcile_slot_occupancy",
    "get_next_free_slot",
    "freie_plaetze",
    "add_folder",
    "edit_folder",
    "rename_folder",
    "delete_folder",
    "list_folders",
    "export_inventory_csv",
    "log_audit",
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
    "item_type",
    "reserved_until",
    "location_hint",
    "cardmarket_name",
    "rarity",
    "date_bought",
    "market_price",
}

# Constants for item types and status values.
# ``card``     – single card, lives on a binder slot (storage_code), identity path.
# ``display``  – sealed display / booster box.
# ``zubehoer`` – accessories (sleeves, deck boxes, playmats …).
# ``sonstiges``– anything else.
# Non-card items do not need a binder slot; they use a free-text ``location_hint``.
ITEM_TYPES = ["card", "display", "zubehoer", "sonstiges"]
# Item types that are physical stock but not single cards (no binder slot,
# no card identity, sold/added by name + quantity).
PRODUCT_TYPES = ["display", "zubehoer", "sonstiges"]
STATUS_VALUES = ["verfügbar", "reserviert", "verkauft", "archiviert"]
LANGUAGE_VALUES = ["de", "en", "fr", "it", "es", "ja", ""]
CONDITION_VALUES = ["MT", "NM", "EX", "GD", "LP", "PL", "PO", ""]


def log_audit(card_id: int, user: str, action: str, field_name: str = None, 
              old_value: str = None, new_value: str = None, cursor: sqlite3.Cursor = None) -> None:
    """Log an audit entry for a card change."""
    if cursor is None:
        with sqlite3.connect(DB_FILE) as conn:
            cursor = conn.cursor()
            cursor.execute(
                """
                INSERT INTO audit_log (card_id, user, action, field_name, old_value, new_value, timestamp)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (card_id, user, action, field_name, old_value, new_value, datetime.now().isoformat())
            )
    else:
        cursor.execute(
            """
            INSERT INTO audit_log (card_id, user, action, field_name, old_value, new_value, timestamp)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (card_id, user, action, field_name, old_value, new_value, datetime.now().isoformat())
        )

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
    item_type="card",
    location_hint="",
    *,
    rarity="",
    date_bought="",
    market_price=None,
    cardmarket_name="",
):
    """Add a card or display item and reserve a storage slot if available.

    ``rarity``, ``date_bought`` (purchase date from the import) and
    ``market_price`` (market price at export time) are optional structured
    fields from the CSV import; they are keyword-only so existing positional
    callers are unaffected.
    """
    # Validate item_type
    if item_type not in ITEM_TYPES:
        item_type = "card"
    
    # For cards, storage is required; for displays it's optional
    if item_type == "card" and not storage_code:
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
                           scryfall_id, image_url, foil, item_type, location_hint,
                           rarity, date_bought, market_price, cardmarket_name)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
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
                item_type,
                location_hint,
                rarity,
                date_bought,
                market_price,
                (cardmarket_name or "").strip(),
            ),
        )

    message = f"✅ {'Karte' if item_type == 'card' else 'Display-Item'} '{name}' erfolgreich hinzugefügt"
    if storage_code:
        message += f" und auf '{storage_code}' abgelegt."
    elif location_hint:
        message += f" (Standort: {location_hint})."
    else:
        message += "."
    print(message)
    return True

def find_card_by_identity(set_code, collector_number, language, foil, folder_id):
    """Return the id of an existing card with the same identity in the same
    folder, or ``None``.

    Identity = ``set_code`` + ``collector_number`` + ``language`` + ``foil``
    (condition and price are deliberately not part of the key, matching the
    canonical identity in CLAUDE.md). ``folder_id`` may be ``None`` (no folder).

    Der Set-Code wird ohne Rücksicht auf Groß- und Kleinschreibung verglichen
    (``TRK`` = ``trk``): es ist dasselbe Set, und zwei Zeilen für dieselbe
    Karte im selben Ordner wären eine Dublette. Die drei Schreibweisen stehen
    einzeln in der Abfrage, damit der Index weiter greift.
    """
    with sqlite3.connect(DB_FILE) as conn:
        cursor = conn.cursor()
        query = (
            "SELECT id FROM cards WHERE set_code IN (?, ?, ?) AND collector_number = ? "
            "AND language = ? AND foil = ? AND "
        )
        code = set_code or ""
        params = [code, code.lower(), code.upper(), collector_number, language,
                  int(bool(foil))]
        if folder_id in (None, ""):
            # „Kein Ordner" steht je nach Herkunft als NULL oder als leerer
            # Text in der Tabelle — beides ist dasselbe.
            query += "(folder_id IS NULL OR folder_id = '')"
        else:
            query += "folder_id = ?"
            params.append(int(folder_id))
        query += " ORDER BY id LIMIT 1"
        cursor.execute(query, params)
        row = cursor.fetchone()
        return row[0] if row else None


def add_or_increment_card(
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
    item_type="card",
    location_hint="",
    user="system",
    *,
    rarity="",
    date_bought="",
    market_price=None,
    cardmarket_name="",
):
    """Add a card, or increment the quantity of an identical one in the same folder.

    Dedupe rule (import default): if a card with the same identity
    (``set_code`` + ``collector_number`` + ``language`` + ``foil``) already
    exists in the same folder, its ``quantity`` is increased by ``quantity`` and
    no new storage slot is used. Otherwise a new card is created via
    :func:`add_card` (which allocates a slot). Dedupe only applies when both
    ``set_code`` and ``collector_number`` are present; incomplete identities
    always create a new row to avoid merging unrelated entries.
    """
    if set_code and collector_number:
        existing = find_card_by_identity(
            set_code, collector_number, language, foil, folder_id
        )
        if existing is not None:
            with sqlite3.connect(DB_FILE) as conn:
                cursor = conn.cursor()
                cursor.execute("SELECT quantity FROM cards WHERE id = ?", (existing,))
                old_qty = cursor.fetchone()[0] or 0
                new_qty = old_qty + (quantity or 1)
                cursor.execute(
                    "UPDATE cards SET quantity = ? WHERE id = ?", (new_qty, existing)
                )
                log_audit(existing, user, "update", "quantity", str(old_qty), str(new_qty), cursor)
                conn.commit()
            print(f"➕ Menge von '{name}' erhöht: {old_qty} → {new_qty}.")
            return True

    return add_card(
        name,
        set_code,
        language,
        condition,
        price,
        quantity,
        storage_code,
        cardmarket_id,
        folder_id,
        collector_number,
        scryfall_id,
        image_url,
        foil,
        item_type,
        location_hint,
        rarity=rarity,
        date_bought=date_bought,
        market_price=market_price,
        cardmarket_name=cardmarket_name,
    )


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
    """Lagerplätze für die Seiten 1 bis ``pages`` anlegen (9 je Seite).

    Vorhandene Plätze bleiben unberührt (``INSERT OR IGNORE``). Deshalb legt
    das Vergrößern eines Ordners immer den ganzen Ordner an — siehe
    ``edit_folder``.
    """
    prefix = f"O{int(folder_id):02d}-"
    with sqlite3.connect(DB_FILE) as conn:
        conn.executemany(
            "INSERT OR IGNORE INTO storage_slots (code, is_occupied) VALUES (?, 0)",
            [(f"{prefix}S{page:02d}-P{slot}",)
             for page in range(1, int(pages) + 1) for slot in range(1, 10)])
        conn.commit()


#: Lagerplatz im Ordner-Schema: O<Ordner>-S<Seite>-P<Platz>.
_ORDNERPLATZ = re.compile(r"O(\d+)-S(\d+)-P(\d+)")


def platz_gibt_es_im_ordner(code: str | None) -> bool:
    """Ist das ein Platz 1–9 einer Seite? Andere Schemata gelten als gültig.

    Plätze ab P10 stammen aus einer kurzzeitig aktiven Fassung mit 18 Plätzen
    je Seite. Im Ordner gibt es sie nicht: jede Seite hat 9 Fächer.
    """
    treffer = _ORDNERPLATZ.fullmatch(code or "")
    return not treffer or 1 <= int(treffer.group(3)) <= 9


def get_next_free_slot(prefix: str) -> str | None:
    """Den ersten freien Platz mit diesem Präfix — in der Reihenfolge im Ordner.

    * Nur Plätze 1–9 einer Seite. Übrig gebliebene Plätze ab P10 sortierte
      SQLite als Text sogar **vor** P2 — neue Karten bekamen Plätze, die es
      im Ordner nicht gibt.
    * Ein Platz, auf den schon eine Karte verweist, ist nicht frei, auch wenn
      die Belegung anderes sagt. Die Belegung lief früher beim Umziehen einer
      Karte auseinander; dann landeten zwei Karten auf demselben Platz.
    """
    from TCGInventory.sortierung import platz

    codes = freie_plaetze(prefix)
    return min(codes, key=platz) if codes else None


def freie_plaetze(prefix: str) -> list[str]:
    """Alle wirklich freien Plätze mit diesem Präfix (Regeln wie oben)."""
    with sqlite3.connect(DB_FILE) as conn:
        codes = [z[0] for z in conn.execute(
            "SELECT code FROM storage_slots WHERE code LIKE ? AND is_occupied = 0 "
            "AND code NOT IN (SELECT storage_code FROM cards "
            "                 WHERE COALESCE(storage_code, '') <> '')",
            (f"{prefix}%",))]
    return [c for c in codes if platz_gibt_es_im_ordner(c)]

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
def update_card(card_id, user="system", **kwargs):
    """Update fields of a card if the field names are valid. Logs changes to audit_log."""
    invalid_fields = [key for key in kwargs if key not in ALLOWED_FIELDS]
    if invalid_fields:
        print(f"❌ Ungültige Felder: {', '.join(invalid_fields)}. Aktualisierung abgebrochen.")
        return

    if not kwargs:
        print("⚠️ Keine Felder zum Aktualisieren angegeben.")
        return

    with sqlite3.connect(DB_FILE) as conn:
        cursor = conn.cursor()
        
        # Get current values for audit logging
        cursor.execute(f"SELECT {', '.join(kwargs.keys())} FROM cards WHERE id = ?", (card_id,))
        old_row = cursor.fetchone()
        
        if old_row is None:
            print(f"⚠️ Keine Karte mit ID {card_id} gefunden.")
            return

        fields = []
        values = []

        # Log changes for auditable fields
        for idx, (key, value) in enumerate(kwargs.items()):
            fields.append(f"{key} = ?")
            values.append(value)
            
            # Log changes to quantity, price, and status
            if key in ['quantity', 'price', 'status'] and old_row[idx] != value:
                log_audit(card_id, user, 'update', key, str(old_row[idx]), str(value), cursor)

        values.append(card_id)

        query = f"UPDATE cards SET {', '.join(fields)} WHERE id = ?"
        cursor.execute(query, values)

        # Zieht die Karte um, zieht die Belegung mit: der alte Platz wird
        # frei (wenn dort sonst nichts liegt), der neue belegt. Ohne das
        # blieb der alte Platz für immer „belegt", und der neue galt als
        # frei — die nächste Karte landete auf demselben Platz.
        if "storage_code" in kwargs:
            alt = old_row[list(kwargs).index("storage_code")] or ""
            neu = kwargs["storage_code"] or ""
            if alt != neu:
                _free_slot_if_unused(cursor, alt, card_id)
                if neu:
                    cursor.execute(
                        "UPDATE storage_slots SET is_occupied = 1 WHERE code = ?",
                        (neu,))
        
    # Note: reaching quantity 0 no longer archives the card. Archiving was
    # removed (CLAUDE.md — no archiving); a sold-out card is removed via
    # ``sell_card`` and its slot freed. A manual edit to 0 leaves the row for
    # the user to delete or restock.
    print(f"📝 Karte mit ID {card_id} wurde aktualisiert.")

# ❌ Funktion: Karte löschen
def delete_card(card_id, user="system"):
    """Eine Zeile ganz aus dem Bestand entfernen (kein Verkauf).

    Das Entfernen steht im Änderungsprotokoll — mit Name und Menge. Zuvor
    verschwand eine gelöschte Karte spurlos; nach einer Inventur ließ sich
    nicht mehr sagen, was berichtigt worden war.
    """
    with sqlite3.connect(DB_FILE) as conn:
        cursor = conn.cursor()

        # Lagerplatz freigeben
        cursor.execute("SELECT storage_code, name, quantity FROM cards WHERE id = ?", (card_id,))
        result = cursor.fetchone()

        if result:
            storage_code = result[0]
            log_audit(card_id, user, 'delete', 'name',
                      f"{result[1]} (Menge {result[2] or 0})", 'entfernt', cursor)
            _free_slot_if_unused(cursor, storage_code, card_id)
            cursor.execute("DELETE FROM cards WHERE id = ?", (card_id,))
            if storage_code:
                print(
                    f"🗑️ Karte mit ID {card_id} wurde gelöscht und Lagerplatz '{storage_code}' freigegeben."
                )
            else:
                print(f"🗑️ Karte mit ID {card_id} wurde gelöscht.")
        else:
            print(f"⚠️ Keine Karte mit ID {card_id} gefunden.")


def _free_slot_if_unused(cursor: sqlite3.Cursor, storage_code: str, keep_card_id: int) -> None:
    """Mark a storage slot as free, but only if no *other* card still sits on it.

    Several cards may share one physical slot (see CLAUDE.md). The occupancy
    flag is therefore only cleared once the last card leaves the slot — never
    while another card still references it.
    """
    if not storage_code:
        return
    cursor.execute(
        "SELECT COUNT(*) FROM cards WHERE storage_code = ? AND id <> ?",
        (storage_code, keep_card_id),
    )
    if cursor.fetchone()[0] == 0:
        cursor.execute(
            "UPDATE storage_slots SET is_occupied = 0 WHERE code = ?",
            (storage_code,),
        )


def sell_card(card_id: int, user="system") -> bool:
    """Sell one copy of a card.

    While copies remain, only the quantity is decreased. When the last copy is
    sold the row is **removed** and its storage slot is freed again, so the next
    card added to that folder refills the now-empty place (see CLAUDE.md — no
    archiving, empty slots are reused logically). The sale is recorded in the
    audit log (including the name), and the sales history lives in the orders
    data — deleting the inventory row does not lose it.
    """
    with sqlite3.connect(DB_FILE) as conn:
        verkauft = _verkaufe_ein_exemplar(conn.cursor(), card_id, user)
        conn.commit()
    return verkauft


def _verkaufe_ein_exemplar(cursor: sqlite3.Cursor, card_id: int, user="system") -> bool:
    """Ein Exemplar ausbuchen — innerhalb der Transaktion des Aufrufers.

    Der Kern von :func:`sell_card`. Ohne eigenes ``commit``: wer mehrere
    Exemplare zusammen mit etwas anderem ausbucht (eine Bestellung abschließen),
    braucht alles in **einer** Transaktion. Sonst steht nach einem Abbruch die
    Hälfte im Bestand, oder zwei gleichzeitige Klicks ziehen doppelt ab.
    """
    cursor.execute("SELECT quantity, storage_code, name FROM cards WHERE id = ?", (card_id,))
    row = cursor.fetchone()
    if not row:
        print(f"⚠️ Keine Karte mit ID {card_id} gefunden.")
        return False
    qty = row[0] or 0
    storage_code = row[1]
    name = row[2]
    new_qty = qty - 1

    if new_qty > 0:
        cursor.execute(
            "UPDATE cards SET quantity = ? WHERE id = ?",
            (new_qty, card_id),
        )
        log_audit(card_id, user, 'sell', 'quantity', str(qty), str(new_qty), cursor)
        print(f"🛒 Karte verkauft. {new_qty} verbleibend.")
        return True

    # Last copy sold: log first (card_id still valid), free the slot,
    # then remove the row. No 'archiviert' status remains.
    log_audit(card_id, user, 'sell', 'quantity', str(qty), '0', cursor)
    log_audit(card_id, user, 'sell-remove', 'name', name, 'verkauft', cursor)
    _free_slot_if_unused(cursor, storage_code, card_id)
    cursor.execute("DELETE FROM cards WHERE id = ?", (card_id,))
    print("🛒 Karte verkauft, Zeile entfernt und Lagerplatz freigegeben.")
    return True


def buche_aus(cursor: sqlite3.Cursor, card_id: int, menge: int, user="system") -> int:
    """Bis zu ``menge`` Exemplare ausbuchen; gibt zurück, wie viele es waren.

    Mehr als im Bestand steht, wird nicht abgezogen: eine Zeile mit Menge 2
    gibt bei einer Bestellung über 3 genau 2 her. Der Aufrufer erfährt die
    Zahl und kann den Fehlbestand melden, statt ihn zu verschweigen. Eine
    Zeile mit Menge 0 gibt nichts her und bleibt unangetastet.

    Arbeitet in der Transaktion des Aufrufers (kein ``commit``).
    """
    cursor.execute("SELECT quantity FROM cards WHERE id = ?", (card_id,))
    zeile = cursor.fetchone()
    vorhanden = max(0, (zeile[0] or 0)) if zeile else 0
    abgezogen = 0
    for _ in range(min(max(0, int(menge or 0)), vorhanden)):
        if _verkaufe_ein_exemplar(cursor, card_id, user):
            abgezogen += 1
    return abgezogen


def reconcile_slot_occupancy() -> int:
    """Recompute every storage slot's occupancy from the current cards.

    A slot is occupied iff at least one card still references it. Returns the
    number of slots that were freed by the reconciliation. Idempotent and
    non-destructive — it only flips ``is_occupied`` flags, never deletes.
    """
    with sqlite3.connect(DB_FILE) as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT COUNT(*) FROM storage_slots WHERE is_occupied = 1")
        occupied_before = cursor.fetchone()[0]
        cursor.execute("UPDATE storage_slots SET is_occupied = 0")
        cursor.execute(
            "UPDATE storage_slots SET is_occupied = 1 WHERE code IN "
            "(SELECT storage_code FROM cards WHERE storage_code IS NOT NULL AND storage_code <> '')"
        )
        cursor.execute("SELECT COUNT(*) FROM storage_slots WHERE is_occupied = 1")
        occupied_after = cursor.fetchone()[0]
        conn.commit()
    return max(0, occupied_before - occupied_after)


def cleanup_archived(user: str = "system") -> tuple[int, int]:
    """Remove legacy archived / sold-out card rows and reclaim their slots.

    One-time housekeeping for databases that still carry ``status='archiviert'``
    (or quantity-0) rows from before archiving was dropped. It deletes those
    rows and then reconciles slot occupancy so the freed places become
    available again. Returns ``(removed_rows, freed_slots)``. Deliberately
    manual — never runs automatically on startup or update.
    """
    with sqlite3.connect(DB_FILE) as conn:
        cursor = conn.cursor()
        cursor.execute(
            "SELECT id, name FROM cards WHERE status = 'archiviert' OR quantity <= 0"
        )
        rows = cursor.fetchall()
        for card_id, name in rows:
            log_audit(card_id, user, 'cleanup-remove', 'name', name, 'entfernt', cursor)
            cursor.execute("DELETE FROM cards WHERE id = ?", (card_id,))
        conn.commit()
    freed = reconcile_slot_occupancy()
    print(f"🧹 {len(rows)} archivierte Zeile(n) entfernt, {freed} Platz/Plätze freigegeben.")
    return len(rows), freed


def count_archived() -> int:
    """Return how many archived / sold-out card rows currently exist."""
    with sqlite3.connect(DB_FILE) as conn:
        cursor = conn.cursor()
        cursor.execute(
            "SELECT COUNT(*) FROM cards WHERE status = 'archiviert' OR quantity <= 0"
        )
        return cursor.fetchone()[0]


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
            # Den ganzen Ordner anlegen, nicht nur „so viele Seiten wie neu
            # dazukommen": create_binder(…, 5) legte beim Erhöhen von 25 auf
            # 30 Seiten die Seiten 1–5 noch einmal an statt 26–30 — es kam
            # kein einziger Platz dazu. Vorhandene Plätze bleiben unberührt.
            create_binder(target_id, new_pages)

        if cursor.rowcount:
            print(f"📁 Ordner {target_id} aktualisiert.")
            return True

        print(f"⚠️ Kein Ordner mit ID {folder_id} gefunden.")
        return False


def delete_folder(folder_id: int, user="system") -> bool:
    """Delete a folder along with its cards and storage slots."""
    prefix = f"O{int(folder_id):02d}-"

    # Collect card IDs inside the folder
    with sqlite3.connect(DB_FILE) as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT id FROM cards WHERE folder_id = ?", (folder_id,))
        card_ids = [row[0] for row in cursor.fetchall()]

    # Remove cards and free their slots
    for card_id in card_ids:
        delete_card(card_id, user)

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

