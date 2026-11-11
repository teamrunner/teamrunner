"""File metadata for the browser; team operations stay in the existing engine."""

import hashlib
import os
from pathlib import Path
import tempfile
import time

from ..runfolder import record_file


class OutsideRoot(ValueError):
    pass


class Conflict(ValueError):
    pass


def visible(path: Path) -> bool:
    # Keys and application internals never belong in the file browser.
    return not any(part.startswith(".") for part in path.parts)


def inside(root: Path, written: str) -> Path:
    if "\x00" in written:  # refused everywhere; Windows on Python 3.13+ would only say "not found"
        raise OutsideRoot("Choose a file inside the opened folder.")
    relative = Path(written)
    if relative.is_absolute() or ".." in relative.parts or not visible(relative):
        raise OutsideRoot("Choose a file inside the opened folder.")
    path = (root / relative).resolve()
    if not path.is_relative_to(root) or not visible(path.relative_to(root)):
        raise OutsideRoot("Choose a file inside the opened folder.")
    return path


def files(root: Path, written: str = "") -> dict:
    """One directory at a time: runs stay collapsed, even with years of history."""
    folder = inside(root, written)
    if not folder.is_dir():
        raise FileNotFoundError("That folder could not be found.")
    entries = []
    for child in folder.iterdir():
        relative = child.relative_to(root)
        if not visible(relative):
            continue
        try:
            target = inside(root, relative.as_posix())
            directory = target.is_dir()
            if not directory and not target.is_file():
                continue
            entries.append({"name": child.name, "path": relative.as_posix(),
                            "kind": "folder" if directory else "file",
                            "team": directory and (target / "team.yaml").is_file(),
                            "size": None if directory else target.stat().st_size})
        except (OutsideRoot, OSError, RuntimeError):
            continue  # External, broken and cyclic links are not browsable.
    entries.sort(key=lambda item: (item["kind"] != "folder", item["name"].casefold()))
    return {"path": written, "entries": entries}


TEXT = {".md", ".yaml", ".yml", ".txt", ".json", ".csv", ".toml", ".py"}
MAX_FILE = 2 * 1024 * 1024


def in_runs(root: Path, path: Path) -> bool:
    """Protect this workspace's records, not unrelated ancestors named runs."""
    if any(part.lower() == "runs" for part in path.relative_to(root).parts):
        return True
    # An archive opened directly is identified by its containing package, rather
    # than treating every user workspace named runs as an archive.
    if (root.name.lower() == "runs" and (root.parent / "team.yaml").is_file()
            and not (root / "team.yaml").is_file()):
        return True
    # Also recognize run folders and snapshots opened directly, or reached from
    # a workspace whose root happens to be named runs.
    return any(record_file(folder) is not None for folder in (path, *path.parents))


def editable(root: Path, written: str) -> Path:
    path = inside(root, written)
    parts = path.relative_to(root).parts
    if (not parts or in_runs(root, path)
            or "runs" in [p.lower() for p in Path(written).parts]
            or path.suffix.lower() not in TEXT):
        raise OutsideRoot("Choose a text file in the package. Run records are read-only.")
    return path


def read_file(root: Path, written: str) -> dict:
    path = inside(root, written)
    if path.suffix.lower() not in TEXT or not path.is_file():
        raise ValueError("Choose a text file to open.")
    if path.stat().st_size > MAX_FILE:
        raise ValueError("This file is too large for the editor (limit: 2 MB).")
    data = path.read_bytes()
    try:
        text = data.decode("utf-8")
    except UnicodeError:
        raise ValueError("This file isn’t plain text. Choose a UTF-8 text file.") from None
    return {"path": written, "text": text,
            "revision": hashlib.sha256(data).hexdigest(),
            "readonly": in_runs(root, path)
                        or "runs" in [p.lower() for p in Path(written).parts]}


