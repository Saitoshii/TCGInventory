import os
import sqlite3
import re
import threading
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
    edit_folder,
    delete_folder,
    create_binder,
    list_folders,
)
from TCGInventory.card_scanner import (
    fetch_card_info_by_name,
    autocomplete_names,
    fetch_variants,
    find_variant,
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
from TCGInventory.repo_updater import update_repo
from TCGInventory.order_service import get_order_service
from datetime import timedelta
from pathlib import Path
from werkzeug.utils import secure_filename

app = Flask(__name__)
app.secret_key = os.environ.get("FLASK_SECRET_KEY", "tcg-secret")
app.permanent_session_lifetime = timedelta(minutes=15)
# Maximum file size for database uploads: 500 MB
app.config['MAX_CONTENT_LENGTH'] = 500 * 1024 * 1024

# Queue for cards uploaded via the bulk add feature
UPLOAD_QUEUE: list[dict] = []

# Progress tracking for bulk uploads
BULK_PROGRESS = 0
BULK_DONE = False
BULK_MESSAGE: str | None = None


def make_storage_code(
    folder_id: str | None, page: str | None, slot: str | None
) -> str:
    """Return a formatted storage code or an empty string."""
    if folder_id and page and slot:
        try:
            return f"O{int(folder_id):02d}-S{int(page):02d}-P{int(slot)}"
        except ValueError:
            pass
    return ""


def init_db() -> None:
    if not os.path.exists(DB_FILE):
        initialize_database()
    else:
        init_user_db()


def fetch_cards(search: str | None = None, folder_id: int | None = None):
    """Return card rows optionally filtered by search term and folder."""
    with sqlite3.connect(DB_FILE) as conn:
        c = conn.cursor()
        query = (
            "SELECT cards.id, cards.name, cards.set_code, cards.language, "
            "cards.condition, cards.price, cards.quantity, cards.storage_code, "
            "COALESCE(folders.name, ''), cards.status, cards.image_url, cards.foil, "
            "cards.collector_number FROM cards LEFT JOIN folders ON cards.folder_id = folders.id"
        )
        conditions = []
        params: list[str | int] = []
        if search:
            like = f"%{search}%"
            conditions.append(
                "(cards.name LIKE ? OR cards.set_code LIKE ? OR cards.collector_number LIKE ?)"
            )
            params.extend([like, like, like])
        if folder_id is not None:
            conditions.append("cards.folder_id = ?")
            params.append(folder_id)
        if conditions:
            query += " WHERE " + " AND ".join(conditions)
        c.execute(query, tuple(params))
        return c.fetchall()


def get_card(card_id: int):
    with sqlite3.connect(DB_FILE) as conn:
        c = conn.cursor()
        c.execute(
            "SELECT id, name, set_code, language, condition, price, quantity, "
            "storage_code, cardmarket_id, folder_id, collector_number, "
            "scryfall_id, image_url, foil FROM cards WHERE id = ?",
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
    folder = request.args.get("folder")
    fid = int(folder) if folder and folder.isdigit() else None
    cards = fetch_cards(search if search else None, fid)
    return render_template("cards.html", cards=cards, search=search, folder=fid)


@app.route("/cards/export")
@login_required
def export_cards():
    """Return a CSV export of all cards or a single folder."""
    folder = request.args.get("folder")
    fid = int(folder) if folder and folder.isdigit() else None
    rows = fetch_cards(folder_id=fid)
    output = io.StringIO()
    writer = csv.writer(output, delimiter=";")
    writer.writerow(
        [
            "Collector Number",
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
        ]
    )
    for row in rows:
        writer.writerow([
            row[12],
            row[1],
            row[2],
            row[3],
            row[4],
            row[5],
            row[6],
            row[7],
            row[8],
            row[9],
            row[10],
        ])
    resp = Response(output.getvalue(), mimetype="text/csv")
    filename = "inventory.csv" if fid is None else f"folder_{fid}.csv"
    resp.headers["Content-Disposition"] = f"attachment; filename={filename}"
    return resp


def _parse_csv_bytes(csv_bytes: bytes) -> list[dict]:
    """
    Decode CSV bytes and return a list of dictionaries.

    Handles CSV files with a leading separator directive line (e.g., sep=, or "sep=,")
    which is commonly inserted by Excel and other tools. The directive may be quoted
    and may appear after a UTF-8 BOM.

    Args:
        csv_bytes: Raw CSV file content as bytes.

    Returns:
        A list of dicts where keys are the normalized header column names.

    Raises:
        ValueError: If the CSV cannot be parsed due to encoding or format issues.
    """
    # Decode using utf-8-sig (handles BOM) with latin-1 fallback
    try:
        content = csv_bytes.decode("utf-8-sig")
    except UnicodeDecodeError:
        try:
            content = csv_bytes.decode("latin-1")
        except UnicodeDecodeError as e:
            raise ValueError(
                f"Unable to decode CSV file. Please ensure it uses UTF-8 or Latin-1 encoding: {e}"
            )

    lines = content.splitlines()
    if not lines:
        return []

    delimiter = None

    # Check for a leading separator directive, which may be quoted.
    # Common variants:
    #   sep=,      (unquoted)
    #   "sep=,"    (quoted with delimiter inside, matching quotes)
    #   'sep=,'    (single-quoted variant, matching quotes)
    first_line = lines[0].strip()
    # Use alternation to enforce matching quotes: unquoted, double-quoted, or single-quoted
    sep_pattern = re.compile(
        r'^(?:sep=(.)$|"sep=(.)"$|\'sep=(.)\')$', re.IGNORECASE
    )
    match = sep_pattern.match(first_line)
    if match:
        # Found a separator directive; extract delimiter (from whichever group matched)
        delimiter = match.group(1) or match.group(2) or match.group(3)
        content = "\n".join(lines[1:])

    # Attempt to detect the CSV dialect using Sniffer
    try:
        dialect = csv.Sniffer().sniff(content, delimiters=delimiter or ",;")
    except csv.Error:
        # Fall back to Excel dialect if sniffing fails
        # Create a mutable dialect subclass since csv.excel is immutable
        class FallbackDialect(csv.excel):
            pass
        dialect = FallbackDialect
        if delimiter:
            dialect.delimiter = delimiter

    try:
        reader = csv.DictReader(io.StringIO(content), dialect=dialect)
        rows = list(reader)
    except csv.Error as e:
        raise ValueError(
            f"CSV parsing failed. Please check that the file is valid CSV with a proper header row: {e}"
        )

    return rows


def _process_bulk_upload(form_data: dict, json_bytes: bytes | None, csv_bytes: bytes | None) -> None:
    """Background task to process bulk upload files."""
    global BULK_PROGRESS, BULK_DONE, BULK_MESSAGE
    try:
        folders = list_folders()
        folder_id = form_data.get("folder_id")
        set_code = ""
        for f in folders:
            if str(f[0]) == str(folder_id):
                set_code = f[1]
                break

        entries: list[tuple[str, object]] = []

        if json_bytes:
            import json

            data = json.loads(json_bytes.decode("utf-8-sig"))
            if isinstance(data, list):
                for item in data:
                    entries.append(("json", item))

        if csv_bytes:
            try:
                csv_rows = _parse_csv_bytes(csv_bytes)
                for row in csv_rows:
                    entries.append(("csv", row))
            except ValueError as e:
                # Record error but continue processing other inputs
                BULK_MESSAGE = str(e)

        for name in form_data.get("cards", "").splitlines():
            name = name.strip()
            if name:
                entries.append(("text", name))

        total = len(entries) if entries else 1
        added_any = False

        for idx, (kind, item) in enumerate(entries):
            if kind == "json":
                entry = item
                name = entry.get("name") if isinstance(entry, dict) else entry if isinstance(entry, str) else None
                if not name:
                    BULK_PROGRESS = int((idx + 1) / total * 100)
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
                        "foil": False,
                    }
                )
                added_any = True
            elif kind == "csv":
                row = item
                normalized = {}
                for k, v in row.items():
                    if not k:
                        continue
                    if isinstance(v, list):
                        v = v[0] if v else ""
                    normalized[k.strip().lower().replace(" ", "_")] = (v or "").strip()
                name = normalized.get("card_name", "")
                if not name:
                    BULK_PROGRESS = int((idx + 1) / total * 100)
                    continue
                qty = int(normalized.get("quantity", "1") or 1)
                set_row = normalized.get("set_code", "")
                card_no = normalized.get("card_number", "")
                language = normalized.get("language", "")
                condition = normalized.get("condition", "")
                foil_flag = normalized.get("foil", "").lower() in {"1", "true", "yes", "foil"}
                info = fetch_card_info_by_name(name)
                variant = None
                if set_row:
                    variant = find_variant(name, set_row, card_no or None)
                if not variant and info and card_no and info.get("collector_number") != card_no:
                    variant = find_variant(name, set_row or info.get("set_code", ""), card_no)
                if variant:
                    info = variant
                    if not card_no:
                        card_no = variant.get("collector_number", card_no)
                    set_row = variant.get("set_code", set_row)

                elif info and not card_no:
                    card_no = info.get("collector_number", "")

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
                        "foil": foil_flag,
                    }
                )
                added_any = True
            else:
                name = item
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
                        "foil": False,
                    }
                )
                added_any = True

            BULK_PROGRESS = int((idx + 1) / total * 100)

        BULK_PROGRESS = 100
        BULK_DONE = True
        BULK_MESSAGE = "Cards queued for review" if added_any else "No cards added"
    except Exception as exc:
        BULK_DONE = True
        BULK_PROGRESS = 100
        BULK_MESSAGE = f"Error processing upload: {exc}"



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
        storage_code = make_storage_code(folder_id, page, slot)
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
            bool(request.form.get("foil")),
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
        storage_code = make_storage_code(folder_id, page, slot)
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
            foil=bool(request.form.get("foil")),
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
        "card_form.html",
        card=card,
        folders=folders,
        folder_part=folder_part,
        page=page,
        slot=slot,
    )


