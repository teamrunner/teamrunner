"""Ids: ULIDs (D33), written into hand-written files that lack one (D64).

The id line goes in as text, just before the file's first setting, so
everything else in the file — comments, order, quoting — stays as written:

    id: 01JZY4D5E6F7G8H9JAKBMCNDPE  # added by teamrunner — keeps this agent's history together

A person reads an id by its last 8 characters after an ellipsis (D88).
"""

from __future__ import annotations

import os
import time
from pathlib import Path

_CROCKFORD = "0123456789ABCDEFGHJKMNPQRSTVWXYZ"


def new_ulid() -> str:
    """48 bits of milliseconds, then 80 random bits, in Crockford base 32."""
    value = (int(time.time() * 1000) << 80) | int.from_bytes(os.urandom(10), "big")
    return "".join(_CROCKFORD[(value >> shift) & 31] for shift in range(125, -1, -5))


def short(ulid: str) -> str:
    return "…" + ulid[-8:]


def _line(ulid: str, what: str) -> str:
    return f"id: {ulid}  # added by teamrunner — keeps this {what}'s history together\n"


def _first_setting(lines: list[str], start: int, end: int) -> int:
    for i in range(start, end):
        text = lines[i].strip()
        if text and not text.startswith("#"):
            return i
    return end


def write_member_id(path: Path, what: str, ulid: str | None = None) -> str:
    """Add an id to a member file's frontmatter — a new one, or `ulid`. Returns the id."""
    ulid = ulid or new_ulid()
    lines = path.read_text(encoding="utf-8").splitlines(keepends=True)
    close = next(i for i in range(1, len(lines)) if lines[i].rstrip() == "---")
    lines.insert(_first_setting(lines, 1, close), _line(ulid, what))
    path.write_text("".join(lines), encoding="utf-8")
    return ulid


def write_team_id(path: Path) -> str:
    """Add an id to a team.yaml. Returns the id."""
    ulid = new_ulid()
    lines = path.read_text(encoding="utf-8").splitlines(keepends=True)
    if lines and not lines[-1].endswith("\n"):
        lines[-1] += "\n"
    lines.insert(_first_setting(lines, 0, len(lines)), _line(ulid, "team"))
    path.write_text("".join(lines), encoding="utf-8")
    return ulid