def save_file(root: Path, written: str, text: str, revision: str) -> dict:
    path = editable(root, written)
    if not isinstance(text, str) or len(text.encode()) > MAX_FILE:
        raise ValueError("Choose a text file no larger than 2 MB.")
    if read_file(root, written)["revision"] != revision:
        raise Conflict("This file changed on disk. Reload it before saving; your edits are still here.")
    mode = path.stat().st_mode & 0o777
    handle, temporary = tempfile.mkstemp(dir=path.parent, prefix=".teamrunner-save-")
    try:
        with os.fdopen(handle, "w", encoding="utf-8", newline="") as stream:
            stream.write(text)
        os.chmod(temporary, mode)
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)
    return read_file(root, written)


def team_folder(root: Path, written: str) -> Path:
    path = inside(root, written)
    folder = path if path.is_dir() else path.parent
    while not (folder / "team.yaml").is_file():
        if folder == root:
            raise ValueError("Choose a team folder (one containing team.yaml).")
        folder = folder.parent
    guard_team(root, folder)
    return folder


def guard_team(root: Path, folder: Path):
    """The CLI can follow shared links; the browser must stay within its root."""
    from ..loader import find_top
    if in_runs(root, folder):
        raise OutsideRoot("Run records are read-only. Choose the current team to validate.")
    top = find_top(folder)
    if not top.is_relative_to(root):
        raise OutsideRoot("Open the package's top folder to validate this team.")
    for directory, dirs, names in os.walk(top, followlinks=False):
        dirs[:] = [n for n in dirs if not n.startswith(".") and n != "runs"]
        for name in dirs + [n for n in names if not n.startswith(".")]:
            path = Path(directory) / name
            if path.is_symlink():
                inside(root, path.relative_to(root).as_posix())
                if path.is_dir():
                    raise OutsideRoot("Folder links cannot be validated here. Use a package with ordinary folders.")


REMEMBER = 300  # seconds a provider's list of models is kept, so saving does not ask it each time
_lists: dict[tuple, tuple[float, object]] = {}


def remembering(listers: dict, fresh: bool) -> dict:
    """The engine's listers, answering from memory for a few minutes. Only an answer is
    remembered, never a failure; `fresh` asks again. Ollama is local and is always asked."""
    from ..keys import NO_KEY

    def remember(provider, lister):
        def ask(key, settings):
            slot = (provider, hashlib.sha256((key or "").encode()).hexdigest())
            held = _lists.get(slot)
            if held and not fresh and time.monotonic() - held[0] < REMEMBER:
                return held[1]
            found = lister(key, settings)
            _lists[slot] = (time.monotonic(), found)
            return found
        return ask
    return {p: lister if p in NO_KEY else remember(p, lister) for p, lister in listers.items()}


def validate_team(root: Path, written: str, offline: bool = False, fresh: bool = True) -> dict:
    from ..validate import validate
    from ..checks import online_check
    folder = team_folder(root, written)
    report = validate(folder, write_ids=True)
    def item(problem):
        return {"level": problem.level, "file": problem.file.relative_to(root).as_posix(),
                "line": problem.line, "message": problem.message,
                "value": problem.value, "fix": problem.fix}
    result = {"ok": report.ok, "team": folder.relative_to(root).as_posix(),
              "problems": [item(p) for p in report.problems],
              "ids_added": [p.relative_to(root).as_posix() for p, _ in report.ids_added]}
    # Roles come from the loaded member declarations, even while the team has errors.
    from ..ir import output_fields
    roles = {}
    for m in report.package.team.members:
        try:
            fields = output_fields(m.front.data.get("output"))
        except (TypeError, ValueError, AttributeError, KeyError, IndexError):
            fields = {}
        roles[m.path.relative_to(root).as_posix()] = (
            "toolkeeper" if m.kind == "toolkeeper" else
            "decider" if any(f.type == "choice" for f in fields.values()) else "worker")
    if report.package.team.chair:
        roles[report.package.team.chair.relative_to(root).as_posix()] = "chair"
    result["roles"] = roles
    if not offline and report.ok:  # as `teamrunner validate` does (D140)
        from ..keys import package_envs
        for env in package_envs(report.package.top):
            if not env.resolve().is_relative_to(root):
                raise OutsideRoot("A package key file points outside the opened folder. Set up keys in TeamRunner's key file instead.")
        from ..checks import LISTERS
        check, _ = online_check(report, listers=remembering(LISTERS, fresh))
        result["online"] = check.event()
        result["ok"] = check.ok
    return result