@app.route("/cards/<int:card_id>/delete")
@login_required
def delete_card_route(card_id: int):
    delete_card(card_id)
    flash("Card deleted")
    return redirect(url_for("list_cards"))


@app.route("/cards/<int:card_id>/sell", methods=["POST"])
@login_required
def sell_card_route(card_id: int):
    """Decrease card quantity by one or delete if none remain."""
    card = get_card(card_id)
    if not card:
        return jsonify({"error": "not found"}), 404
    qty = card[6] or 0
    if qty > 1:
        update_card(card_id, quantity=qty - 1)
        return jsonify({"quantity": qty - 1, "removed": False})
    delete_card(card_id)
    flash("Card sold")
    return jsonify({"quantity": 0, "removed": True})


@app.route("/folders/delete/<int:folder_id>")
@login_required
def delete_folder_view(folder_id: int):
    delete_folder(folder_id)
    flash("Folder deleted")
    return redirect(url_for("list_folders_view"))


@app.route("/folders")
@login_required
def list_folders_view():
    sort = request.args.get("sort", "name")
    search = request.args.get("q", "").strip()

    allowed = {"id": "collector_number", "storage": "storage_code", "name": "name"}
    order_col = allowed.get(sort, "name")

    folders = list_folders()
    folder_cards = {}
    with sqlite3.connect(DB_FILE) as conn:
        c = conn.cursor()
        for fid, _, _ in folders:
            query = (
                "SELECT id, name, set_code, quantity, storage_code, collector_number, foil FROM cards "
                "WHERE folder_id=?"
            )
            params = [fid]
            if search:
                query += (
                    " AND (collector_number LIKE ? OR storage_code LIKE ? OR name LIKE ?)"
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
        pages = int(request.form.get("pages", "1"))
        folder_id = add_folder(name, pages)
        if folder_id is not None:
            create_binder(folder_id, pages)
        flash("Folder added")
        return redirect(url_for("list_folders_view"))
    return render_template("folder_form.html")


@app.route("/folders/edit/<int:folder_id>", methods=["GET", "POST"])
@login_required
def edit_folder_view(folder_id: int):
    with sqlite3.connect(DB_FILE) as conn:
        c = conn.cursor()
        c.execute("SELECT id, name, pages FROM folders WHERE id=?", (folder_id,))
        folder = c.fetchone()
    if not folder:
        flash("Folder not found", "error")
        return redirect(url_for("list_folders_view"))
    if request.method == "POST":
        pages = int(request.form.get("pages", folder[2] or 0))
        new_id_val = request.form.get("id")
        new_id = int(new_id_val) if new_id_val and new_id_val.isdigit() else None
        edit_folder(folder_id, request.form["name"], pages, new_id)
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
        global BULK_PROGRESS, BULK_DONE, BULK_MESSAGE
        BULK_PROGRESS = 0
        BULK_DONE = False
        BULK_MESSAGE = None

        form_data = request.form.to_dict()
        json_file = request.files.get("json_file")




        if json_file and json_file.filename:
            try:
                import json

                data = json.load(json_file)
                if isinstance(data, list):
                    for entry in data:
                        if isinstance(entry, dict):
                            name = entry.get("name")
                            set_row = entry.get("set_code", "")
                            card_no = entry.get("collector_number", "")
                            language = entry.get("language", "")
                            condition = entry.get("condition", "")
                            qty = int(entry.get("quantity", 1) or 1)
                            foil_flag = str(entry.get("foil", "")).lower() in {
                                "1",
                                "true",
                                "yes",
                                "foil",
                            }
                        else:
                            name = str(entry)
                            set_row = ""
                            card_no = ""
                            language = ""
                            condition = ""
                            qty = 1
                            foil_flag = False
                        if not name:
                            continue
                        info = fetch_card_info_by_name(name)
                        variant = None
                        if set_row:
                            variant = find_variant(name, set_row, card_no or None)
                        if not variant and info and card_no and info.get("collector_number") != card_no:
                            variant = find_variant(
                                name,
                                set_row or info.get("set_code", ""),
                                card_no,
                            )
                        if variant:
                            info = variant
                            if not card_no:
                                card_no = variant.get("collector_number", card_no)
                            set_row = variant.get("set_code", set_row)
                        elif info and not card_no:
                            card_no = info.get("collector_number", "")
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
                                "foil": foil_flag,
                            }
                        )
                        added_any = True
            except Exception:
                flash("Invalid JSON file")

        # handle uploaded CSV file


        csv_file = request.files.get("csv_file")
        json_bytes = json_file.read() if json_file and json_file.filename else None
        csv_bytes = csv_file.read() if csv_file and csv_file.filename else None

        threading.Thread(
            target=_process_bulk_upload,
            args=(form_data, json_bytes, csv_bytes),
            daemon=True,
        ).start()

        return render_template("bulk_add_progress.html")
    return render_template("bulk_add.html", folders=folders)

