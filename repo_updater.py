import subprocess
from typing import Tuple


def update_repo() -> Tuple[bool, str]:
    """Fetch updates from the git repository and pull if needed.

    Returns a tuple ``(success, message)`` describing the outcome.
    """
    fetch = subprocess.run([
        "git",
        "fetch",
    ], capture_output=True, text=True)
    if fetch.returncode != 0:
        return False, (fetch.stderr or fetch.stdout).strip()

    rev = subprocess.run(
        ["git", "rev-list", "HEAD..@{u}", "--count"],
        capture_output=True,
        text=True,
    )
    if rev.returncode != 0:
        return False, (rev.stderr or rev.stdout).strip()

    if rev.stdout.strip() != "0":
        pull = subprocess.run([
            "git",
            "pull",
        ], capture_output=True, text=True)
        if pull.returncode != 0:
            return False, (pull.stderr or pull.stdout).strip()
        return True, pull.stdout.strip()

    return True, "Repository already up to date."
