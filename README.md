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


The CLI can manage optional storage slots for physical binders.  Use option
``Ordner anlegen`` to create slots for a set (one page equals nine slots).  When
adding a card without a storage code, the next free slot will be used if
available.  If no slot exists the card is still stored without a location.



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

For a detailed walkthrough of the available pages and forms, see [docs/WEB_INTERFACE.md](docs/WEB_INTERFACE.md).

## Autocomplete

When adding a card through the web interface the **Name** field now shows suggestions provided by the Scryfall API. Start typing to get a dropdown list of matching card names.