@app.route("/cards/bulk_add/progress")
@login_required
def bulk_add_progress():
    global BULK_PROGRESS, BULK_DONE, BULK_MESSAGE
    if BULK_DONE and BULK_MESSAGE:
        flash(BULK_MESSAGE)
        BULK_MESSAGE = None
    return jsonify({"percent": int(BULK_PROGRESS), "done": BULK_DONE})



@app.route("/cards/upload_queue")
@login_required
def upload_queue_view():
    """Display queued cards from the bulk upload."""
    search = request.args.get("q", "").strip()
    enumerated_queue = list(enumerate(UPLOAD_QUEUE))
    if search:
        enumerated_queue = [
            (i, c)
            for i, c in enumerated_queue
            if search.lower() in c.get("name", "").lower()
        ]
    return render_template("upload_queue.html", queue=enumerated_queue, search=search)


@app.route("/cards/upload_queue/foil/<int:index>", methods=["POST"])
@login_required
def toggle_queued_foil(index: int):
    """Toggle the foil flag for a queued card."""
    if 0 <= index < len(UPLOAD_QUEUE):
        UPLOAD_QUEUE[index]["foil"] = bool(request.form.get("foil"))
    return ("", 204)


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
                "foil": bool(request.form.get("foil")),
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
        int(card.get("foil", 0)),
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
            card.get("foil", False),
        )
        flash(
            "Card added" if success else "No slot for " + card["name"],
            "error" if not success else None,
        )
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
            card.get("foil", False),
        )
    flash("All queued cards added")
    return redirect(url_for("list_cards"))