def show_member(root: Path, written: str) -> dict:
    from ..show import show, NotAMember
    path = inside(root, written)
    team_folder(root, written)
    try:
        text, _ = show(str(path), path.parent)
    except NotAMember:
        return {"text": "This view is for one member. Open a member's file, such as judge.md, "
                        "to see it with every setting written out."}
    return {"text": text or "Fix the validation errors to see this member as it will run."}


def explain_team(root: Path, written: str) -> dict:
    from ..build import load_team
    from ..explain import explain
    team, _ = load_team(team_folder(root, written))
    if team:
        runs = team.package_top / "runs"
        inside(root, runs.relative_to(root).as_posix())
        for directory, dirs, names in os.walk(runs, followlinks=False):
            for name in dirs + names:
                path = Path(directory) / name
                if path.is_symlink():
                    inside(root, path.relative_to(root).as_posix())
                    if path.is_dir():
                        raise OutsideRoot("Run folder links cannot be read here.")
    return {"text": explain(team) if team else "Fix the validation errors to see the flow summary."}


def join_written(folder: str, name: str) -> str:
    return name if folder in ("", ".") else f"{folder.rstrip('/')}/{name}"


def create_member(root: Path, written: str) -> dict:
    path = editable(root, written)
    if path.suffix != ".md":
        raise ValueError("Give the member a Markdown filename, such as researcher.md.")
    if path.name.lower() == "chair.md":
        raise ValueError("The chair is not a member. Use + Chair to write the team’s flow.")
    if path.name.lower() in ("manager.md", "readme.md"):
        raise ValueError(f"{path.name} is not a member’s file. Choose another name, such as researcher.md.")
    from ..loader import member_name
    from ..validate import WORD
    from ..vocabulary import RESERVED_NAMES
    name = member_name(path)
    if not WORD.fullmatch(name) or name in RESERVED_NAMES:
        raise ValueError("Use a member name with letters, digits and underscores, starting with a letter or underscore; avoid flow words such as done and stop.")
    team_folder(root, str(path.parent.relative_to(root)))
    with path.open("x", encoding="utf-8") as stream:
        stream.write('---\ndescription: ""\n---\nWrite this member’s instructions here.\n')
    return read_file(root, written)


def create_chair(root: Path, written: str) -> dict:
    """Add chair.md to the team folder `written`, holding a commented starter and no flow yet."""
    chair = join_written(written, "chair.md")
    path = editable(root, chair)
    if not (path.parent / "team.yaml").is_file():
        raise ValueError("Choose a team folder (one containing team.yaml).")
    guard_team(root, path.parent)
    if path.exists() or path.is_symlink():
        raise Conflict("This team already has a chair.md.")
    with path.open("x", encoding="utf-8") as stream:
        stream.write("# The flow: who acts, and in what order. For example:\n#\n"
                     "#   researcher -> writer -> reviewer\n#\n"
                     "# Write the steps below, using your members' names. Delete this file\n"
                     "# and members take one turn each, in alphabetical filename order.\n\n")
    return read_file(root, chair)


