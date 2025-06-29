import subprocess
from typing import Tuple


def update_repo() -> Tuple[bool, str]:
    """Fetch updates from the git repository and pull if needed.

    Local modifications are stashed before pulling and restored afterwards so
    the update succeeds even when the working tree is dirty.

    Returns a tuple ``(success, message)`` describing the outcome.
    """
    fetch = subprocess.run(["git", "fetch"], capture_output=True, text=True)
    if fetch.returncode != 0:
        return False, (fetch.stderr or fetch.stdout).strip()

    rev = subprocess.run(
        ["git", "rev-list", "HEAD..@{u}", "--count"],
        capture_output=True,
        text=True,
    )
    if rev.returncode != 0:
        return False, (rev.stderr or rev.stdout).strip()

    if rev.stdout.strip() == "0":
        return True, "Repository already up to date."

    status = subprocess.run(["git", "status", "--porcelain"], capture_output=True, text=True)
    if status.returncode != 0:
        return False, (status.stderr or status.stdout).strip()

    need_stash = bool(status.stdout.strip())
    if need_stash:
        stash = subprocess.run(["git", "stash", "--include-untracked"], capture_output=True, text=True)
        if stash.returncode != 0:
            return False, (stash.stderr or stash.stdout).strip()

    pull = subprocess.run(["git", "pull"], capture_output=True, text=True)
    if pull.returncode != 0:
        if need_stash:
            subprocess.run(["git", "stash", "pop"], capture_output=True, text=True)
        return False, (pull.stderr or pull.stdout).strip()

    if need_stash:
        pop = subprocess.run(["git", "stash", "pop"], capture_output=True, text=True)
        if pop.returncode != 0:
            return False, (pop.stderr or pop.stdout).strip()

    return True, pull.stdout.strip()
