# TCGInventory

A Flask-based inventory system for a Cardmarket TCG shop (Magic: The Gathering).
It is server-rendered (Jinja2 + Bootstrap 5) on top of SQLite, and is designed
to run on a Raspberry Pi. It provides both a command-line interface and a small
web application to manage cards, storage locations, folders/binders, and orders
imported from Cardmarket notification emails.

> The project framework and non-negotiable constraints (Pi operation,
> server-rendered, the end-to-end identity path, "never blindly guess") are
> documented in [`CLAUDE.md`](CLAUDE.md). Read it before making changes.

## Contents

- [Installation](#installation)
- [CLI usage](#cli-usage)
- [Web interface](#web-interface)
- [Gmail order ingestion](#gmail-order-ingestion)
- [Autocomplete](#autocomplete)
- [Export](#export)
- [Project structure](#project-structure)
- [Architecture in brief](#architecture-in-brief)
- [Testing](#testing)
- [Further documentation](#further-documentation)

## Installation

Install the required dependencies with `pip`:

```bash
pip install -r requirements.txt
```

For the web interface, set `FLASK_SECRET_KEY` in your environment.

If you have not created the SQLite database yet, initialize it first:

```bash
python -m TCGInventory.setup_db
```

## CLI usage

Run the CLI via the module path so Python can resolve the `TCGInventory`
package:

```bash
python -m TCGInventory.cli
```

On first launch you are asked to register a single user. Subsequent starts
require a login and sessions expire after 15 minutes of inactivity.

The CLI can manage optional storage slots for physical binders. Use the option
`Ordner anlegen` to create slots for a set (one page equals nine slots). When
adding a card without a storage code, the next free slot is used if available.
If no slot exists the card is still stored without a location code. Existing
folders can be edited via `Ordner bearbeiten` to change the name or page count
without affecting the cards stored inside. Increasing the page count
automatically creates additional storage slots. The folder ID can also be
changed there as long as the new number is not already used. Newly created
folders always receive the lowest free ID so numbering stays compact. Existing
folders can also be renamed via `Ordner umbenennen` without affecting the cards
stored inside. Deleting a folder removes all cards stored inside and frees their
slots.

The CLI also provides `Karte verkaufen` to quickly decrease the quantity of a
card. Once no copies remain the card is removed automatically.

Functions can also be called programmatically, e.g.:

```python
from TCGInventory.lager_manager import add_card
from TCGInventory import DB_FILE  # default path to ``mtg_lager.db``
```

You can change `DB_FILE` before calling any functions if you want to store the
data elsewhere.

## Web interface

In addition to the CLI, a small Flask-based web application is available. It
provides functionality such as viewing, adding and editing cards, a dashboard,
an audit log, and order management.

Start the local server with:

```bash
python web.py
```

The application initializes the SQLite database on first start if it does not
exist. Open `http://localhost:5000` in your browser. You are prompted to
register a user on the very first start; use the `/login` page afterwards. The
web session times out after 15 minutes of inactivity.

To expose the web interface to the entire local network, set `FLASK_RUN_HOST`
before running the command:

```bash
FLASK_RUN_HOST=0.0.0.0 python web.py
```

Optionally change the port with `FLASK_RUN_PORT`.

For a detailed walkthrough of the available pages and forms, see
[docs/WEB_INTERFACE.md](docs/WEB_INTERFACE.md). For the UI/UX and data-model
capabilities (filtering, dashboard, audit log, item types, import/export), see
[docs/FEATURES.md](docs/FEATURES.md).

## Gmail order ingestion

The application can automatically import Cardmarket orders from Gmail. This
feature:

- Polls Gmail for "Bitte versenden" (German) or "Please ship" (English) emails
  from Cardmarket.
- Extracts buyer information and card lists from both German and English email
  formats.
- Displays orders in the "Offene Bestellungen" tab with images and storage
  locations.
- Operates during 11:00–22:00 with configurable polling.

**Setup required:** Gmail OAuth credentials must be configured. See
[docs/GMAIL_SETUP.md](docs/GMAIL_SETUP.md) for detailed setup instructions, and
[docs/ORDERS.md](docs/ORDERS.md) for how order matching, deletion and email-date
handling are implemented.

Environment variables needed:

```bash
export GMAIL_CLIENT_ID="your-client-id.apps.googleusercontent.com"
export GMAIL_CLIENT_SECRET="your-client-secret"
```

**Database schema:** the order ingestion feature requires the `orders` and
`order_items` tables. These are created automatically when you start the web
application or CLI for the first time, and are also added to existing databases
automatically (the migration is safe and preserves all existing data).

If you are upgrading from an older version without these tables, simply start
the web application or run the CLI — the schema is updated automatically:

```bash
# Web interface — tables are created on startup
python web.py

# Or via CLI — tables are created on startup
python -m TCGInventory.cli

# Or manually trigger database initialization
python -m TCGInventory.setup_db
```

## Autocomplete

The application can provide name suggestions when adding a card. Download the
Scryfall `default-cards` JSON manually and place it in `TCGInventory/data` as
`default-cards.json`. Run `python -m TCGInventory.build_card_db` once to convert
the JSON file into `default-cards.db`, which enables fast offline search. If no
database is available, the Scryfall API is used as a fallback.

Alternatively, you can upload a pre-built `default-cards.db` file directly
through the web interface using the **Upload DB** button in the navigation menu.
This is especially useful when running on a Raspberry Pi or other systems where
building the database from JSON might be time-consuming.

## Export

Use the CLI option **Karten exportieren** or the *Export* link in the web
interface to download the inventory as a CSV file. You can optionally export a
single folder by specifying its name (CLI) or by using the folder-specific link
in the web interface. The CSV uses a semicolon as separator for better
compatibility with spreadsheet applications.

## Project structure

```
TCGInventory/
├── web.py             # Flask web application (routes, views)
├── cli.py             # Command-line interface
├── lager_manager.py   # Inventory & storage: add/update/delete/sell cards, folders
├── card_scanner.py    # Scryfall enrichment, barcode scanning, variant lookup
├── email_parser.py    # Parse Cardmarket order emails
├── gmail_auth.py      # Gmail OAuth + email fetching
├── order_service.py   # Order ingestion service (polls Gmail, saves orders)
├── cardmarket_api.py  # Cardmarket API client (no API access currently; future use)
├── auth.py            # User authentication (register/login)
├── setup_db.py        # Schema creation & non-destructive migrations
├── build_card_db.py   # Convert Scryfall JSON into default-cards.db
├── repo_updater.py    # Self-update from git
├── __init__.py        # Package init; defines DB_FILE
├── templates/         # Jinja2 templates
├── static/            # css/ (app.css, tokens.css), js/, img/
├── data/              # SQLite databases (mtg_lager.db, default-cards.db)
├── docs/              # Documentation
└── tests/             # pytest suite
```

## Architecture in brief

- **Stack:** Python 3.11+, Flask, SQLite (`data/*.db`), Jinja2, Bootstrap 5.3
  (via CDN). Server-rendered; no heavyweight JS build step (it runs on a
  Raspberry Pi). Custom styling lives in a thin CSS layer on top of Bootstrap
  under `static/css/`.
- **Core data model — the `cards` table:** a card is uniquely identified by the
  combination `set_code` + `collector_number` + `language` + `foil`. Scryfall
  enrichment adds the canonical `scryfall_id` and `cardmarket_id` (for later API
  use). `storage_code` is the physical location (multiple cards per location
  allowed); `folder_id` is the binder/folder.
- **End-to-end identity path (must not be broken):**
  Dragonshield CSV → Scryfall enrichment (canonical IDs) → inventory row with a
  storage location → parse order email → match on
  `name + set_code + language [+ foil]` → show location → "sold" removes exactly
  that row.
- **Enrichment is offline:** Scryfall bulk data is available locally
  (`default-cards.db` / `build_card_db.py`); it is used for enrichment instead
  of querying online.

## Testing

Tests live in `tests/` and use pytest:

```bash
python -m pytest
```

## Further documentation

- [`CLAUDE.md`](CLAUDE.md) — project framework, constraints and principles.
- [docs/WEB_INTERFACE.md](docs/WEB_INTERFACE.md) — walkthrough of the web pages
  and forms.
- [docs/FEATURES.md](docs/FEATURES.md) — UI/UX and data-model capabilities
  (filtering, dashboard, audit log, item types, import/export).
- [docs/ORDERS.md](docs/ORDERS.md) — how the Open Orders feature is implemented.
- [docs/GMAIL_SETUP.md](docs/GMAIL_SETUP.md) — Gmail OAuth setup for order
  ingestion.
- [docs/FUTURE_IMPROVEMENTS.md](docs/FUTURE_IMPROVEMENTS.md) — suggested future
  enhancements and Raspberry Pi deployment notes (systemd, nginx).
