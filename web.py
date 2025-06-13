import os
import sqlite3
from queue import Queue
from flask import Flask, render_template, request, redirect, url_for, flash

from TCGInventory.lager_manager import add_card, update_card, delete_card, add_storage_slot
from TCGInventory.card_scanner import scan_and_queue, SCANNER_QUEUE
from TCGInventory.cardmarket_api import upload_card, CardmarketClient
from TCGInventory.setup_db import initialize_database
from TCGInventory import DB_FILE

app = Flask(__name__)
app.secret_key = "tcg-secret"

MKM_CLIENT = CardmarketClient.from_env()


def init_db() -> None:
    if not os.path.exists(DB_FILE):
        initialize_database()


def fetch_cards():
    with sqlite3.connect(DB_FILE) as conn:
        c = conn.cursor()
        c.execute(
            "SELECT id, name, set_code, language, condition, price, storage_code, status, cardmarket_id FROM cards"
        )
        return c.fetchall()


def get_card(card_id: int):
    with sqlite3.connect(DB_FILE) as conn:
        c = conn.cursor()
        c.execute(
            "SELECT id, name, set_code, language, condition, price, storage_code, cardmarket_id FROM cards WHERE id = ?",
            (card_id,),
        )
        return c.fetchone()


@app.route("/")
def index():
    return redirect(url_for("list_cards"))


@app.route("/cards")
def list_cards():
    cards = fetch_cards()
    return render_template("cards.html", cards=cards)


@app.route("/cards/add", methods=["GET", "POST"])
def add_card_view():
    if request.method == "POST":
        add_card(
            request.form["name"],
            request.form["set_code"],
            request.form.get("language", ""),
            request.form.get("condition", ""),
            float(request.form.get("price", 0) or 0),
            request.form["storage_code"],
            request.form.get("cardmarket_id", ""),
        )
        flash("Card added")
        return redirect(url_for("list_cards"))
    return render_template("card_form.html", card=None)


@app.route("/cards/<int:card_id>/edit", methods=["GET", "POST"])
def edit_card_view(card_id: int):
    card = get_card(card_id)
    if request.method == "POST":
        update_card(
            card_id,
            name=request.form["name"],
            set_code=request.form["set_code"],
            language=request.form.get("language", ""),
            condition=request.form.get("condition", ""),
            price=float(request.form.get("price", 0) or 0),
            storage_code=request.form["storage_code"],
            cardmarket_id=request.form.get("cardmarket_id", ""),
        )
        flash("Card updated")
        return redirect(url_for("list_cards"))
    return render_template("card_form.html", card=card)


@app.route("/cards/<int:card_id>/delete")
def delete_card_route(card_id: int):
    delete_card(card_id)
    flash("Card deleted")
    return redirect(url_for("list_cards"))


@app.route("/storage/add", methods=["GET", "POST"])
def add_storage_view():
    if request.method == "POST":
        add_storage_slot(request.form["code"])
        flash("Storage slot added")
        return redirect(url_for("list_cards"))
    return render_template("storage_form.html")


@app.route("/scan", methods=["GET", "POST"])
def scan_view():
    if request.method == "POST":
        image = request.files.get("image")
        if image:
            os.makedirs("uploads", exist_ok=True)
            path = os.path.join("uploads", image.filename)
            image.save(path)
            scan_and_queue(path)
            flash("Image scanned")
            return redirect(url_for("queue_view"))
    return render_template("scan.html")


@app.route("/queue")
def queue_view():
    items = list(SCANNER_QUEUE.queue)
    return render_template("queue.html", queue=items)


@app.route("/queue/upload/<int:index>")
def upload_card_route(index: int):
    items = list(SCANNER_QUEUE.queue)
    if 0 <= index < len(items):
        card = items[index]
        upload_card(card)
        new_q: Queue = Queue()
        for i, item in enumerate(items):
            if i != index:
                new_q.put(item)
        SCANNER_QUEUE.queue.clear()
        while not new_q.empty():
            SCANNER_QUEUE.put(new_q.get())
        flash(f"Card '{card.get('name', '')}' uploaded")
    return redirect(url_for("queue_view"))


@app.route("/update_price", methods=["GET", "POST"])
def update_price_view():
    if request.method == "POST":
        article_id = int(request.form.get("article_id", 0) or 0)
        price = float(request.form.get("new_price", 0) or 0)
        MKM_CLIENT.update_price(article_id, price)
        flash("Price updated")
        return redirect(url_for("index"))
    return render_template("update_price.html")


@app.route("/sales")
def fetch_sales():
    sales = MKM_CLIENT.fetch_sales()
    return render_template("sales.html", sales=sales)


if __name__ == "__main__":
    init_db()
    app.run(debug=True)
