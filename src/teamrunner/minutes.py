# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright 2026 Kumar Muthuraman
"""What a member's model is given on its turn (D111).

Two parts: its own file's text as its instructions, with the inputs filled
in (D70, D71), then the minutes — everything it may read, in order, each
part labelled with who said it — ending with "It is your turn":

    for:
    Remote work saves two hours of commuting a day…

    against:
    But teams lose the quick hallway conversations…

    It is your turn.

In 0.1 everyone reads everything said (D73); only a toolkeeper's reply is
for one reader, whoever asked (D85). `read` and `prompt_hash` let the
event log prove what the model was sent (D103).
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field

from . import ir
from .validate import PLACEHOLDER

NOTHING_YET = "Nothing has been said yet."


@dataclass(frozen=True)
class Entry:
    seq: int  # the event's seq in the run's log
    speaker: str  # a member's name
    text: str
    to: str | None = None  # None: everyone may read it


@dataclass
class Minutes:
    entries: list[Entry] = field(default_factory=list)

    def add(self, entry: Entry) -> None:
        self.entries.append(entry)

    def readable_by(self, name: str) -> list[Entry]:
        return [e for e in self.entries if e.to is None or e.to == name]


@dataclass(frozen=True)
class Request:
    messages: list[dict]  # what the model is sent
    read: list[int]  # the seq of every entry in the minutes
    prompt_hash: str


class MissingInput(Exception):
    pass


def _value(value) -> str:
    if isinstance(value, str):
        return value
    return json.dumps(value, ensure_ascii=False)


def instructions(member: ir.Member, inputs: dict[str, object]) -> str:
    """The member's file text with each `{{input}}` filled in."""
    def fill(match):
        name = match.group(1)
        if name not in inputs:
            raise MissingInput(f"{member.name} needs the input `{name}`, which has no value")
        return _value(inputs[name])
    return PLACEHOLDER.sub(fill, member.body).strip()


def write_minutes(entries: list[Entry], stage: str | None, must_ask: str | None = None) -> str:
    parts = []
    for e in entries:
        label = f"{e.speaker} (to {e.to})" if e.to else e.speaker
        parts.append(f"{label}:\n{e.text.strip()}")
    if not parts:
        parts.append(NOTHING_YET)
    turn = f"It is your turn. Stage: {stage}." if stage else "It is your turn."
    if must_ask:
        turn += f" End your turn with a request to {must_ask}."  # a toolkeeper step follows (D85)
    parts.append(turn)
    return "\n\n".join(parts)


def request_for(member: ir.Member, inputs: dict[str, object], minutes: Minutes,
                stage: str | None, must_ask: str | None = None) -> Request:
    entries = minutes.readable_by(member.name)
    messages = [
        {"role": "system", "content": instructions(member, inputs)},
        {"role": "user", "content": write_minutes(entries, stage, must_ask)},
    ]
    digest = hashlib.sha256(json.dumps(messages, ensure_ascii=False, sort_keys=True).encode()).hexdigest()
    return Request(messages, [e.seq for e in entries], "sha256:" + digest)
