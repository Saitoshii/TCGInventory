import os
import sqlite3
import hashlib
import hmac
import binascii
from functools import wraps

from . import DB_FILE

HASH_ITERATIONS = 100_000


def init_user_db():
    """Ensure the users table exists."""
    with sqlite3.connect(DB_FILE) as conn:
        c = conn.cursor()
        c.execute(
            """
            CREATE TABLE IF NOT EXISTS users (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                username TEXT UNIQUE,
                password_hash TEXT
            )
            """
        )
        conn.commit()


def user_exists() -> bool:
    with sqlite3.connect(DB_FILE) as conn:
        c = conn.cursor()
        c.execute("SELECT COUNT(*) FROM users")
        return c.fetchone()[0] > 0


def get_password_hash(username: str) -> str | None:
    with sqlite3.connect(DB_FILE) as conn:
        c = conn.cursor()
        c.execute(
            "SELECT password_hash FROM users WHERE username=?",
            (username,),
        )
        row = c.fetchone()
        return row[0] if row else None


def register_user(username: str, password: str) -> None:
    pw_hash = hash_password(password)
    with sqlite3.connect(DB_FILE) as conn:
        c = conn.cursor()
        c.execute(
            "INSERT INTO users (username, password_hash) VALUES (?, ?)",
            (username, pw_hash),
        )
        conn.commit()


def hash_password(password: str) -> str:
    salt = os.urandom(16)
    hashed = hashlib.pbkdf2_hmac(
        "sha256", password.encode(), salt, HASH_ITERATIONS
    )
    return (
        binascii.hexlify(salt).decode()
        + "$"
        + binascii.hexlify(hashed).decode()
    )


def verify_password(stored: str | None, provided: str) -> bool:
    if not stored:
        return False
    try:
        salt_hex, hash_hex = stored.split("$")
    except ValueError:
        return False
    salt = binascii.unhexlify(salt_hex)
    expected = binascii.unhexlify(hash_hex)
    hashed = hashlib.pbkdf2_hmac(
        "sha256", provided.encode(), salt, HASH_ITERATIONS
    )
    return hmac.compare_digest(hashed, expected)


def verify_user(username: str, password: str) -> bool:
    return verify_password(get_password_hash(username), password)


# Flask helper
def login_required(f):
    from flask import session, redirect, url_for, request

    @wraps(f)
    def wrapper(*args, **kwargs):
        if not session.get("user"):
            return redirect(url_for("login", next=request.path))
        return f(*args, **kwargs)

    return wrapper
