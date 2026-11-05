"""`teamrunner new <name> --describe "…"` (D106): a model writes the team.

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


def describe(folder: Path, description: str, model_name: str, model: Model) -> Described:
    folder = Path(folder)
    _check_name(folder.name)
    if folder.exists():
        raise NewError(f"{folder} already exists; `new` never writes over anything")
    messages = [
        {"role": "system", "content": instructions() + ANSWER},
        {"role": "user", "content": f"The team folder is called `{folder.name}`.\n\n"
                                    f"Write a team that does this:\n\n{description.strip()}"},
    ]
    cost, unknown, notes, report, wrong = 0.0, False, "", None, []
    for round_ in range(1, MAX_ROUNDS + 1):
        reply = asyncio.run(model.complete(model_name, {}, messages, SCHEMA))
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
                return Described(folder, True, round_, notes, None if unknown else round(cost, 6), report)
            wrong = [f"validate: {json_report(report)}"]
        messages += [
            {"role": "assistant", "content": reply.text},
            {"role": "user", "content": "That did not work:\n" + "\n".join(wrong)
             + "\n\nAnswer again with the full set of files, corrected."},
        ]
    return Described(folder, False, MAX_ROUNDS, notes, None if unknown else round(cost, 6), report, wrong)
