# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright 2026 Kumar Muthuraman
"""The daily version check (D148, D149).

Once a day TeamRunner fetches https://version.teamrunner.ai/version.json, which names two
floors: `min_version`, below which it nudges the person to update, and `min_secure_version`,
the oldest version still considered secure, below which it refuses to run.

The request is an ordinary GET with two headers, nothing else:
    User-Agent: TeamRunner/<version> (python <major.minor>; <platform>)
    X-TeamRunner-Install: <a random id made on the first check>
No query string and no body: never team names, models, files, paths, keys, users or commands.

Every failure of the lookup is silent and costs at most TIMEOUT seconds. The only things this
module ever prints are the nudge and the refusal.
"""
from __future__ import annotations

import json
import os
import re
import sys
import threading
import urllib.request
import uuid
from datetime import datetime, timezone
from pathlib import Path

from . import __version__

URL = "https://version.teamrunner.ai/version.json"
TIMEOUT = 2.0  # seconds; a slow or absent network never stalls a start by more than this (D148 (7))
MAX_BYTES = 16_384
UPDATE = "pip install -U teamrunner"  # written here, never taken from the file (D148 (8))
_VERSION = re.compile(r"\d+(\.\d+){0,5}")
_UUID4 = re.compile(r"[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}")


def _home() -> Path:
    home = os.environ.get("TEAMRUNNER_HOME")
    return Path(home) if home else Path.home() / ".teamrunner"


def cache_path() -> Path:
    return _home() / "version-check.json"


def install_id_path() -> Path:
    return _home() / "install-id"


def parse_version(text) -> tuple[int, ...] | None:
    """The leading numbers of a version (`0.0.6rc1` → (0, 0, 6)); None if it has none."""
    if not isinstance(text, str):
        return None
    found = _VERSION.match(text.strip())
    return tuple(int(part) for part in found.group().split(".")) if found else None


def _shown(floor) -> str:
    return ".".join(map(str, parse_version(floor) or ()))


def _below(version: str, floor) -> bool:
    mine, wanted = parse_version(version), parse_version(floor)
    if mine is None or wanted is None:
        return False
    width = max(len(mine), len(wanted))
    return mine + (0,) * (width - len(mine)) < wanted + (0,) * (width - len(wanted))


def user_agent() -> str:
    return f"TeamRunner/{__version__} (python {sys.version_info[0]}.{sys.version_info[1]}; {sys.platform})"


def install_id() -> str | None:
    """A random id, made once and kept next to the cache. Tied to nothing: not the machine,
    the user or the network. Deleting it just makes a new one. None if it cannot be kept,
    so a home folder that cannot be written does not count as a new install every run."""
    path = install_id_path()
    try:
        found = path.read_text(encoding="utf-8").strip()
        if _UUID4.fullmatch(found):
            return found
    except (OSError, UnicodeError):
        pass
    made = str(uuid.uuid4())
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(made + "\n", encoding="utf-8")
    except OSError:
        return None
    return made


def _read(raw: bytes) -> dict | None:
    """The file's floors and message, or None if it is not a version file."""
    data = json.loads(raw)
    if not isinstance(data, dict):
        return None
    floors = {name: data.get(name) for name in ("min_version", "min_secure_version")}
    if any(not isinstance(value, str) or not _VERSION.fullmatch(value) for value in floors.values()):
        return None
    message = data.get("message", "")
    return {**floors, "message": message if isinstance(message, str) else ""}


def fetch() -> dict | None:
    """One GET of the version file, or None on any failure whatever. Runs in a thread so
    that nothing (not even a DNS lookup, which has no timeout of its own) waits past TIMEOUT."""
    headers = {"User-Agent": user_agent()}
    found = install_id()
    if found:
        headers["X-TeamRunner-Install"] = found
    result: list = []

    def get():
        try:
            request = urllib.request.Request(URL, headers=headers, method="GET")
            with urllib.request.urlopen(request, timeout=TIMEOUT) as response:
                result.append(_read(response.read(MAX_BYTES + 1)[:MAX_BYTES]))
        except BaseException:  # noqa: BLE001 — the lookup never says anything or raises
            pass

    worker = threading.Thread(target=get, name="teamrunner-version-check", daemon=True)
    worker.start()
    worker.join(TIMEOUT)
    return result[0] if result and not worker.is_alive() else None


def _load() -> dict:
    try:
        data = json.loads(cache_path().read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError, UnicodeError):
        return {}


def _save(state: dict) -> None:
    try:
        path = cache_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(state), encoding="utf-8")
    except OSError:
        pass


def _plain(message) -> str:
    """The file's message as one line of plain text: no control characters or terminal escapes."""
    if not isinstance(message, str):
        return ""
    text = "".join(ch if ch.isprintable() else " " for ch in message)
    return " ".join(text.split())[:300]


def today() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d")


def check(quiet: bool = False, err=None) -> int | None:
    """Run the daily check. Returns an exit code if TeamRunner must refuse to run, else None."""
    err = err or sys.stderr
    try:
        state = _load()
        if state.get("checked") != today():  # at most one request a day (D148 (7))
            fresh = fetch()
            # A failed lookup keeps what the last good one said, including a secure floor (D148 (5)).
            state = {**state, **(fresh or {}), "checked": today()}
            _save(state)
    except Exception:  # noqa: BLE001 — a broken cache or clock never stops a run
        return None
    # Refuse to run below min_secure_version: that version has a known security problem, and
    # running it could put the person's keys, files or machine at risk. This is a lock, not a
    # nudge: TEAMRUNNER_NO_VERSION_CHECK does not turn it off, and a copy that has once seen a
    # floor above itself keeps refusing offline until it is updated (D148 (4), (5)).
    if _below(__version__, state.get("min_secure_version")):
        message = _plain(state.get("message"))
        print(f"✗ TeamRunner {__version__} has a known security problem and will not run. "
              f"Version {_shown(state['min_secure_version'])} or later is needed.", file=err)
        if message:
            print(f"  {message}", file=err)
        print(f"  Update with: {UPDATE}", file=err)
        return 1
    # The nudge only; the request above still goes, so a secure floor still arrives (D149).
    if (_below(__version__, state.get("min_version")) and not quiet
            and os.environ.get("TEAMRUNNER_NO_VERSION_CHECK", "").strip() not in {"1", "true", "yes"}):
        message = _plain(state.get("message"))
        print(f"↑ A newer TeamRunner is wanted: version {_shown(state['min_version'])} or later "
              f"(you have {__version__}).{' ' + message if message else ''}", file=err)
        print(f"  Update with: {UPDATE}   (TEAMRUNNER_NO_VERSION_CHECK=1 hides this)", file=err)
    return None
