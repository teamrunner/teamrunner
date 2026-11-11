# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright 2026 Kumar Muthuraman
"""Problems found in a package: errors stop a run, warnings do not.

Every problem names its file and, where it can, the line, the offending
value and a suggested fix (D62), so a person or an assistant can repair it.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

ERROR = "error"
WARNING = "warning"


@dataclass(frozen=True)
class Problem:
    level: str
    file: Path
    message: str
    line: int | None = None  # 1-based
    value: str | None = None
    fix: str | None = None

    def where(self, root: Path) -> str:
        try:
            shown = self.file.relative_to(root)
        except ValueError:
            shown = self.file
        return f"{shown}:{self.line}" if self.line else str(shown)


class Problems(list):
    """A list of Problem with shorthands for adding them."""

    def error(self, file, message, line=None, value=None, fix=None):
        self.append(Problem(ERROR, Path(file), message, line, value, fix))

    def warning(self, file, message, line=None, value=None, fix=None):
        self.append(Problem(WARNING, Path(file), message, line, value, fix))

    @property
    def errors(self):
        return [p for p in self if p.level == ERROR]

    @property
    def warnings(self):
        return [p for p in self if p.level == WARNING]
