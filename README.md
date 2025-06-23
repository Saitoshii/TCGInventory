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
available.  If no slot exists the card is still stored without a location.
Existing folders can be edited via ``Ordner bearbeiten`` to change the name or
page count without affecting the cards stored inside. Increasing the page count
automatically creates additional storage slots.



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

## Autocomplete

The application can provide name suggestions when adding a card.  Download the
Scryfall ``default-cards`` JSON manually and place it in ``TCGInventory/data`` as
``default-cards.json``.  Run ``python -m TCGInventory.build_card_db`` once to
convert the JSON file into ``default-cards.db`` which enables fast offline
search. If no database is available the Scryfall API is used as fallback.

## Export

Use the CLI option **Karten exportieren** or the *Export* link in the web interface to download all cards as a CSV file.
