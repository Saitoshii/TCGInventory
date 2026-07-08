# Features — UI/UX & Data Model

This document describes the UI/UX and data-model capabilities of TCGInventory.
It consolidates the former `FEATURE_SUMMARY.md` and `IMPLEMENTATION_DETAILS.md`
root files.

## Overview

The system distinguishes between cards and display items, supports rich
filtering/sorting, a status workflow with auto-archiving, a reporting dashboard,
and a full audit log — while remaining server-rendered (Jinja2 + Bootstrap 5)
and backward compatible with existing databases.

## 1. Data model & migrations

Additional fields on the `cards` table:

- `item_type` (`card` | `display`) — distinguishes cards from display items,
  default `card`.
- `reserved_until` — supports a reservation workflow.
- `location_hint` — optional location for display items.

Additional table:

- `audit_log` — complete change tracking (with foreign key to `cards`).

Rules and guarantees:

- Smart, safe defaults for all new fields (backward compatible).
- Item type determines storage requirements: **storage is required for cards,
  optional for displays**.
- Language and condition are validated enums: languages `de/en/fr/it/es/ja`;
  conditions `MT/NM/EX/GD/LP/PL/PO`.
- All migrations are idempotent.

## 2. Status workflow & auto-archive

- Status values: `verfügbar`, `reserviert`, `verkauft`, `archiviert`.
- `add_card()` supports `item_type` and conditional storage.
- `sell_card()` auto-archives an item when quantity reaches 0 — cards are
  **never deleted** from the database, preserving data integrity.
- Audit logging records all changes to quantity, price and status, together with
  the acting user and a timestamp.
- Actions tracked: `sell` (card sold, quantity reduced), `update` (manual
  updates), `auto-archive` (automatic archiving).

## 3. Filtering, sorting & pagination (Cards view)

Multi-criteria filter bar:

- Status (verfügbar, reserviert, verkauft, archiviert)
- Language dropdown (de, en, fr, it, es, ja)
- Condition dropdown (MT, NM, EX, GD, LP, PL, PO)
- Item Type (card / display)
- Price range (min/max)
- Quantity range (min/max)
- Text search (name, set, collector number)

Additional behaviour:

- Sortable columns — click a header to sort ASC/DESC.
- Pagination with customizable page size (10 / 30 / 50 / 100).
- All filters are URL-based, so filtered views can be bookmarked.
- Pagination respects the active filters.
- Bulk selection checkboxes (UI ready for future batch operations).

## 4. UI enhancements

- Color-coded status badges: green = available, yellow = reserved,
  blue = sold, gray = archived.
- Hover image preview with a zoom effect.
- Clear "missing image" indicators/badges.
- Reorganized navigation grouped by function: Dashboard · Inventory (Cards,
  Folders) · Add/Import (Add Card, Bulk Add, Upload Queue) · Orders (Open
  Orders) · Reports & System (Audit Log, Export, Upload DB, Update).
- User context: username displayed; logout button repositioned to top-right.
- Consistent button styling (primary vs destructive actions).
- Responsive, Bootstrap-based layout.

## 5. Dashboard & reporting

Summary cards:

- Total Items (unique cards)
- Total Quantity (all copies)
- Total Value (calculated as Price × Quantity)
- Missing Images count

Analytics widgets:

- Top Folders by item count
- Top Sets by item count
- Inventory breakdown by Type (Card vs Display)
- Inventory breakdown by Status

Alerts:

- Low Stock Alert (Qty ≤ 1) with direct edit links
- Recent Activity — the last 20 audit-log entries

## 6. Audit log view

- Complete history of every change to quantity, price and status.
- Filtering by user and action type.
- Pagination (25 / 50 / 100 items per page).
- Direct links to the affected cards.
- Timestamp display.

## 7. Forms & validation

- Item-type selector at the top of the form with conditional field visibility.
- Storage section visible for both types, labeled appropriately.
- Location-hint field for displays.
- Language dropdown (replaces free-text input).
- Condition dropdown (prevents typos, ensures data consistency).
- Dynamic labels based on item type ("required for cards" vs "optional for
  displays").

## 8. Import / export

Enhanced CSV import supports flexible column names:

- Name / Card_Name
- Type (card / display, with validation)
- Storage / Storage_Code
- Location / Location_Hint
- Language / Lang
- Condition

Upload Queue improvements:

- Warning badges for missing images (⚠️)
- Info badges for missing storage on cards (ℹ️)
- Type badges (Card / Display)
- Display of all new fields

Filtered export:

- Respects all active filters
- Includes the new fields (Type, Location)
- Smart filename generation (e.g. `inventory_verfügbar_card.csv`)
- UTF-8 encoding

Example CSV import template:

```csv
Name,Type,Set,Language,Condition,Price,Qty,Storage,Location
Lightning Bolt,card,LEA,en,NM,5.50,3,O01-S01-P1,
Booster Box,display,MH3,en,,299.99,2,,Shelf A
```

Filtering examples:

- Show only available cards: Status = "verfügbar"
- Show low stock: Max Qty = 1
- Show expensive cards: Min Price = 100
- Show German cards: Language = "de"

## How to use

**Filtering cards:** Cards view → expand filter bar → select criteria → click
"Filter" → results update with pagination info.

**Viewing the dashboard:** click "Dashboard" → review summary statistics, low
stock alerts and recent activity.

**Viewing the audit log:** Reports & System → "Audit Log" → filter by user or
action type → click "View Card" to see affected items.

**Exporting filtered data:** apply filters in the Cards view → click "Export" →
download the filtered CSV with a smart filename.

## Notes

- Cards with quantity = 0 are automatically archived (not deleted).
- The audit log tracks all changes to quantity, price and status.
- Storage is required for cards, optional for displays.
- All filters are URL-based, allowing bookmarking of filtered views.
- The dashboard auto-calculates total value from price × quantity.

## Performance considerations

- Efficient SQL queries with appropriate indexing.
- Pagination to handle large datasets.
- A `limit` parameter on `fetch_cards` to prevent memory issues.
- Optimized audit-log queries.

## Backward compatibility

- All existing data is preserved.
- Safe defaults for new fields.
- No breaking changes to the existing API.
- Migrations are idempotent.

## Implementation history

This capability set was delivered as one milestone. Historical statistics from
that milestone:

- Files modified: 11
- Lines added: ~1,500; lines removed: ~150
- Tests passing at the time: 48/48
- Database tables added: 1 (`audit_log`)
- Database fields added: 3 (`item_type`, `reserved_until`, `location_hint`)

Files touched:

- **Backend:** `setup_db.py` (migrations + audit-log table),
  `lager_manager.py` (new parameters + audit logging), `web.py` (filtering,
  dashboard, audit-log routes).
- **Templates:** `base.html` (navigation, user context), `cards.html` (filters,
  sorting, pagination, bulk selection), `card_form.html` (type selector,
  conditional fields, dropdowns), `upload_queue.html` (warnings, type badges,
  new fields), `dashboard.html` (new), `audit_log.html` (new).
- **Tests:** `test_sell_card.py` (updated for archive behaviour).

## Out-of-scope / future enhancements

See [FUTURE_IMPROVEMENTS.md](FUTURE_IMPROVEMENTS.md). In short: bulk status
change, bulk folder assignment, auto-suggest last used storage per folder, a
`reserved_until` date picker and expiration workflow, advanced charting, and
real-time dashboard updates.