@app.route("/cards/upload_queue/clear")
@login_required
def clear_upload_queue():
    """Remove all cards from the upload queue."""
    UPLOAD_QUEUE.clear()
    flash("Upload queue cleared")
    return redirect(url_for("upload_queue_view"))


@app.route("/update")
@login_required
def update_view():
    """Fetch updates from the git repository."""
    success, message = update_repo()
    flash(message, "error" if not success else None)
    return redirect(url_for("index"))


@app.route("/upload_database", methods=["GET", "POST"])
@login_required
def upload_database():
    """Upload and replace the default-cards.db file."""
    data_dir = Path(__file__).resolve().parent / "data"
    db_path = data_dir / "default-cards.db"
    
    # Check if current file exists and get its size
    current_file_exists = db_path.exists()
    current_file_size = ""
    if current_file_exists:
        size_bytes = db_path.stat().st_size
        if size_bytes < 1024:
            current_file_size = f"{size_bytes} bytes"
        elif size_bytes < 1024 * 1024:
            current_file_size = f"{size_bytes / 1024:.2f} KB"
        else:
            current_file_size = f"{size_bytes / (1024 * 1024):.2f} MB"
    
    if request.method == "POST":
        # Check if file was uploaded
        if "db_file" not in request.files:
            flash("No file selected", "error")
            return redirect(request.url)
        
        file = request.files["db_file"]
        
        # Check if filename is empty
        if file.filename == "":
            flash("No file selected", "error")
            return redirect(request.url)
        
        # Check if confirmation checkbox is checked
        if not request.form.get("confirm"):
            flash("Please confirm that you want to replace the database", "error")
            return redirect(request.url)
        
        # Validate filename
        filename = secure_filename(file.filename)
        if filename != "default-cards.db":
            flash("File must be named 'default-cards.db'", "error")
            return redirect(request.url)
        
        try:
            # Ensure data directory exists
            data_dir.mkdir(exist_ok=True)
            
            # Save to temporary file first for validation
            temp_path = db_path.with_suffix('.db.tmp')
            file.save(str(temp_path))
            
            # Verify it's a valid SQLite database
            try:
                with sqlite3.connect(str(temp_path)) as conn:
                    cursor = conn.cursor()
                    # Check if it has a cards table
                    cursor.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='cards'")
                    if not cursor.fetchone():
                        # Remove invalid file
                        temp_path.unlink()
                        flash("Invalid database file: missing 'cards' table", "error")
                        return redirect(request.url)
            except sqlite3.Error as e:
                # Remove invalid file
                if temp_path.exists():
                    temp_path.unlink()
                flash(f"Invalid SQLite database file: {e}", "error")
                return redirect(request.url)
            
            # Validation passed, replace the actual database file
            if db_path.exists():
                db_path.unlink()
            temp_path.rename(db_path)
            
            flash("Database file uploaded successfully!", "success")
            return redirect(url_for("upload_database"))
            
        except Exception as e:
            flash(f"Error uploading file: {e}", "error")
            return redirect(request.url)
    
    return render_template(
        "upload_db.html",
        current_file_exists=current_file_exists,
        current_file_size=current_file_size
    )


