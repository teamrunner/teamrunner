# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright 2026 Kumar Muthuraman
"""`teamrunner new <name> --describe "…"` (D106): a model writes the team.

The description may also come from a file (`--describe-file`) or the browser
page's + Team (D162); it is kept at the top of `team.yaml`.

It is given AGENTS.md — the same instructions as the authoring skill — and
the description, and answers with the files. TeamRunner writes them into
the new folder, validates them, and sends any errors back for the model to
fix, a few times at most. It never writes over an existing folder, says
what it cost, and the command ends by printing `teamrunner explain`.
"""

from __future__ import annotations

import asyncio
import json
import re
import shutil
from dataclasses import dataclass, field
from pathlib import Path

from .authoring import instructions
from .explain import json_report
from .model import Model
from .new import GITIGNORE, NewError, _check_name
from .validate import validate

MAX_ROUNDS = 3
MAX_DESCRIPTION = 50_000  # bytes, by either route (D162)

# Offered when no model is named, for whichever keys are set.
DESCRIBE_MODELS = {"OPENAI_API_KEY": "openai/gpt-6-luna", "ANTHROPIC_API_KEY": "anthropic/claude-sonnet-5-5",
                   "GEMINI_API_KEY": "gemini/gemini-2.5-pro"}
ALLOWED = re.compile(r"^(team\.yaml|[A-Za-z0-9_]+\.md|models/[A-Za-z0-9_]+\.yaml)$")

ANSWER = """
## How to answer here

You cannot run commands in this conversation. Answer with JSON only, in this form:

{"files": [{"path": "team.yaml", "content": "..."}, {"path": "chair.md", "content": "..."}, ...],
 "notes": "what you chose that the description did not say, in a sentence or two"}

Paths are relative to the team folder: `team.yaml`, `chair.md`, member files
such as `for.md`, and model profiles as `models/<name>.yaml`. No subfolders
other than `models/`. TeamRunner will write the files and run `validate`;
if it finds errors you will be sent them and asked for the full set of
files again, corrected.
"""

SCHEMA = {
    "type": "object",
    "properties": {
        "files": {"type": "array", "items": {
            "type": "object",
            "properties": {"path": {"type": "string"}, "content": {"type": "string"}},
            "required": ["path", "content"], "additionalProperties": False}},
        "notes": {"type": "string"},
    },
    "required": ["files", "notes"],
    "additionalProperties": False,
}


@dataclass
class Described:
    folder: Path
    ok: bool
    rounds: int
    notes: str
    cost_usd: float | None
    report: object = None
    problems: list[str] = field(default_factory=list)


def _files(text: str) -> tuple[list[tuple[str, str]], str, list[str]]:
    """The files in a reply, its notes, and what was wrong with it."""
    try:
        data = json.loads(text)
        files = [(f["path"].strip(), f["content"]) for f in data["files"]]
        notes = str(data.get("notes", ""))
    except (ValueError, KeyError, TypeError):
        return [], "", ["the reply was not the JSON asked for"]
    wrong = [f"`{path}` is not a file a team may hold" for path, _ in files if not ALLOWED.match(path)]
    if not any(path == "team.yaml" for path, _ in files):
        wrong.append("there was no team.yaml")
    return files, notes, wrong


def offered_models() -> list[str]:
    """The models `--describe` and the page offer: those whose key is set."""
    import os
    from .keys import key_file, read_env
    stored = read_env(key_file())
    return [m for k, m in DESCRIBE_MODELS.items() if k in stored or os.environ.get(k)]


def check_description(description: str) -> str:
    """The description, stripped; refused if empty or longer than MAX_DESCRIPTION (D162)."""
    if not isinstance(description, str) or not description.strip():
        raise NewError("the description is empty; say what the team should do")
    if len(description.encode("utf-8")) > MAX_DESCRIPTION:
        raise NewError(f"the description is longer than {MAX_DESCRIPTION // 1000} KB; shorten it")
    return description.strip()


def read_description(path: Path) -> str:
    """A description from a UTF-8 text file (`--describe-file`, D162)."""
    path = Path(path).expanduser()
    if not path.is_file():
        raise NewError(f"{path} is not a file")
    if path.stat().st_size > MAX_DESCRIPTION:
        raise NewError(f"{path} is longer than {MAX_DESCRIPTION // 1000} KB; shorten it")
    try:
        return check_description(path.read_text(encoding="utf-8"))
    except UnicodeError:
        raise NewError(f"{path} isn't plain text; give a UTF-8 text file") from None


def keep_description(folder: Path, description: str) -> None:
    """Put the description at the top of team.yaml as a comment (D162)."""
    team = Path(folder) / "team.yaml"
    if not team.is_file():
        return
    lines = ["# Described as (the description this team was written from):"]
    lines += [f"# {line}".rstrip() for line in description.strip().splitlines()]
    team.write_text("\n".join(lines) + "\n\n" + team.read_text(encoding="utf-8"), encoding="utf-8")


def describe(folder: Path, description: str, model_name: str, model: Model,
             on_round=None) -> Described:
    """`on_round(n)` is called before each try, for a progress line."""
    folder = Path(folder)
    _check_name(folder.name)
    description = check_description(description)
    if folder.exists():
        raise NewError(f"{folder} already exists; `new` never writes over anything")
    messages = [
        {"role": "system", "content": instructions() + ANSWER},
        {"role": "user", "content": f"The team folder is called `{folder.name}`.\n\n"
                                    f"Write a team that does this:\n\n{description.strip()}"},
    ]
    cost, unknown, notes, report, wrong = 0.0, False, "", None, []
    for round_ in range(1, MAX_ROUNDS + 1):
        if on_round:
            on_round(round_)
        try:
            reply = asyncio.run(model.complete(model_name, {}, messages, SCHEMA))
        except Exception as e:  # no credit, a wrong model name, the network (review 2026-11-16, C8)
            if folder.exists():
                shutil.rmtree(folder)  # only ever a folder this command made, in an earlier round
            reason = str(e).strip().splitlines()[0] if str(e).strip() else type(e).__name__
            raise NewError(f"the model could not write the team: {reason}; nothing was made") from e
        if reply.cost_usd is None:
            unknown = True
        else:
            cost += reply.cost_usd
        files, notes, wrong = _files(reply.text)
        if not wrong:
            if folder.exists():
                shutil.rmtree(folder)  # only ever a folder this command made, in an earlier round
            folder.mkdir(parents=True)
            for path, content in files:
                target = folder / path
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_text(content if content.endswith("\n") else content + "\n", encoding="utf-8")
            (folder / ".gitignore").write_text(GITIGNORE, encoding="utf-8")
            report = validate(folder)
            if report.ok:
                report = validate(folder, write_ids=True)  # D64: ids, now the files are right
                keep_description(folder, description)
                return Described(folder, True, round_, notes, None if unknown else round(cost, 6), report)
            wrong = [f"validate: {json_report(report)}"]
        messages += [
            {"role": "assistant", "content": reply.text},
            {"role": "user", "content": "That did not work:\n" + "\n".join(wrong)
             + "\n\nAnswer again with the full set of files, corrected."},
        ]
    if folder.exists():
        keep_description(folder, description)
        report = validate(folder)  # again, so the lines it names match the file with its comment
    return Described(folder, False, MAX_ROUNDS, notes, None if unknown else round(cost, 6), report, wrong)
