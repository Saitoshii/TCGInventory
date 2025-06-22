import os
import sqlite3
import re
from flask import (
    Flask,
    render_template,
    request,
    redirect,
    url_for,
    flash,
    jsonify,
    session,
    Response,
)
import csv
import io

from TCGInventory.lager_manager import (
    add_card,
    update_card,
    delete_card,
    add_storage_slot,
    add_folder,
    rename_folder,
    create_binder,
    list_folders,
)
from TCGInventory.card_scanner import (
    fetch_card_info_by_name,
    autocomplete_names,
    fetch_variants,
)
from TCGInventory.setup_db import initialize_database
from TCGInventory import DB_FILE
from TCGInventory.auth import (
    init_user_db,
    user_exists,
    register_user,
    verify_user,
    login_required,
)
from datetime import timedelta

app = Flask(__name__)
app.secret_key = os.environ.get("FLASK_SECRET_KEY", "tcg-secret")
app.permanent_session_lifetime = timedelta(minutes=15)

# Queue for cards uploaded via the bulk add feature
UPLOAD_QUEUE: list[dict] = []


def init_db() -> None:
    if not os.path.exists(DB_FILE):
        initialize_database()
    else:
        init_user_db()


def fetch_cards(search: str | None = None):
    with sqlite3.connect(DB_FILE) as conn:
        c = conn.cursor()
        query = (
            """
            SELECT cards.id, cards.name, cards.set_code, cards.language,
                   cards.condition, cards.price, cards.quantity, cards.storage_code,
                   COALESCE(folders.name, ''), cards.status, cards.image_url
            FROM cards
            LEFT JOIN folders ON cards.folder_id = folders.id
            """
        )
        params: tuple = ()
        if search:
            query += " WHERE cards.name LIKE ? OR cards.set_code LIKE ?"
            like = f"%{search}%"
            params = (like, like)
        c.execute(query, params)
        return c.fetchall()


def get_card(card_id: int):
    with sqlite3.connect(DB_FILE) as conn:
        c = conn.cursor()
        c.execute(
            """
            SELECT id, name, set_code, language, condition, price, quantity, storage_code,
                   cardmarket_id, folder_id, collector_number, scryfall_id,
                   image_url
            FROM cards WHERE id = ?
            """,
            (card_id,),
        )
        return c.fetchone()


@app.route("/register", methods=["GET", "POST"])
def register_view():
    init_user_db()
    if user_exists():
        return redirect(url_for("login"))
    if request.method == "POST":
        username = request.form["username"]
        password = request.form["password"]
        register_user(username, password)
        session["user"] = username
        session.permanent = True
        flash("Registration successful")
        return redirect(url_for("index"))
    return render_template("register.html")


@app.route("/login", methods=["GET", "POST"])
def login():
    init_user_db()
    if not user_exists():
        return redirect(url_for("register_view"))
    if request.method == "POST":
        username = request.form["username"]
        password = request.form["password"]
        if verify_user(username, password):
            session["user"] = username
            session.permanent = True
            flash("Logged in")
            next_page = request.args.get("next") or url_for("index")
            return redirect(next_page)
        flash("Invalid credentials", "error")
    return render_template("login.html")


@app.route("/logout")
def logout():
    session.pop("user", None)
    flash("Logged out")
    return redirect(url_for("login"))


@app.route("/api/autocomplete")
def autocomplete_api():
    """Return card name suggestions for the given query."""
    query = request.args.get("q", "")
    if not query:
        return jsonify([])
    return jsonify(autocomplete_names(query))


@app.route("/api/lookup")
def lookup_api():
    """Return card variants for the given name."""
    name = request.args.get("name", "")
    if not name:
        return jsonify([])
    return jsonify(fetch_variants(name))


@app.route("/")
@login_required
def index():
    return redirect(url_for("list_cards"))


@app.route("/cards")
@login_required
def list_cards():
    search = request.args.get("q", "")
    cards = fetch_cards(search if search else None)
    return render_template("cards.html", cards=cards, search=search)


@app.route("/cards/export")
def export_cards():
    """Return a CSV export of all cards."""
    rows = fetch_cards()
    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow([
        "ID",
        "Name",
        "Set",
        "Sprache",
        "Zustand",
        "Preis (€)",
        "Anzahl",
        "Lagerplatz",
        "Ordner",
        "Status",
        "Bild",
    ])
    writer.writerows(rows)
    resp = Response(output.getvalue(), mimetype="text/csv")
    resp.headers["Content-Disposition"] = "attachment; filename=inventory.csv"
    return resp


