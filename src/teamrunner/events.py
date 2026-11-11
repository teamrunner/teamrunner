# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright 2026 Kumar Muthuraman
"""The run's event log (SPEC §7, D103): every event, numbered, in order.

`Log.emit` adds the envelope — `seq`, `ts`, `run_id` — and returns the
event's `seq`, which is how later events point back to it (`read`).
Writing `events.jsonl` to the run folder comes with run logging (D69).
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Callable

from .ids import new_ulid


class Log:
    def __init__(self, run_id: str | None = None, on_event: Callable[[dict], None] | None = None):
        self.run_id = run_id or new_ulid()
        self.events: list[dict] = []
        self.on_event = on_event

    def emit(self, event: dict) -> int:
        seq = len(self.events) + 1
        line = {
            "seq": seq,
            "ts": datetime.now(timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z"),
            "run_id": self.run_id,
            **event,
        }
        self.events.append(line)
        if self.on_event:
            self.on_event(line)
        return seq

    def of(self, kind: str) -> list[dict]:
        return [e for e in self.events if e["event"] == kind]
