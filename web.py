import os
import sqlite3
from flask import Flask, render_template, request, redirect, url_for, flash

from TCGInventory.lager_manager import (
    add_card,
    update_card,
    delete_card,
    add_storage_slot,
)
from TCGInventory.setup_db import initialize_database
from TCGInventory import DB_FILE

app = Flask(__name__)
app.secret_key = os.environ.get("FLASK_SECRET_KEY", "tcg-secret")


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
            request.form.get("storage_code", ""),
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
            storage_code=request.form.get("storage_code", ""),
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




if __name__ == "__main__":
    init_db()
    app.run(debug=True)
