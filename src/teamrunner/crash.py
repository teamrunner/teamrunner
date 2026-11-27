# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright 2026 Kumar Muthuraman
"""Crashes (D165): what TeamRunner does when it hits an error it did not foresee.

Three things, all here so the command line and the browser page do the same:

1. The full details (a traceback, keys hidden) are kept on this machine, in
   ~/.teamrunner/last-error.txt, for the person to read and attach if they choose.
2. One small report is sent to https://version.teamrunner.ai/crash, with the version
   check's two headers and three fields, nothing else:
       {"type": "<the exception's type>", "file": "<file in TeamRunner>", "line": <line>}
   Never the error's message, a traceback, a path of the person's, a team, model, file,
   key, argument or command. TEAMRUNNER_NO_CRASH_REPORTS=1 turns it off.
3. `teamrunner report`, and a button in the browser page, open a GitHub issue filled in
   with the same fields. Nothing is posted until the person presses Submit.

Sending is silent, costs at most TIMEOUT seconds and happens at most DAILY times a day.
"""
from __future__ import annotations

import json
import os
import platform
import re
import sys
import threading
import traceback
import urllib.request
from pathlib import Path
from urllib.parse import quote

from . import __version__, version_check

URL = "https://version.teamrunner.ai/crash"
TIMEOUT = 2.0  # seconds
DAILY = 5      # reports a day from one install: a crash in a loop cannot flood the log
OFF = "TEAMRUNNER_NO_CRASH_REPORTS"
ISSUES = "https://github.com/teamrunner/teamrunner/issues/new"
HELLO = "hello@teamrunner.ai"
PACKAGE = Path(__file__).resolve().parent
_TYPE = re.compile(r"[A-Za-z_][A-Za-z0-9_.]{0,99}")
SENT = ("A crash report was sent to TeamRunner: its version, your system, the kind of error and "
        f"where in TeamRunner's code it happened; nothing of yours. {OFF}=1 stops this.")


def details_path() -> Path:
    return version_check._home() / "last-error.txt"


def last_path() -> Path:
    return version_check._home() / "last-crash.json"


def _count_path() -> Path:
    return version_check._home() / "crash-reports.json"


def off() -> bool:
    # Any value but an explicit "no" turns them off: `on` and `off` both mean the person
    # set the switch, and sending after that would be the wrong way to guess (NS8).
    from .keys import switch
    return switch(OFF) not in {"", "0", "false", "no"}


def kind(error: BaseException) -> str:
    """The exception's type, if it is Python's, TeamRunner's or an installed package's;
    `other` for a type defined anywhere else, whose name could be the person's own."""
    cls = type(error)
    module = cls.__module__ or ""
    top = module.split(".")[0]
    if top == "builtins":
        name = cls.__qualname__
    else:
        name = f"{module}.{cls.__qualname__}"
        file = getattr(sys.modules.get(module), "__file__", None) or ""
        packaged = any(part in {"site-packages", "dist-packages"} for part in Path(file).parts)
        if not (top == "teamrunner" or top in sys.stdlib_module_names or packaged):
            return "other"
    return name if _TYPE.fullmatch(name) else "other"


def place(tb) -> tuple[str, int]:
    """The innermost line of TeamRunner's own code in a traceback, as (file, line), the file
    relative to the package (`ui/jobs.py`). ("outside", 0) if none of it is TeamRunner's."""
    found = ("outside", 0)
    for frame in traceback.extract_tb(tb):
        try:
            inside = Path(frame.filename).resolve().relative_to(PACKAGE)
        except (ValueError, OSError):
            continue
        found = (inside.as_posix(), frame.lineno or 0)
    return found


def fields(error: BaseException) -> dict:
    """Everything a crash report says about the crash itself."""
    file, line = place(error.__traceback__)
    return {"type": kind(error), "file": file, "line": line}


def what(crash: dict) -> str:
    """A crash in a few words: `KeyError at ui/jobs.py line 281`."""
    where = "outside TeamRunner's code" if crash["file"] == "outside" else f"at {crash['file']} line {crash['line']}"
    return f"{crash['type']} {where}"


def system() -> str:
    return f"{platform.system()} {platform.release()}, Python {platform.python_version()}"


def report_url(crash: dict | None = None) -> str:
    """A GitHub bug report, filled in. A link the person opens and reads before posting."""
    version = f"teamrunner {__version__}"
    url = f"{ISSUES}?template=bug_report.yml&version={quote(version)}&system={quote(system())}"
    if crash:
        text = (f"TeamRunner hit a problem it did not expect: {what(crash)}.\n\n"
                "What I was doing when it happened:\n\n")
        url += f"&what={quote(text)}"
    return url


def mail_url(crash: dict | None = None) -> str:
    body = f"\n\nteamrunner {__version__}\n{system()}" + (f"\n{what(crash)}" if crash else "")
    return f"mailto:{HELLO}?subject={quote('TeamRunner problem')}&body={quote(body)}"