@app.route("/cards/add", methods=["GET", "POST"])
@login_required
def add_card_view():
    folders = list_folders()
    if request.method == "POST":
        folder_id = request.form.get("folder_id")
        set_code = ""
        for f in folders:
            if str(f[0]) == str(folder_id):
                set_code = f[1]
                break
        page = request.form.get("page")
        slot = request.form.get("slot")
        storage_code = ""
        if page and slot and folder_id:
            try:
                storage_code = f"O{int(folder_id):02d}-S{int(page):02d}-P{int(slot)}"
            except ValueError:
                storage_code = ""
        success = add_card(
            request.form["name"],
            set_code,
            request.form.get("language", ""),
            request.form.get("condition", ""),
            float(request.form.get("price", 0) or 0),
            int(request.form.get("quantity", 1) or 1),
            storage_code,
            request.form.get("cardmarket_id", ""),
            folder_id,
            request.form.get("collector_number", ""),
            request.form.get("scryfall_id", ""),
            request.form.get("image_url", ""),
        )
        if success:
            flash("Card added")
            return redirect(url_for("list_cards"))
        flash("No free storage slot", "error")
    return render_template(
        "card_form.html", card=None, folders=folders, folder_part="", page="", slot=""
    )


@app.route("/cards/<int:card_id>/edit", methods=["GET", "POST"])
@login_required
def edit_card_view(card_id: int):
    card = get_card(card_id)
    folders = list_folders()
    if request.method == "POST":
        folder_id = request.form.get("folder_id")
        set_code = card[2]
        for f in folders:
            if str(f[0]) == str(folder_id):
                set_code = f[1]
                break
        page = request.form.get("page")
        slot = request.form.get("slot")
        storage_code = ""
        if page and slot and folder_id:
            try:
                storage_code = f"O{int(folder_id):02d}-S{int(page):02d}-P{int(slot)}"
            except ValueError:
                storage_code = ""
        update_card(
            card_id,
            name=request.form["name"],
            set_code=set_code,
            language=request.form.get("language", ""),
            condition=request.form.get("condition", ""),
            price=float(request.form.get("price", 0) or 0),
            quantity=int(request.form.get("quantity", 1) or 1),
            storage_code=storage_code,
            cardmarket_id=request.form.get("cardmarket_id", ""),
            folder_id=folder_id,
            collector_number=request.form.get("collector_number", ""),
            scryfall_id=request.form.get("scryfall_id", ""),
            image_url=request.form.get("image_url", ""),
        )
        flash("Card updated")
        return redirect(url_for("list_cards"))
    folder_part = page = slot = ""
    if card and card[7]:
        m = re.match(r"O(\d+)\-S(\d+)\-P(\d+)", card[7])
        if m:
            folder_part = f"O{int(m.group(1)):02d}"
            page = m.group(2)
            slot = m.group(3)
    return render_template(
        "card_form.html", card=card, folders=folders, folder_part=folder_part, page=page, slot=slot
    )


@app.route("/cards/<int:card_id>/delete")
@login_required
def delete_card_route(card_id: int):
    delete_card(card_id)
    flash("Card deleted")
    return redirect(url_for("list_cards"))


@app.route("/folders")
@login_required
def list_folders_view():
    sort = request.args.get("sort", "name")
    search = request.args.get("q", "").strip()

    allowed = {"id": "id", "storage": "storage_code", "name": "name"}
    order_col = allowed.get(sort, "name")

    folders = list_folders()
    folder_cards = {}
    with sqlite3.connect(DB_FILE) as conn:
        c = conn.cursor()
        for fid, _ in folders:
            query = (
                "SELECT id, name, set_code, quantity, storage_code FROM cards "
                "WHERE folder_id=?"
            )
            params = [fid]
            if search:
                query += (
                    " AND (CAST(id AS TEXT) LIKE ? OR storage_code LIKE ? OR name LIKE ?)"
                )
                like = f"%{search}%"
                params.extend([like, like, like])
            query += f" ORDER BY {order_col}"
            c.execute(query, params)
            folder_cards[fid] = c.fetchall()
    return render_template(
        "folders.html",
        folders=folders,
        folder_cards=folder_cards,
        sort=sort,
        search=search,
    )


@app.route("/folders/add", methods=["GET", "POST"])
@login_required
def add_folder_view():
    if request.method == "POST":
        name = request.form["name"]
        pages = request.form.get("pages", "1")
        folder_id = add_folder(name)
        if folder_id is not None:
            try:
                create_binder(folder_id, int(pages))
            except ValueError:
                pass
        flash("Folder added")
        return redirect(url_for("list_folders_view"))
    return render_template("folder_form.html")


