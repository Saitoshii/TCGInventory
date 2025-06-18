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

The CLI automatically manages storage slots.  Create binders for each set using
option ``Ordner anlegen``.  Specify the set code and the number of pages; each
page provides nine slots.  When you add a card without specifying a storage
code, the next free slot in the corresponding binder will be used.



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