def post(crash: dict) -> bool:
    """One POST of the three fields; True if the server took it. Never raises, and never
    waits past TIMEOUT (in a thread, as the version check does, since DNS has no timeout)."""
    headers = {"User-Agent": version_check.user_agent(), "Content-Type": "application/json"}
    found = version_check.install_id()
    if found:
        headers["X-TeamRunner-Install"] = found
    body = json.dumps({"type": crash["type"], "file": crash["file"], "line": crash["line"]}).encode()
    result: list = []

    def send():
        try:
            request = urllib.request.Request(URL, data=body, headers=headers, method="POST")
            with urllib.request.urlopen(request, timeout=TIMEOUT) as response:
                result.append(200 <= response.status < 300)
        except BaseException:  # noqa: BLE001 — a report never says anything or raises
            pass

    worker = threading.Thread(target=send, name="teamrunner-crash-report", daemon=True)
    worker.start()
    worker.join(TIMEOUT)
    return bool(result and result[0])


def _allowed_today() -> bool:
    """Count this report against today's DAILY; False once they are used up, or if the
    count cannot be kept (so an unwritable home does not send without limit)."""
    path, day = _count_path(), version_check.today()
    try:
        state = json.loads(path.read_text(encoding="utf-8"))
        sent = state["sent"] if state.get("day") == day and isinstance(state.get("sent"), int) else 0
    except (OSError, ValueError, KeyError, TypeError, AttributeError, UnicodeError):
        sent = 0
    if sent >= DAILY:
        return False
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps({"day": day, "sent": sent + 1}), encoding="utf-8")
    except OSError:
        return False
    return True


def send(crash: dict) -> bool:
    """Send the report unless it is turned off or today's are used up. True if it arrived."""
    try:
        if off() or not _allowed_today():
            return False
        return post(crash)
    except Exception:  # noqa: BLE001
        return False


_SECRET_NAME = re.compile(r"(KEY|TOKEN|SECRET|PASSWORD|PASSWD|CREDENTIAL)", re.I)


def _shell_secrets() -> list[str]:
    """Values in the environment that are keys by their names: a provider's or a tool's key,
    or any name with KEY, TOKEN, SECRET or PASSWORD in it. Short values are left: hiding
    `1` or `true` wherever it appears would spoil the details."""
    from .builtin_tools import TOOL_KEYS
    from .keys import PROVIDER_KEYS
    known = set(PROVIDER_KEYS.values()) | set(TOOL_KEYS.values())
    return [value for name, value in os.environ.items()
            if len(value) >= 8 and (name in known or _SECRET_NAME.search(name))]


def _write_private(path: Path, text: str) -> None:
    """Written for the owner alone: the details hold a command line and a traceback, with
    the person's paths in them (NS8). Not on Windows, where the profile folder does this."""
    path.unlink(missing_ok=True)
    handle = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(handle, "w", encoding="utf-8") as stream:
        stream.write(text)


def record(error: BaseException, command: str = "", secrets=()) -> dict:
    """Keep a crash's details on this machine and send the small report. Returns the three
    fields plus `sent` (the report arrived), `kept` (the details file, or None) and `report`
    (the filled-in issue). `secrets` are hidden in the kept details, beside the key file's
    keys. Never raises: a crash handler that crashes helps nobody."""
    try:
        crash = fields(error)
    except Exception:  # noqa: BLE001
        crash = {"type": "other", "file": "outside", "line": 0}
    kept = None
    try:
        from .keys import hide, stored_secrets
        text = "".join(traceback.format_exception(type(error), error, error.__traceback__))
        stored = stored_secrets()
        # The command line is kept too, and a key may be only in the shell, never in the key
        # file: both are hidden, because `teamrunner report` invites pasting from this file.
        text = hide(f"teamrunner {__version__}\n{command}\n\n{text}",
                    [*stored, *_shell_secrets(), *(s for s in secrets if isinstance(s, str))])
        path = details_path()
        path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        _write_private(path, text)
        kept = path
    except Exception:  # noqa: BLE001
        pass
    try:
        _write_private(last_path(), json.dumps(crash))
    except OSError:
        pass
    return {**crash, "sent": send(crash), "kept": kept, "report": report_url(crash)}


def shown(crash: dict) -> dict:
    """What the browser page is told about a crash, for its "Report this problem" button."""
    return {"what": what(crash), "report": crash["report"], "mail": mail_url(crash), "email": HELLO,
            "sent": crash["sent"], "off": OFF, "details": str(crash["kept"]) if crash["kept"] else None}


def last() -> dict | None:
    """The last crash recorded on this machine, or None."""
    try:
        crash = json.loads(last_path().read_text(encoding="utf-8"))
        if (isinstance(crash, dict) and isinstance(crash.get("type"), str) and _TYPE.fullmatch(crash["type"])
                and isinstance(crash.get("file"), str) and isinstance(crash.get("line"), int)):
            return {"type": crash["type"], "file": crash["file"][:200], "line": crash["line"]}
    except (OSError, ValueError, UnicodeError):
        pass
    return None
