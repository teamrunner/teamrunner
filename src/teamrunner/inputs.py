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
from .schema_check import plain


@dataclass
class Given:
    value: object
    source: str  # given | default
    file: dict | None = None  # for --file: the original path and its hash


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
        data = YAML(typ="rt").load(path.read_text(encoding="utf-8"))
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
    return plain(data["inputs"])


def resolve(team: ir.Team, message: str | None = None, file: Path | None = None,
            task_file: Path | None = None) -> dict[str, Given]:
    """The value of every input, without copying anything yet."""
    if message is not None and file is not None:
        raise InputError("give either --message or --file, not both")
    given: dict[str, Given] = {}
    if task_file is not None:
        for name, value in read_task_file(task_file).items():
            if name not in team.inputs:
                raise InputError(f"the task file gives `{name}`, but no prompt in {team.name} uses it"
                                 + (f"; its inputs are {', '.join(f'`{n}`' for n in team.inputs)}"
                                    if team.inputs else "; this team takes no inputs"))
            given[name] = Given(value, "given")
    if message is not None or file is not None:
        flag = "--message" if message is not None else "--file"
        target = team.message_goes_to
        if target is None:
            raise InputError(
                f"{team.name} has nowhere to put {flag}: " +
                ("it takes no inputs" if not team.inputs else
                 "it has several inputs and `message_goes_to:` in team.yaml does not say which one "
                 "it fills; add that line, or use --task-file"))
        if target in given:
            raise InputError(f"`{target}` is given twice: by {flag} and by the task file")
        if file is not None:
            path = Path(file).expanduser()
            if not path.is_file():
                raise InputError(f"there is no file at {path}")
            given[target] = Given(str(path), "given", {
                "original": str(path.resolve()),
                "sha256": hashlib.sha256(path.read_bytes()).hexdigest()})
        else:
            given[target] = Given(message, "given")

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
                         "no default: give it with --message, --file or --task-file, "
                         "or add it under `default_inputs:` in team.yaml")
    return given


def copy_files(given: dict[str, Given], inputs_dir: Path) -> None:
    """Copy a --file into the run's `inputs/`; its value becomes the copy's path (D108)."""
    for item in given.values():
        if item.file:
            inputs_dir.mkdir(exist_ok=True)
            copy = inputs_dir / Path(item.file["original"]).name
            shutil.copy2(item.file["original"], copy)
            item.value = str(copy)