@app.route("/orders")
@login_required
def list_orders():
    """Display open orders from Cardmarket emails."""
    with sqlite3.connect(DB_FILE) as conn:
        c = conn.cursor()
        c.execute(
            """
            SELECT o.id, o.buyer_name, o.date_received, o.status 
            FROM orders o 
            WHERE o.status = 'open'
            ORDER BY o.date_received DESC
            """
        )
        orders = c.fetchall()
        
        # Get items for each order
        order_details = []
        for order in orders:
            order_id = order[0]
            c.execute(
                """
                SELECT id, card_name, quantity, image_url, storage_code
                FROM order_items
                WHERE order_id = ?
                ORDER BY card_name
                """,
                (order_id,)
            )
            items = c.fetchall()
            order_details.append({
                'id': order[0],
                'buyer_name': order[1],
                'date_received': order[2],
                'status': order[3],
                'items': items
            })
    
    # Get service status
    service = get_order_service()
    polling_enabled = service.is_enabled()
    
    return render_template(
        "orders.html",
        orders=order_details,
        polling_enabled=polling_enabled
    )


@app.route("/orders/<int:order_id>/mark_sold", methods=["POST"])
@login_required
def mark_order_sold(order_id: int):
    """Mark an order as sold/completed."""
    from datetime import datetime
    
    with sqlite3.connect(DB_FILE) as conn:
        c = conn.cursor()
        c.execute(
            """
            UPDATE orders 
            SET status = 'sold', date_completed = ?
            WHERE id = ?
            """,
            (datetime.now().isoformat(), order_id)
        )
        conn.commit()
    
    flash("Order marked as sold")
    return redirect(url_for("list_orders"))


@app.route("/orders/sync", methods=["POST"])
@login_required
def sync_orders():
    """Manually trigger order synchronization."""
    service = get_order_service()
    success, message, count = service.sync_orders()
    
    if success:
        flash(message)
    else:
        flash(message, "error")
    
    return redirect(url_for("list_orders"))


@app.route("/orders/toggle_polling", methods=["POST"])
@login_required
def toggle_order_polling():
    """Enable or disable automatic order polling."""
    service = get_order_service()
    
    action = request.form.get("action")
    if action == "enable":
        service.enable()
        flash("Order polling enabled")
    elif action == "disable":
        service.disable()
        flash("Order polling disabled")
    
    return redirect(url_for("list_orders"))


if __name__ == "__main__":
    init_db()
    
    # Start the order ingestion service
    order_service = get_order_service()
    order_service.start()
    
    host = os.environ.get("FLASK_RUN_HOST", "0.0.0.0")
    port = int(os.environ.get("FLASK_RUN_PORT", 5000))
    app.run(host=host, port=port, debug=True)
