# TCGInventory

This project provides simple utilities to manage Magic: The Gathering cards via
SQLite.

## Installation

Install the required dependencies with ``pip``:

```bash
pip install -r requirements.txt
```

For the optional web interface set ``FLASK_SECRET_KEY`` in your environment.

## Usage

If you haven't created the SQLite database yet, initialize it first:

```bash
python -m TCGInventory.setup_db
```

Run the CLI via the module path so Python can resolve the ``TCGInventory`` package:

```bash
python -m TCGInventory.cli
```
On first launch you will be asked to register a single user. Subsequent starts
require a login and sessions expire after 15 minutes of inactivity.


The CLI can manage optional storage slots for physical binders.  Use option
``Ordner anlegen`` to create slots for a set (one page equals nine slots).  When
adding a card without a storage code, the next free slot will be used if
available. If no slot exists the card is still stored without a location code.
Existing folders can be edited via ``Ordner bearbeiten`` to change the name or
page count without affecting the cards stored inside. Increasing the page count
automatically creates additional storage slots. The folder ID can also be
changed there as long as the new number is not already used. Newly created
folders always receive the lowest free ID so numbering stays compact. Existing
folders can also be renamed via ``Ordner umbenennen`` without affecting the
cards stored inside. Deleting a folder will remove all cards stored inside and
free their slots.

The CLI also provides an option ``Karte verkaufen`` to quickly decrease the
quantity of a card. Once no copies remain the card is removed automatically.



Example functions can also be called programmatically, e.g.:

```python
from TCGInventory.lager_manager import add_card
from TCGInventory import DB_FILE  # default path to ``mtg_lager.db``
```

You can change ``DB_FILE`` before calling any functions if you want to store the
data elsewhere.

## Web Interface

In addition to the CLI a small Flask based web application is available.  It
provides basic functionality like viewing, adding and editing cards.

Start the local server with:

```bash
python web.py
```

The application will initialize the SQLite database on first start if it does
not exist.  Open ``http://localhost:5000`` in your browser to use the
interface.
You will be prompted to register a user on the very first start. Use the
``/login`` page afterwards. The web session times out after 15 minutes of
inactivity.

To expose the web interface to the entire local network set ``FLASK_RUN_HOST``
before running the command, for example:

```bash
FLASK_RUN_HOST=0.0.0.0 python web.py
```

Optionally change the port with ``FLASK_RUN_PORT``.

For a detailed walkthrough of the available pages and forms, see [docs/WEB_INTERFACE.md](docs/WEB_INTERFACE.md).

## Gmail Order Ingestion

The application can automatically import Cardmarket orders from Gmail. This feature:
- Polls Gmail for "Bitte versenden" emails from Cardmarket
- Extracts buyer information and card lists
- Displays orders in the "Offene Bestellungen" tab with images and storage locations
- Operates during 11:00-22:00 with configurable polling

**Setup required:** Gmail OAuth credentials must be configured. See [docs/GMAIL_SETUP.md](docs/GMAIL_SETUP.md) for detailed setup instructions.

Environment variables needed:
```bash
export GMAIL_CLIENT_ID="your-client-id.apps.googleusercontent.com"
export GMAIL_CLIENT_SECRET="your-client-secret"
```

**Database Schema:** The order ingestion feature requires the `orders` and `order_items` tables. These tables are automatically created when you start the web application or CLI for the first time, and will also be added to existing databases automatically (the migration is safe and preserves all existing data).

If you're upgrading from an older version without these tables, simply start the web application or run the CLI - the schema will be updated automatically:

```bash
# Web interface - tables are created on startup
python web.py

# Or via CLI - tables are created on startup
python -m TCGInventory.cli

# Or manually trigger database initialization
python -m TCGInventory.setup_db
```

## Autocomplete

The application can provide name suggestions when adding a card.  Download the
Scryfall ``default-cards`` JSON manually and place it in ``TCGInventory/data`` as
``default-cards.json``.  Run ``python -m TCGInventory.build_card_db`` once to
convert the JSON file into ``default-cards.db`` which enables fast offline
search. If no database is available the Scryfall API is used as fallback.

Alternatively, you can upload a pre-built ``default-cards.db`` file directly 
through the web interface using the **Upload DB** button in the navigation menu. 
This is especially useful when running on a Raspberry Pi or other systems where 
building the database from JSON might be time-consuming.

## Export

Use the CLI option **Karten exportieren** or the *Export* link in the web interface to download the inventory as a CSV file. You can optionally export a single folder by specifying its name (CLI) or by using the folder-specific link in the web interface. The CSV uses a semicolon as separator for better compatibility with spreadsheet applications.