@app.route("/folders/edit/<int:folder_id>", methods=["GET", "POST"])
@login_required
def edit_folder_view(folder_id: int):
    with sqlite3.connect(DB_FILE) as conn:
        c = conn.cursor()
        c.execute("SELECT id, name FROM folders WHERE id=?", (folder_id,))
        folder = c.fetchone()
    if not folder:
        flash("Folder not found", "error")
        return redirect(url_for("list_folders_view"))
    if request.method == "POST":
        rename_folder(folder_id, request.form["name"])
        flash("Folder updated")
        return redirect(url_for("list_folders_view"))
    return render_template("folder_form.html", folder=folder)


@app.route("/storage/add", methods=["GET", "POST"])
@login_required
def add_storage_view():
    if request.method == "POST":
        add_storage_slot(request.form["code"])
        flash("Storage slot added")
        return redirect(url_for("list_cards"))
    return render_template("storage_form.html")


@app.route("/cards/bulk_add", methods=["GET", "POST"])
@login_required
def bulk_add_view():
    folders = list_folders()
    if request.method == "POST":
        folder_id = request.form.get("folder_id")
        set_code = ""
        for f in folders:
            if str(f[0]) == str(folder_id):
                set_code = f[1]
                break

        added_any = False
        # handle uploaded JSON file
        json_file = request.files.get("json_file")
        if json_file and json_file.filename:
            try:
                import json
                data = json.load(json_file)
                if isinstance(data, list):
                    for entry in data:
                        name = entry.get("name") if isinstance(entry, dict) else None
                        if not name and isinstance(entry, str):
                            name = entry
                        if not name:
                            continue
                        info = fetch_card_info_by_name(name) or {}
                        UPLOAD_QUEUE.append(
                            {
                                "name": info.get("name", name),
                                "set_code": set_code or info.get("set_code", ""),
                                "language": info.get("language", ""),
                                "condition": "",
                                "quantity": 1,
                                "cardmarket_id": info.get("cardmarket_id", ""),
                                "folder_id": folder_id,
                                "collector_number": info.get("collector_number", ""),
                                "scryfall_id": info.get("scryfall_id", ""),
                                "image_url": info.get("image_url", ""),
                            }
                        )
                        added_any = True
            except Exception:
                flash("Invalid JSON file")

        # handle uploaded CSV file
        csv_file = request.files.get("csv_file")
        if csv_file and csv_file.filename:
            try:
                raw = csv_file.read()
                try:
                    content = raw.decode("utf-8-sig")
                except UnicodeDecodeError:
                    content = raw.decode("latin-1")
                lines = content.splitlines()
                delimiter = None
                if lines and lines[0].lower().startswith("sep="):
                    delimiter = lines[0][4:].strip()
                    content = "\n".join(lines[1:])
                try:
                    dialect = csv.Sniffer().sniff(content, delimiters=delimiter or ",;")
                except csv.Error:
                    dialect = csv.excel
                    if delimiter:
                        dialect.delimiter = delimiter
                reader = csv.DictReader(io.StringIO(content), dialect=dialect)
                for row in reader:
                    normalized = {}
                    for k, v in row.items():
                        if not k:
                            # extra columns end up under the key ``None``
                            # and may contain a list of values
                            continue
                        if isinstance(v, list):
                            v = v[0] if v else ""
                        normalized[k.strip().lower().replace(" ", "_")] = (v or "").strip()
                    name = normalized.get("card_name", "")
                    if not name:
                        continue
                    qty = int(normalized.get("quantity", "1") or 1)
                    set_row = normalized.get("set_code", "")
                    card_no = normalized.get("card_number", "")
                    language = normalized.get("language", "")
                    condition = normalized.get("condition", "")
                    info = fetch_card_info_by_name(name)
                    if info and set_row and info.get("set_code") != set_row:
                        variants = fetch_variants(name)
                        for v in variants:
                            if v.get("set_code") == set_row and (
                                not card_no or v.get("collector_number") == card_no
                            ):
                                info = v
                                break
                    if not info:
                        info = {}
                    UPLOAD_QUEUE.append(
                        {
                            "name": info.get("name", name),
                            "set_code": set_row or set_code or info.get("set_code", ""),
                            "language": language or info.get("language", ""),
                            "condition": condition,
                            "quantity": qty,
                            "cardmarket_id": info.get("cardmarket_id", ""),
                            "folder_id": folder_id,
                            "collector_number": card_no or info.get("collector_number", ""),
                            "scryfall_id": info.get("scryfall_id", ""),
                            "image_url": info.get("image_url", ""),
                        }
                    )
                    added_any = True
            except Exception as exc:
                flash(f"Invalid CSV file: {exc}")

        # names from textarea
        for line in request.form.get("cards", "").splitlines():
            name = line.strip()
            if not name:
                continue
            info = fetch_card_info_by_name(name) or {}
            UPLOAD_QUEUE.append(
                {
                    "name": info.get("name", name),
                    "set_code": set_code or info.get("set_code", ""),
                    "language": info.get("language", ""),
                    "condition": "",
                    "quantity": 1,
                    "cardmarket_id": info.get("cardmarket_id", ""),
                    "folder_id": folder_id,
                    "collector_number": info.get("collector_number", ""),
                    "scryfall_id": info.get("scryfall_id", ""),
                    "image_url": info.get("image_url", ""),
                }
            )
            added_any = True

        if added_any:
            flash("Cards queued for review")
            return redirect(url_for("upload_queue_view"))
        flash("No cards added", "error")
        return redirect(url_for("bulk_add_view"))
    return render_template("bulk_add.html", folders=folders)


