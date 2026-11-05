"""The `teamrunner` command."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from . import __version__
from .ids import short
from .problems import ERROR
from .validate import NotATeam, Report, validate


class Style:
    def __init__(self, stream):
        on = stream.isatty()
        self.red = (lambda s: f"\033[31m{s}\033[0m") if on else str
        self.yellow = (lambda s: f"\033[33m{s}\033[0m") if on else str
        self.green = (lambda s: f"\033[32m{s}\033[0m") if on else str
        self.dim = (lambda s: f"\033[2m{s}\033[0m") if on else str


def _plural(n: int, word: str) -> str:
    return f"{n} {word}" if n == 1 else f"{n} {word}s"


def _summary(report: Report) -> str:
    team = report.package.team
    toolkeepers = sum(1 for m in team.members if m.kind == "toolkeeper")
    parts = [_plural(len(team.members), "member")]
    parts.append("chair.md" if team.chair else "round-robin")
    if toolkeepers:
        parts.append(_plural(toolkeepers, "toolkeeper"))
    return f"{team.name} — " + ", ".join(parts)


def print_problems(report: Report, out: Style) -> None:
    root = report.package.top
    for path, ulid in report.ids_added:
        shown = path.relative_to(root) if path.is_relative_to(root) else path
        print(f"{out.green('+')} {shown}  id added: {short(ulid)}")
    problems = sorted(report.problems, key=lambda p: (p.level != ERROR, str(p.file), p.line or 0))
    for p in problems:
        mark = out.red("✗") if p.level == ERROR else out.yellow("!")
        print(f"{mark} {p.where(root)}  {p.message}")
        if p.fix:
            print(f"    {out.dim(p.fix)}")


def _online_check(report, out, quiet: bool):
    """validate --online (D100): the same key and model check as `run`, and no run.
    It never asks: a key found only in the shell is used for the check, not saved."""
    from .build import load_team
    from .checks import LISTERS, litellm_data, litellm_params, needed_keys, run_check
    from .console import Console
    from .keys import extra_settings, find_keys
    team, _ = load_team(report.package.team.folder)
    keys = find_keys(needed_keys(team), team.package_top, None)
    check = run_check(team, keys, LISTERS, extra_settings(), litellm_data, litellm_params)
    if not quiet:
        Console(False, sys.stdout).check(check, keys)
    return check


def cmd_validate(args) -> int:
    out = Style(sys.stdout)
    try:
        report = validate(Path(args.team), write_ids=True)
    except NotATeam as e:
        print(out.red(f"✗ {e}"), file=sys.stderr)
        return 2
    online = None
    if args.online and report.ok:
        online = _online_check(report, out, quiet=args.json)
    if args.json:
        from .explain import json_report
        data = json.loads(json_report(report))
        if online is not None:
            data["online"] = online.event()
            data["ok"] = data["ok"] and online.ok
        print(json.dumps(data, indent=2, ensure_ascii=False))
        return 0 if data["ok"] else 1
    print_problems(report, out)
    if args.verbose:
        team = report.package.team
        for member in team.members:
            choice = report.models.get(member.path)
            if member.kind == "toolkeeper":
                tool = next(iter(member.front.data.get("tools") or {}), "?")
                print(f"  {member.name:<20} toolkeeper · {tool}")
            elif choice:
                print(f"  {member.name:<20} {choice.model}  {out.dim('from ' + choice.source)}")
            else:
                print(f"  {member.name:<20} {out.dim('no model')}")
    errors, warnings = len(report.problems.errors), len(report.problems.warnings)
    if errors:
        tail = out.red(_plural(errors, "error"))
        if warnings:
            tail += ", " + out.yellow(_plural(warnings, "warning"))
    else:
        tail = out.green("OK") + (", " + out.yellow(_plural(warnings, "warning")) if warnings else "")
    print(f"{_summary(report)} · {tail}")
    if online is not None and not online.ok:
        return 1
    return 1 if errors else 0


def ask_at_keyboard(question: str, answers: list[str]) -> str:
    """A question at the terminal; the first answer is the default."""
    labels = " / ".join(f"[{a[0]}]{a[1:]}" if i == 0 else a for i, a in enumerate(answers))
    while True:
        reply = input(f"{question} {labels} ").strip().lower()
        if not reply:
            return answers[0]
        for a in answers:
            if a.lower().startswith(reply):
                return a


def cmd_run(args) -> int:
    from .run import EXIT, Options, run_team
    out = Style(sys.stdout)
    interactive = sys.stdin.isatty() and sys.stdout.isatty() and not args.quiet
    options = Options(message=args.message, file=args.file, task_file=args.task_file,
                      runs_dir=args.runs_dir, verbose=args.verbose, quiet=args.quiet,
                      ask=ask_at_keyboard if interactive else None, rehearse=args.rehearse)
    try:
        outcome = run_team(Path(args.team), options)
    except NotATeam as e:
        if not args.quiet:
            print(out.red(f"✗ {e}"), file=sys.stderr)
        return EXIT["could_not_start"]
    if outcome.status == "could_not_start" and outcome.report and not outcome.report.ok and not args.quiet:
        print_problems(outcome.report, out)
        print(f"{_summary(outcome.report)} · " + out.red("cannot run until these are fixed"))
    return outcome.exit_code


def cmd_runs(args) -> int:
    from .console import Console
    from .runs import NoRuns, all_runs, pick, print_list, replay, runs_folder
    out = Console(args.verbose)
    folder, when = Path("."), None
    for given in args.args:
        if Path(given).is_dir() and (Path(given) / "team.yaml").is_file():
            folder = Path(given)
        else:
            when = given if when is None else f"{when} {given}"
    try:
        top, runs = runs_folder(folder, args.runs_dir)
    except NoRuns as e:
        print(Style(sys.stderr).red(f"✗ {e}"), file=sys.stderr)
        return 1
    records = all_runs(runs)
    if args.team:
        records = [r for r in records if r.team == args.team]
    if args.status:
        records = [r for r in records if r.run.get("status") == args.status]
    if when is None:
        print_list(top, records, records if args.all else records[:20], out)
        return 0
    matches = pick(records, when)
    if len(matches) == 1:
        replay(matches[0], out)
        return 0
    if not matches:
        out.say(out.paint.red(f"✗ no run matches “{when}”"))
        return 1
    out.say(f"{len(matches)} runs match “{when}”; give more of it, or the folder name:")
    print_list(top, records, matches, out)
    return 1


def cmd_show(args) -> int:
    from .show import NotAMember, show
    out = Style(sys.stdout)
    try:
        text, report = show(args.member, Path.cwd())
    except NotAMember as e:
        print(out.red(f"✗ {e}"), file=sys.stderr)
        return 1
    if text is None:
        print_problems(report, out)
        print(f"{_summary(report)} · " + out.red("fix these first; `show` reads a team that validates"))
        return 1
    print(text)
    return 0


def cmd_gather(args) -> int:
    from .gather import envs, outside_links, replace
    out = Style(sys.stdout)
    folder = Path(args.team)
    if not (folder / "team.yaml").is_file():
        print(out.red(f"✗ {folder} is not a team folder: it has no team.yaml"), file=sys.stderr)
        return 1
    top, links = outside_links(folder)
    for env in envs(top):
        print(out.yellow(f"! {env.relative_to(top.parent)} holds settings or keys; it is not needed "
                         "and anyone you send the package to gets it — remove it before handing over"))
    if not links:
        print(f"{top.name} · " + out.green("nothing points outside the package"))
        return 0
    print(f"{top.name} · {len(links)} link{'s' if len(links) != 1 else ''} point{'s' if len(links) == 1 else ''} "
          "outside the package:")
    for link, target in links:
        print(f"  {link.relative_to(top.parent)}  →  {target}")
    if not args.yes:
        if not (sys.stdin.isatty() and sys.stdout.isatty()):
            print(out.dim("nothing changed; run with --yes to replace them with copies"))
            return 1
        if ask_at_keyboard("Replace each with a real copy, here in the package?", ["Yes", "No"]) != "Yes":
            print(out.dim("nothing changed"))
            return 1
    for link, target in links:
        replace(link, target)
        print(f"{out.green('✓')} {link.relative_to(top.parent)} is now a copy")
    return 0


# Offered by `new --describe` when no --model is given, for whichever keys are set.
DESCRIBE_MODELS = {"OPENAI_API_KEY": "openai/gpt-6-luna", "ANTHROPIC_API_KEY": "anthropic/claude-sonnet-4-5",
                   "GEMINI_API_KEY": "gemini/gemini-2.5-pro"}


def _describe(args, out) -> int:
    import os
    from .build import load_team
    from .describe import describe
    from .explain import explain
    from .keys import PROVIDER_KEYS, extra_settings, find_keys, key_file, read_env
    from .new import NewError
    interactive = sys.stdin.isatty() and sys.stdout.isatty()
    model = args.model
    if model is None:
        stored = read_env(key_file())
        offered = [m for k, m in DESCRIBE_MODELS.items() if k in stored or os.environ.get(k)]
        if not offered:
            print(out.red("✗ no model key found; add one to ~/.teamrunner/.env, or give --model"),
                  file=sys.stderr)
            return 1
        if not interactive:
            print(out.red(f"✗ give --model; for example --model {offered[0]}"), file=sys.stderr)
            return 1
        model = ask_at_keyboard("Which model should write the team?", offered)
    provider = model.split("/", 1)[0]
    name = PROVIDER_KEYS.get(provider)
    keys = find_keys([name] if name else [], None, ask_at_keyboard if interactive else None)
    if keys.missing:
        print(out.red(f"✗ {model} needs {name}; not found in ~/.teamrunner/.env or your shell"),
              file=sys.stderr)
        return 1
    if args.model_factory is None:
        from .litellm_model import LiteLLMModel
        writer = LiteLLMModel(keys.values, extra_settings())
    else:
        writer = args.model_factory(keys.values, {})
    print(out.dim(f"{model} is writing the team…"))
    try:
        result = describe(Path(args.name), args.describe, model, writer)
    except NewError as e:
        print(out.red(f"✗ {e}"), file=sys.stderr)
        return 1
    cost = "cost unknown" if result.cost_usd is None else f"${result.cost_usd:.4f}"
    tries = f"{result.rounds} tr{'y' if result.rounds == 1 else 'ies'}"
    if not result.ok:
        print(out.red(f"✗ the model could not write a team that validates in {tries} ({cost})"))
        if result.report is not None:
            print_problems(result.report, out)
        else:
            for problem in result.problems:
                print(out.red(f"  {problem}"))
        print(out.dim(f"what it wrote is in {result.folder}" if result.folder.exists() else "nothing was written"))
        return 1
    print(f"{out.green('✓')} {result.folder} written by {model} in {tries} · {cost}")
    if result.notes:
        print(out.dim(f"its choices: {result.notes}"))
    team, _ = load_team(result.folder)
    print()
    print(explain(team))
    return 0


def cmd_new(args) -> int:
    from .new import NewError, copy_from, create, find_source
    out = Style(sys.stdout)
    if args.describe and args.source:
        print(out.red("✗ give either --from or --describe, not both"), file=sys.stderr)
        return 1
    if args.describe:
        return _describe(args, out)
    try:
        if args.source:
            folder, warnings = copy_from(find_source(args.source), Path(args.name))
            for w in warnings:
                print(out.yellow(f"! {w}"))
        else:
            folder = create(Path(args.name))
    except NewError as e:
        print(out.red(f"✗ {e}"), file=sys.stderr)
        return 1
    report = validate(folder)
    print_problems(report, out)
    made = "copied from " + str(args.source) if args.source else "made"
    print(f"{out.green('✓')} {folder} {made} · {_summary(report)}")
    return 0


def cmd_explain(args) -> int:
    import webbrowser
    from .build import load_team
    from .explain import diagram_page, explain, mermaid
    out = Style(sys.stdout)
    try:
        team, report = load_team(Path(args.team))
    except NotATeam as e:
        print(out.red(f"✗ {e}"), file=sys.stderr)
        return 1
    if team is None:
        print_problems(report, out)
        print(f"{_summary(report)} · " + out.red("fix these first; `explain` reads a team that validates"))
        return 1
    if args.text:
        print(mermaid(team))
    elif args.diagram:
        page = team.folder / f"{team.folder.name}_flow.html" if args.save else \
            Path(__import__("tempfile").gettempdir()) / f"teamrunner_{team.folder.name}_flow.html"
        page.write_text(diagram_page(team), encoding="utf-8")
        print(f"{out.green('✓')} the flow is drawn in {page}")
        webbrowser.open(page.as_uri())
    else:
        print(explain(team))
    return 0


def cmd_skill(args) -> int:
    from .authoring import NotOurs, install_skill, write_agents_md
    out = Style(sys.stdout)
    failed = False
    for assistant, path, what in install_skill():
        shown = f"~/{path.relative_to(Path.home()).as_posix()}" if path.is_relative_to(Path.home()) else str(path)
        if what.startswith("left alone"):
            failed = True
            print(f"{out.yellow('!')} {assistant}: {what}")
        else:
            print(f"{out.green('✓')} {assistant}: {what} · {shown}")
    if args.agents_md:
        try:
            path, what = write_agents_md(Path(args.agents_md))
            print(f"{out.green('✓')} AGENTS.md: {what} · {path}")
        except NotOurs as e:
            failed = True
            print(f"{out.yellow('!')} AGENTS.md: {e}")
    return 1 if failed else 0


def main(argv=None) -> int:
    for stream in (sys.stdout, sys.stderr):  # Windows sends piped output in its old code page, without ✓ or ≈
        if hasattr(stream, "reconfigure") and (stream.encoding or "").lower().replace("-", "") != "utf8":
            stream.reconfigure(encoding="utf-8", errors="replace")
    parser = argparse.ArgumentParser(prog="teamrunner",
                                     description="Run teams of agents from text files.")
    parser.add_argument("--version", action="version", version=f"teamrunner {__version__}")
    commands = parser.add_subparsers(dest="command", required=True)

    v = commands.add_parser("validate", help="check a team's files before running it")
    v.add_argument("team", nargs="?", default=".", help="the team folder (default: this folder)")
    v.add_argument("-v", "--verbose", action="store_true",
                   help="also list each member and where its model comes from")
    v.add_argument("--online", action="store_true",
                   help="also check every key and ask each provider whether its models are available")
    v.add_argument("--json", action="store_true",
                   help="every problem as JSON, with its file, line, value and fix (for assistants)")
    v.set_defaults(func=cmd_validate)

    r = commands.add_parser("run", help="run a team")
    r.add_argument("team", nargs="?", default=".", help="the team folder (default: this folder)")
    given = r.add_mutually_exclusive_group()
    given.add_argument("--message", "-m", help="text for the input `message_goes_to:` names")
    given.add_argument("--file", type=Path, help="a file for the input `message_goes_to:` names")
    r.add_argument("--task-file", type=Path, help="a YAML file of inputs, under `inputs:`")
    r.add_argument("--runs-dir", type=Path, help="keep the run somewhere other than the package's runs/")
    r.add_argument("--rehearse", action="store_true",
                   help="no model: placeholder words and random answers, to check the flow for free")
    shown = r.add_mutually_exclusive_group()
    shown.add_argument("-v", "--verbose", action="store_true",
                       help="everything said in full, tokens, models, decisions and retries")
    shown.add_argument("-q", "--quiet", action="store_true",
                       help="print nothing; the exit code says what happened")
    r.set_defaults(func=cmd_run)

    rs = commands.add_parser("runs", help="list a package's runs, or replay one",
                             description="With no run named, list the runs, newest first. Name one by "
                             "`latest`, by when it ran as the list shows it (`14:32`, `1 Nov 14:32`) "
                             "or by its folder name, to replay what it printed.")
    rs.add_argument("args", nargs="*", metavar="[team folder] [when]")
    rs.add_argument("--team", help="only the runs of this team")
    rs.add_argument("--status", choices=["completed", "failed", "stopped"], help="only runs that ended so")
    rs.add_argument("--all", action="store_true", help="every run, not only the last 20")
    rs.add_argument("--runs-dir", type=Path, help="look here instead of the package's runs/")
    rs.add_argument("-v", "--verbose", action="store_true", help="replay with everything said in full")
    rs.set_defaults(func=cmd_runs)

    sh = commands.add_parser("show", help="one member exactly as it will run, and where each value comes from")
    sh.add_argument("member", help="its file (judge.md, debate/judge.md) or its name in this folder")
    sh.set_defaults(func=cmd_show)

    g = commands.add_parser("gather", help="replace links that point outside the package with real copies")
    g.add_argument("team", nargs="?", default=".", help="any team folder in the package (default: this folder)")
    g.add_argument("--yes", "-y", action="store_true", help="replace them without asking")
    g.set_defaults(func=cmd_gather)

    n = commands.add_parser("new", help="make a new team folder, empty or copied from another team")
    n.add_argument("name", help="the new folder; its name is the team's name")
    n.add_argument("--from", dest="source", metavar="TEAM",
                   help="copy a team folder, or an example by name (fact_checked, reference_check, …), "
                        "with fresh ids")
    n.add_argument("--describe", metavar="TEXT", help="have a model write the team from this description")
    n.add_argument("--model", help="with --describe: the model that writes it, as provider/name")
    n.set_defaults(func=cmd_new, model_factory=None)

    sk = commands.add_parser("skill", help="install or update the authoring skill for Claude Code and Codex",
                             description="Gives AI assistants TeamRunner's instructions for writing a team. "
                             "The skill goes in each assistant's own folder in your home directory, so it "
                             "works in every project; run this again to update it.")
    sk.add_argument("--agents-md", metavar="FOLDER", help="also write AGENTS.md into this folder, "
                    "for assistants that read that file")
    sk.set_defaults(func=cmd_skill)

    x = commands.add_parser("explain", help="what a team will do, in plain words, or its flow as a picture")
    x.add_argument("team", nargs="?", default=".", help="the team folder (default: this folder)")
    shape = x.add_mutually_exclusive_group()
    shape.add_argument("--diagram", action="store_true", help="draw the flow on a page and open it in the browser")
    shape.add_argument("--text", action="store_true", help="print the flow as Mermaid, to paste into GitHub or Notion")
    x.add_argument("--save", action="store_true", help="with --diagram: keep the page in the team folder")
    x.set_defaults(func=cmd_explain)

    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
