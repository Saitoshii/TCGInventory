# Using the Web Interface

This project includes a small Flask-based web application that provides a convenient way to view and modify the card database.

## Starting the server

Run the following command from the project directory:

```bash
python web.py
```

On first launch the SQLite database will be created automatically if it does not exist. Visit `http://localhost:5000` in your browser.

## Navigation

The navigation bar at the top links to the most important pages:

- **Cards** – List all cards with a search field.
- **Add Card** – Open a form to add a single card manually.
- **Bulk Add** – Add many cards at once by entering a list of names or uploading a JSON file.
- **Folders** – Manage folders (sets) and see which cards belong to each folder.

## Working with cards

The **Cards** page shows the current inventory. Use the search field to filter by card name or set code. Each card entry provides buttons to edit or delete the card. The **Add Card** form accepts the following fields:

- *Name* – Name of the card (required).
- *Language* – Language of the card, e.g. `EN` or `DE`.
- *Condition* – Condition such as `Near Mint`.
- *Price* – Price in Euro; use `0` if unknown.
- *Storage Code* – Optional storage slot identifier.
- *Cardmarket ID* – Optional link to the Cardmarket entry.
- *Folder* – Assign the card to a folder (set) if one exists.

When typing the name a list of suggestions appears.  Place the Scryfall
``default-cards.json`` in ``TCGInventory/data`` and convert it with
``python -m TCGInventory.build_card_db``.  Suggestions and automatic
card lookups are then served from the local SQLite database without
internet access.  If no local data is found the Scryfall API is used as
fallback.
Selecting a suggested entry automatically fills language and ID fields. If
multiple versions exist a second dropdown lets you choose the exact variant.

To change an existing card, click **Edit** next to the card and modify the fields in the form.

## Bulk adding cards

Use the **Bulk Add** page to quickly insert many cards. Enter one card name per line or upload a JSON file containing a list of card names (or objects with a `name` field). The application tries to fetch additional information automatically if possible.

## Managing folders

Folders correspond to sets or binders. The **Folders** page lists all folders and the cards stored inside each one. Click **Add Folder** to create a new folder by specifying its name or set code. Cards can be assigned to a folder when adding or editing them.