@app.route("/cards/upload_queue")
@login_required
def upload_queue_view():
    """Display queued cards from the bulk upload."""
    return render_template("upload_queue.html", queue=UPLOAD_QUEUE)


@app.route("/cards/upload_queue/edit/<int:index>", methods=["GET", "POST"])
@login_required
def edit_queued_card(index: int):
    """Edit details of a queued card before adding it."""
    if not (0 <= index < len(UPLOAD_QUEUE)):
        flash("Invalid card index", "error")
        return redirect(url_for("upload_queue_view"))

    folders = list_folders()
    card = UPLOAD_QUEUE[index]

    if request.method == "POST":
        folder_id = request.form.get("folder_id") or None
        set_code = card.get("set_code", "")
        for f in folders:
            if str(f[0]) == str(folder_id):
                set_code = f[1]
                break

        card.update(
            {
                "name": request.form["name"],
                "language": request.form.get("language", ""),
                "condition": request.form.get("condition", ""),
                "price": float(request.form.get("price", 0) or 0),
                "quantity": int(request.form.get("quantity", 1) or 1),
                "cardmarket_id": request.form.get("cardmarket_id", ""),
                "folder_id": folder_id,
                "set_code": set_code,
                "collector_number": request.form.get("collector_number", ""),
                "scryfall_id": request.form.get("scryfall_id", ""),
                "image_url": request.form.get("image_url", ""),
            }
        )
        flash("Card updated")
        return redirect(url_for("upload_queue_view"))

    tuple_card = (
        0,
        card.get("name", ""),
        card.get("set_code", ""),
        card.get("language", ""),
        card.get("condition", ""),
        card.get("price", 0),
        card.get("quantity", 1),
        "",
        card.get("cardmarket_id", ""),
        card.get("folder_id"),
        card.get("collector_number", ""),
        card.get("scryfall_id", ""),
        card.get("image_url", ""),
    )
    folder_part = ""
    if card.get("folder_id"):
        folder_part = f"O{int(card['folder_id']):02d}"
    return render_template(
        "card_form.html",
        card=tuple_card,
        folders=folders,
        folder_part=folder_part,
        page="",
        slot="",
    )


@app.route("/cards/upload_queue/add/<int:index>")
@login_required
def upload_card_route(index: int):
    """Add a queued card to the database and remove it from the queue."""
    if 0 <= index < len(UPLOAD_QUEUE):
        card = UPLOAD_QUEUE.pop(index)
        success = add_card(
            card["name"],
            card.get("set_code", ""),
            card.get("language", ""),
            card.get("condition", ""),
            0,
            card.get("quantity", 1),
            None,
            card.get("cardmarket_id", ""),
            card.get("folder_id"),
            card.get("collector_number", ""),
            card.get("scryfall_id", ""),
            card.get("image_url", ""),
        )
        flash("Card added" if success else "No slot for " + card["name"], "error" if not success else None)
    return redirect(url_for("upload_queue_view"))


@app.route("/cards/upload_queue/add_all")
@login_required
def upload_all_route():
    """Add all cards from the upload queue."""
    while UPLOAD_QUEUE:
        card = UPLOAD_QUEUE.pop(0)
        add_card(
            card["name"],
            card.get("set_code", ""),
            card.get("language", ""),
            card.get("condition", ""),
            0,
            card.get("quantity", 1),
            None,
            card.get("cardmarket_id", ""),
            card.get("folder_id"),
            card.get("collector_number", ""),
            card.get("scryfall_id", ""),
            card.get("image_url", ""),
        )
    flash("All queued cards added")
    return redirect(url_for("list_cards"))




if __name__ == "__main__":
    init_db()
    host = os.environ.get("FLASK_RUN_HOST", "127.0.0.1")
    port = int(os.environ.get("FLASK_RUN_PORT", 5000))
    app.run(host=host, port=port, debug=True)
