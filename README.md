# TCGInventory

This project provides simple utilities to manage Magic: The Gathering cards via
SQLite.

## Installation

Install the required dependencies with ``pip``:

```bash
pip install -r requirements.txt
```

The Cardmarket integration requires additional environment variables for OAuth1
authentication:

```
MKM_APP_TOKEN=<your app token>
MKM_APP_SECRET=<your app secret>
MKM_TOKEN=<your access token>
MKM_TOKEN_SECRET=<your access token secret>
```

## Usage

If you haven't created the SQLite database yet, initialize it first:

```bash
python -m TCGInventory.setup_db
```

Run the CLI via the module path so Python can resolve the ``TCGInventory`` package:

```bash
python -m TCGInventory.cli
```

Within the CLI you can now update article prices on Cardmarket and fetch your
current sales.  When sales are fetched, a PDF summary can be generated.

Example functions can also be called programmatically, e.g.:

```python
from TCGInventory.lager_manager import add_card
from TCGInventory import DB_FILE  # default path to ``mtg_lager.db``
```

You can change ``DB_FILE`` before calling any functions if you want to store the
data elsewhere.
