# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright 2026 Kumar Muthuraman
"""Ids: ULIDs (D33), written into hand-written files that lack one (D64).

The id line goes in as text, just before the file's first setting, so
everything else in the file — comments, order, quoting — stays as written:

    id: 01JZY4D5E6F7G8H9JAKBMCNDPE  # added by teamrunner — keeps this agent's history together

A person reads an id by its last 8 characters after an ellipsis (D88).

A team.yaml may open with `---` (and `%YAML` lines), as many editors write
it; the id goes below them. After writing, the file is read back: if it no
longer reads, or the id is not in it, the file is put back exactly as it was
and `IdNotWritten` is raised, so adding an id never breaks a file.
"""

from __future__ import annotations

import os
import time
from pathlib import Path

class IdNotWritten(OSError):
    """The id could not be added without breaking the file; the file is unchanged."""

    def __init__(self, path: Path):
        super().__init__(f"TeamRunner could not add its `id:` line to {path.name} without breaking it, "
                         "so the file is unchanged; add `id: …` as its first setting yourself")
        self.strerror = str(self)


_CROCKFORD = "0123456789ABCDEFGHJKMNPQRSTVWXYZ"


def new_ulid() -> str:
    """48 bits of milliseconds, then 80 random bits, in Crockford base 32."""
    value = (int(time.time() * 1000) << 80) | int.from_bytes(os.urandom(10), "big")
    return "".join(_CROCKFORD[(value >> shift) & 31] for shift in range(125, -1, -5))


def short(ulid: str) -> str:
    return "…" + ulid[-8:]


def _line(ulid: str, what: str) -> str:
    return f"id: {ulid}  # added by teamrunner — keeps this {what}'s history together\n"


def _first_setting(lines: list[str], start: int, end: int, document_start: bool = False) -> int:
    """The first line that is a setting. With `document_start`, a `%` directive or a
    `---` before it is not one: the id goes below them."""
    for i in range(start, end):
        text = lines[i].strip()
        if not text or text.startswith("#"):
            continue
        if document_start and (text.startswith("%") or text == "---" or text.startswith("--- #")):
            continue
        return i
    return end


def insert_at_top(text: str, block: str) -> str:
    """`block` (whole lines) placed before the first setting of a YAML file's text."""
    lines = text.splitlines(keepends=True)
    if lines and not lines[-1].endswith(("\n", "\r")):
        lines[-1] += "\r\n" if "\r\n" in text else "\n"
    lines.insert(_first_setting(lines, 0, len(lines), document_start=True), block)
    return "".join(lines)


def reads_with_id(text: str, ulid: str, member: bool = False) -> bool:
    """Whether `text` still reads as settings, holding `id: <ulid>`."""
    from ruamel.yaml import YAML
    from .loader import split_frontmatter
    if member:
        parts = split_frontmatter(text)
        if parts is None or parts[1] is None:
            return False
        text = parts[0]
    try:
        data = YAML(typ="safe").load(text)
    except Exception:  # noqa: BLE001 — any failure to read means the id did not go in cleanly
        return False
    return isinstance(data, dict) and str(data.get("id")) == ulid


def write_member_id(path: Path, what: str, ulid: str | None = None) -> str:
    """Add an id to a member file's frontmatter — a new one, or `ulid`. Returns the id."""
    ulid = ulid or new_ulid()
    text, eol = _read(path)
    bom, text = (BOM, text[1:]) if text.startswith(BOM) else ("", text)
    lines = text.splitlines(keepends=True)
    close = next(i for i in range(1, len(lines)) if lines[i].rstrip() == "---")
    lines.insert(_first_setting(lines, 1, close), _line(ulid, what).replace("\n", eol))
    _write_checked(path, text, "".join(lines), ulid, member=True, bom=bom)
    return ulid


def write_team_id(path: Path) -> str:
    """Add an id to a team.yaml. Returns the id."""
    ulid = new_ulid()
    text, eol = _read(path)
    bom, text = (BOM, text[1:]) if text.startswith(BOM) else ("", text)
    _write_checked(path, text, insert_at_top(text, _line(ulid, "team").replace("\n", eol)), ulid, bom=bom)
    return ulid


def _read(path: Path) -> tuple[str, str]:
    """A file's text as written, and its line ending: an id line matches the file's
    own endings, and a file's endings are never changed, on any system."""
    with path.open(encoding="utf-8", newline="") as stream:
        text = stream.read()
    return text, "\r\n" if "\r\n" in text else "\n"


BOM = "\ufeff"  # a byte-order mark, as Windows Notepad writes: kept, but not read as text


def _write_checked(path: Path, before: str, after: str, ulid: str, member: bool = False,
                   bom: str = "") -> None:
    """Write `after` only if it reads back with the id; never leave a broken file."""
    if not reads_with_id(after, ulid, member):
        raise IdNotWritten(path)
    _write(path, bom + after)


def _write(path: Path, text: str) -> None:
    with path.open("w", encoding="utf-8", newline="") as stream:
        stream.write(text)