def rename_file(root: Path, written: str, destination: str, revision: str) -> dict:
    source, target = editable(root, written), editable(root, destination)
    target = target.with_name(Path(destination).name)
    if source.name == "team.yaml" or source.suffix != target.suffix or source.parent != target.parent:
        raise ValueError("Rename a file in the same folder, keeping its extension. Keep team.yaml's name.")
    if read_file(root, written)["revision"] != revision:
        raise Conflict("This file changed on disk. Reload it before renaming.")
    if target.exists():
        # samefile alone also accepts distinct hard links; allow only a case change
        # resolving to the same directory entry on a case-insensitive filesystem.
        case_change = source.name != target.name and source.name.casefold() == target.name.casefold()
        distinct = {p.name for p in source.parent.iterdir()}
        if not (case_change and target.name not in distinct and source.samefile(target)):
            raise Conflict("That filename already exists.")
    source.rename(target)
    return read_file(root, destination)


def trash_file(root: Path, written: str, revision: str) -> dict:
    from .trash import move_to_trash
    path = editable(root, written)
    if path.name == "team.yaml":
        raise ValueError("Keep team.yaml: it marks this folder as a team.")
    if read_file(root, written)["revision"] != revision:
        raise Conflict("This file changed on disk. Reload it before moving it to the trash.")
    move_to_trash(path)
    return {"ok": True}


def _team_to_trash(root: Path, written: str) -> Path:
    if not written or (root / written).is_symlink():
        raise ValueError("Choose a team folder inside the opened folder.")
    path = inside(root, written)
    if in_runs(root, path):
        raise OutsideRoot("Run records are read-only.")
    if path == root or not (path / "team.yaml").is_file():
        raise ValueError("Choose a team folder inside the opened folder.")
    return path


def team_contents(root: Path, written: str) -> dict:
    """What moving this team to the trash would take with it."""
    from ..runs import all_runs
    path = _team_to_trash(root, written)
    files = runs = 0
    for directory, dirs, names in os.walk(path, followlinks=False):
        here = Path(directory)
        if here.name.lower() == "runs":
            try:
                runs += len(list(all_runs(here)))
            except (OSError, ValueError):
                pass
            dirs[:] = []
            continue
        files += len(names)
    return {"path": written, "name": path.name, "files": files, "runs": runs}


def trash_team(root: Path, written: str) -> dict:
    """The whole folder goes, records included; the system trash can put it back."""
    from .trash import move_to_trash
    move_to_trash(_team_to_trash(root, written))
    return {"ok": True}


def new_team(root: Path, written: str, example: str = "") -> dict:
    from ..new import EXAMPLES, examples, create, copy_from
    from ..workspace import is_workspace
    if "/" not in written and "\\" not in written and is_workspace(root):
        written = f"teams/{written}"  # a team named on its own goes in the workspace's teams/ (D144)
    path = inside(root, written)
    if in_runs(root, path):
        raise OutsideRoot("Run records are read-only.")
    if example:
        if example not in examples():
            raise ValueError("Choose one of the shipped examples.")
        copy_from(EXAMPLES / example, path)
    else:
        create(path)
    return {"path": (path / "team.yaml").relative_to(root).as_posix()}


def key_status(root: Path, written: str) -> dict:
    from ..build import load_team
    from ..checks import needed_keys
    from ..keys import PROVIDER_KEYS, find_keys
    from ..builtin_tools import TOOL_KEYS
    folder = team_folder(root, written)
    team, _ = load_team(folder)
    names = needed_keys(team) if team else []
    keys = find_keys(names, None, None)
    return {"keys": [{"name": name, "present": name not in keys.missing} for name in names],
            "allowed": sorted(set(PROVIDER_KEYS.values()) | set(TOOL_KEYS.values()))}


def set_key(name: str, value: str) -> dict:
    from ..keys import PROVIDER_KEYS, key_file, save_key
    from ..builtin_tools import TOOL_KEYS
    if name not in set(PROVIDER_KEYS.values()) | set(TOOL_KEYS.values()):
        raise ValueError("Choose a provider or tool key from the list.")
    if not isinstance(value, str) or not value or any(c.isspace() for c in value) or len(value) > 8192:
        raise ValueError("Enter a key with no spaces or line breaks.")
    save_key(key_file(), name, value)
    return {"ok": True, "name": name}
