# Using the Web Interface

This project includes a small Flask-based web application that provides a convenient way to view and modify the card database.

## Starting the server

Run the following command from the project directory:

```bash
python web.py
```

On first launch the SQLite database will be created automatically if it does not exist. Visit `http://localhost:5000` in your browser.
The first start also asks you to create a user account. After registration use
the `/login` page. Sessions expire after 15 minutes of inactivity.

To access the interface from another machine set `FLASK_RUN_HOST=0.0.0.0` when starting the server. You can also change the port with `FLASK_RUN_PORT`.

## Navigation

The navigation bar at the top links to the most important pages:

- **Cards** – List all cards with a search field.
- **Add Card** – Open a form to add a single card manually.
- **Bulk Add** – Add many cards at once by entering a list of names or uploading a JSON or CSV file.
- **Upload Queue** – Review and edit cards before adding them to the database.
- **Folders** – Manage folders (sets) and see which cards belong to each folder.
- **Upload DB** – Upload or replace the default-cards.db file for autocomplete functionality.
- **Update** – Pull updates from the git repository.
- **Export** – Download a CSV file containing all cards.

## Working with cards

The **Cards** page shows the current inventory. Use the search field to filter by card name or set code. Each card entry provides buttons to edit or delete the card. The **Add Card** form accepts the following fields:

- *Name* – Name of the card (required).
- *Language* – Language of the card, e.g. `EN` or `DE`.
- *Condition* – Condition such as `Near Mint`.
- *Price* – Price in Euro; use `0` if unknown.
- *Quantity* – Number of copies to add.
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
multiple versions exist a second dropdown lets you choose the exact variant,
including the collector number. Available collector numbers are also listed in a
dropdown next to the input field so you can quickly pick the desired one.

To change an existing card, click **Edit** next to the card and modify the fields in the form.
Use the *Foil* checkbox to mark shiny versions. Foil cards are shown with a **★** symbol next to their name in the card and folder overviews.

## Bulk adding cards

Use the **Bulk Add** page to quickly insert many cards. Enter one card name per line or upload a JSON file containing a list of card names (or objects with a `name` field). Alternatively you can upload a CSV file with the columns `Card Name`, `Set Code`, `Card Number`, `Quantity` and `Language`. Additional columns are ignored. The application tries to fetch additional information automatically if possible. Files encoded as UTF‑8 or Latin‑1 are accepted and Excel's optional `sep=;` line is ignored.

After submitting the form the cards appear in the **Upload Queue** where each entry can be reviewed and edited before adding it to the database.
A small checkbox lets you mark whether a card is foil before uploading.

## Managing folders

Folders correspond to sets or binders. The **Folders** page lists all folders and the cards stored inside each one. When clicking **Add Folder** you can enter a name and the number of pages of the binder. Each page provides nine storage slots. Cards can then be assigned to a folder and stored on a specific page and slot when adding or editing them. Existing folders can be edited using the **Edit** link next to each entry to change the name or page count. Increasing the number of pages automatically adds more storage slots. Folders can be removed from within the edit view using the **Delete** button.
The folder ID can also be changed in the edit view if needed. Newly created folders automatically receive the lowest free ID so the numbering stays compact.

The overview also offers a small form to filter the listed cards by name, number or storage code and to sort them by name, number or storage.

## Database upload

The **Upload DB** page allows you to upload a pre-built `default-cards.db` file to enable autocomplete functionality without having to build the database from the Scryfall JSON file. This is especially useful when running on a Raspberry Pi or other systems where building the database might be time-consuming.

To upload a database file:

1. Click **Upload DB** in the navigation menu.
2. Select the `default-cards.db` file from your computer.
3. Check the confirmation checkbox.
4. Click **Upload Database**.

The uploaded file will be validated to ensure it's a valid SQLite database with the required `cards` table. The maximum file size is 500 MB. If a database file already exists, it will be replaced by the new file.

