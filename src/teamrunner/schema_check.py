# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright 2026 Kumar Muthuraman
"""Check a file's values against its JSON Schema, in plain words (D26).

The schemas in `schemas/` are the machine-checkable form of SPEC.md. Each
node that can fail carries an `x-expect` phrase ("a whole number, 1 or
more"), which becomes the error message; a person never sees JSON Schema's
own wording.
"""

from __future__ import annotations

import json
from functools import cache
from importlib.resources import files

from jsonschema import Draft202012Validator

from .loader import Doc


@cache
def validator(name: str) -> Draft202012Validator:
    schema = json.loads(files("teamrunner.schemas").joinpath(f"{name}.schema.json").read_text(encoding="utf-8"))
    return Draft202012Validator(schema)


def plain(value):
    """ruamel's round-trip types as plain Python, for the schema checker."""
    if isinstance(value, dict):
        return {k: plain(v) for k, v in value.items()}
    if isinstance(value, list):
        return [plain(v) for v in value]
    if isinstance(value, bool) or value is None:
        return value
    if isinstance(value, int):
        return int(value)
    if isinstance(value, float):
        return float(value)
    return str(value) if isinstance(value, str) else value


def _shown(value) -> str:
    text = json.dumps(value, ensure_ascii=False, default=str)
    return text if len(text) <= 60 else text[:57] + "..."


def _setting(path) -> str:
    return ".".join(str(p) for p in path)


def check_schema(doc: Doc, schema: str, problems, only: set[str] | None = None) -> bool:
    """Report every value that does not fit. `only` limits it to these words
    (the ones check_words let through). Returns True if all fit."""
    data = plain(doc.data)
    if only is not None:
        data = {k: v for k, v in data.items() if k in only}
    ok = True
    for err in sorted(validator(schema).iter_errors(data), key=lambda e: list(map(str, e.path))):
        ok = False
        path = list(err.path)
        expect = err.schema.get("x-expect") if isinstance(err.schema, dict) else None
        if "propertyNames" in err.schema_path:
            # a key that does not fit: err.instance is the key itself
            problems.error(doc.path, f"`{err.instance}` under `{_setting(path)}` should be "
                           f"{expect or 'one word'}",
                           line=doc.line(*path, err.instance), value=str(err.instance))
        elif err.validator == "required" and not path:
            missing = err.message.split("'")[1]
            problems.error(doc.path, f"`{missing}:` is missing", line=1,
                           fix=f"add a `{missing}:` line")
        else:
            problems.error(doc.path,
                           f"`{_setting(path)}` should be {expect or err.message}",
                           line=doc.line(*path), value=_shown(err.instance))
    return ok
