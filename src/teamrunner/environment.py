# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright 2026 Kumar Muthuraman
"""The versions a run depends on, recorded in every record.json (D14c: a study must be rerunnable)."""

from __future__ import annotations

import platform
from importlib.metadata import PackageNotFoundError, version


def _version(package: str) -> str | None:
    try:
        return version(package)
    except PackageNotFoundError:
        return None


def environment() -> dict:
    """Python, LiteLLM and Deno, beside TeamRunner's own version."""
    return {"python": platform.python_version(),
            "litellm": _version("litellm"),
            "deno": _version("deno")}
