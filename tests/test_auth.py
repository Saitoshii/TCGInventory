import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(__file__))))
import TCGInventory.auth as auth  # noqa: E402


def test_hash_and_verify():
    pw = "secret"
    hashed = auth.hash_password(pw)
    assert auth.verify_password(hashed, pw)
    assert not auth.verify_password(hashed, "wrong")
