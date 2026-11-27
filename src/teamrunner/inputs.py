# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright 2026 Kumar Muthuraman
"""What a run is given (SPEC §8, D70, D108): `--message`, `--file`,
`--task-file`, or nothing, with `default_inputs` filling the rest.

Every problem is found before any tokens are spent, and every input is
recorded as `given` or `default`.
"""

from __future__ import annotations

import hashlib
import shutil
from dataclasses import dataclass
from pathlib import Path

from ruamel.yaml import YAML
from ruamel.yaml.error import YAMLError

from . import ir
from .loader import IndentingTab, load_tabs_as_spaces
from .schema_check import plain


@dataclass
class Given:
    value: object
    source: str  # given | default
    file: dict | None = None  # for --file: the original path and its hash
    via: str | None = None  # `--message` or `--file`, when no `message_goes_to:` line named this input


# A task file may be labelled, as a lab labels its materials; the labels are
# never sent to a model and are not part of a study's hash.
TASK_LABELS = ("name", "description")


class InputError(Exception):
    """A run cannot start with what it was given."""


def read_task_file(path: Path) -> dict:
    path = Path(path).expanduser()
    if not path.is_file():
        raise InputError(f"there is no task file at {path}")
    try:
        data = load_tabs_as_spaces(YAML(typ="rt").load, path.read_text(encoding="utf-8-sig"))
    except IndentingTab as e:
        raise InputError(f"{path}: line {e.line + 1} is indented with a tab; YAML needs spaces to indent, "
                         "so replace it with spaces") from e
    except YAMLError as e:
        raise InputError(f"{path} is not valid YAML: {e}") from e
    if not isinstance(data, dict) or not isinstance(data.get("inputs"), dict):
        raise InputError(f"{path} should hold `inputs:`, with one `name: value` line under it for each input")
    extra = set(data) - {"inputs", *TASK_LABELS}
    if extra:
        raise InputError(f"{path} has {', '.join(f'`{k}`' for k in sorted(map(str, extra)))}; "
                         "a task file holds `inputs:`, and may have a `name:` and a `description:`")
    for label in TASK_LABELS:
        if label in data and not isinstance(data[label], str):
            raise InputError(f"{path}: `{label}:` should be a line of text")
    empty = [str(name) for name, value in data["inputs"].items() if value is None]
    if empty:  # `topic:` with nothing after it reached the prompt as the word null
        raise InputError(f"{path}: `{empty[0]}:` has no value; write one after the colon, "
                         "or `\"\"` for an empty one")
    return plain(data["inputs"])


def _not_an_input(team: ir.Team, who: str, name: str) -> InputError:
    return InputError(f"{who} gives `{name}`, but no prompt in {team.name} uses it"
                      + (f"; its inputs are {', '.join(f'`{n}`' for n in team.inputs)}"
                         if team.inputs else "; this team takes no inputs"))


def read_pairs(pairs: list[str]) -> list[tuple[str, str]]:
    """`--input name=value`, each as written: the value is the text after the first `=`."""
    out = []
    for pair in pairs:
        name, equals, value = pair.partition("=")
        name = name.strip()
        if not equals or not name:
            raise InputError(f"--input {pair!r} should be `name=value`, "
                             "for example --input motion=\"Should zoos be abolished?\"")
        out.append((name, value))
    return out


def resolve(team: ir.Team, message: str | None = None, file: Path | None = None,
            task_file: Path | None = None, task_inputs: dict | None = None,
            pairs: list[str] | None = None) -> dict[str, Given]:
    """The value of every input, without copying anything yet. `task_inputs` is a task
    file's inputs already read; the file is then not read again. `pairs` are the
    `--input name=value` flags, as typed."""
    if message is not None and file is not None:
        raise InputError("give either --message or --file, not both")
    given: dict[str, Given] = {}
    by: dict[str, str] = {}  # who gave each input, to name both when one is given twice

    def give(name, item, who):
        if name in by:
            raise InputError(f"`{name}` is given twice: by {by[name]} and by {who}")
        given[name], by[name] = item, who

    if task_inputs is not None or task_file is not None:
        for name, value in (task_inputs if task_inputs is not None else read_task_file(task_file)).items():
            if name not in team.inputs:
                raise _not_an_input(team, "the task file", name)
            give(name, Given(value, "given"), "the task file")
    for name, value in read_pairs(pairs or []):
        if name not in team.inputs:
            raise _not_an_input(team, "--input", name)
        give(name, Given(value, "given"), "--input")
    if message is not None or file is not None:
        flag = "--message" if message is not None else "--file"
        target = team.message_goes_to
        if target is None:
            raise InputError(
                f"{team.name} has nowhere to put {flag}: " +
                ("it takes no inputs" if not team.inputs else
                 "it has several inputs and `message_goes_to:` in team.yaml does not say which one "
                 "it fills; add that line, or use --input or --task-file"))
        if target in by:
            raise InputError(f"`{target}` is given twice: by {by[target]} and by {flag}")
        via = flag if team.message_goes_to_inferred else None
        if file is not None:
            path = Path(file).expanduser()
            if not path.is_file():
                raise InputError(f"there is no file at {path}")
            given[target] = Given(str(path), "given", {
                "original": str(path.resolve()),
                "sha256": hashlib.sha256(path.read_bytes()).hexdigest()}, via)
        else:
            given[target] = Given(message, "given", via=via)
        by[target] = flag

    missing = []
    for name, spec in team.inputs.items():
        if name not in given:
            if spec.has_default:
                given[name] = Given(spec.default, "default")
            else:
                missing.append(name)
    if missing:
        names = ", ".join(f"`{n}`" for n in missing)
        raise InputError(f"{team.name} needs {names}, which {'has' if len(missing) == 1 else 'have'} "
                         "no default: give it with --message, --file, --input or --task-file, "
                         "or add it under `default_inputs:` in team.yaml")
    return {name: given[name] for name in team.inputs}  # in the team's order, however they were given


def copy_files(given: dict[str, Given], inputs_dir: Path) -> None:
    """Copy a --file into the run's `inputs/`; its value becomes the copy's path (D108)."""
    for item in given.values():
        if item.file:
            inputs_dir.mkdir(exist_ok=True)
            copy = inputs_dir / Path(item.file["original"]).name
            shutil.copy2(item.file["original"], copy)
            item.value = str(copy)
