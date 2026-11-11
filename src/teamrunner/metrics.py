# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright 2026 Kumar Muthuraman
"""An experiment's metrics (D138 (13), SPEC §5.4): `metrics/`, never the team's.

Two kinds, each listed under `metrics:` in experiment.md:

- **`kind: answer_key`** (a .yaml file): a score against a known answer,
  no model, recomputed every time from the run's `events.jsonl`. `source:`
  names where the team's answer is — `emma.output.flagged_lines`, or
  `everyone.output.<field>` for every member at every turn — with `stage:`
  to take the answer given at that stage; `parse:` turns it into items
  (`line_numbers`: "10, 15" → 10 and 15; `list`: comma-separated); `reports:`
  says what to count: `hits`, `misses`, `false_alarms`, `correct`, or, with
  `compare: {raised: …, reported: …}`, `suppressed` (answers raised but not
  reported).
- **`kind: evaluator`** (a member-format .md file): a model that grades, with
  its own pinned `model:`, `params:`, typed `output:` and the rubric as its
  body. It reads only `{{output}}`, `{{transcript}}`, `{{inputs.<name>}}` and
  `{{<member>.output.<field>}}` — never the condition, seed, costs or models.
  `evaluations:` (default 1) evaluates each run that many times.
"""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass, field
from pathlib import Path

from . import ir
from .loader import split_frontmatter

ANSWER_KEY, EVALUATOR = "answer_key", "evaluator"
PARSES = ("line_numbers", "list")
SOURCE_REPORTS = ("hits", "misses", "false_alarms", "correct")
COMPARE_REPORTS = ("suppressed",)
ANSWER_KEY_WORDS = {"name", "description", "kind", "answer", "source", "stage", "parse", "compare", "reports", "id"}
EVALUATOR_WORDS = {"name", "description", "kind", "model", "params", "output", "evaluations", "id"}
PLACEHOLDER = re.compile(r"\{\{\s*([^{}]+?)\s*\}\}")
SOURCE = re.compile(r"^(\w[\w-]*)\.output\.(\w[\w-]*)$")
EVERYONE = "everyone"


@dataclass
class Source:
    who: str  # a member's name, or "everyone"
    field: str
    stage: str | None = None


@dataclass
class AnswerKey:
    name: str
    path: Path
    answer: frozenset[str]
    reports: list[str]
    source: Source | None = None
    raised: Source | None = None
    reported: Source | None = None
    parse: str | None = None
    kind: str = ANSWER_KEY

    def score(self, events: list[dict]) -> dict[str, float | None]:
        """Each report's value for one run; None when the team never gave the answer."""
        def items(src):
            return _items(_given(events, src), self.parse)
        out = {}
        if self.source is not None:
            got = items(self.source)
            for r in self.reports:
                if got is None:
                    out[r] = None
                elif r == "hits":
                    out[r] = len(self.answer & got)
                elif r == "misses":
                    out[r] = len(self.answer - got)
                elif r == "false_alarms":
                    out[r] = len(got - self.answer)
                elif r == "correct":
                    out[r] = 1 if got == self.answer else 0
        else:
            raised, reported = items(self.raised), items(self.reported)
            for r in self.reports:
                out[r] = None if raised is None or reported is None else len((self.answer & raised) - reported)
        return out


@dataclass
class Evaluator:
    name: str
    path: Path
    model: str
    params: dict
    output: dict[str, ir.OutputField]
    body: str
    evaluations: int = 1
    version: str = ""  # its version hash: settings (ids aside) and rubric
    source: str = ""
    kind: str = EVALUATOR
    placeholders: list[str] = field(default_factory=list)

    def prompt(self, run: Path, record: dict, events: list[dict]) -> str:
        """The rubric with its placeholders filled from one run — and nothing else."""
        def fill(match):
            name = match.group(1)
            if name == "output":
                path = run / "output.md"
                return path.read_text(encoding="utf-8").strip() if path.is_file() else "(no output)"
            if name == "transcript":
                said = [e for e in events if e.get("event") == "agent_message"]
                return "\n\n".join(f"{e.get('from')}:\n{(e.get('content') or '').strip()}" for e in said) \
                    or "(nothing was said)"
            if name.startswith("inputs."):
                value = (record.get("inputs") or {}).get(name[len("inputs."):], {}).get("value")
                return _text(value) if value is not None else "(none)"
            found = SOURCE.match(name)
            value = _given(events, Source(found.group(1), found.group(2))) if found else None
            return _text(value) if value is not None else "(none)"
        return PLACEHOLDER.sub(fill, self.body).strip()


@dataclass
class OutputMeasure:
    """A member's typed output, listed under `metrics:` by its path (D138 (13), amended):
    its value in each run is the member's last answer in that field."""
    name: str  # the path as written: judge.output.winner
    source: Source
    type: str  # choice | number | flag
    options: tuple = ()  # a choice's options, in the order its member declares them
    kind: str = "output"

    def value(self, events: list[dict]):
        return _given(events, self.source)


Metric = AnswerKey | Evaluator | OutputMeasure


def read_outputs(exp, members) -> list[OutputMeasure]:
    """The members' typed outputs `metrics:` names by path, checked against the conditions."""
    out = []
    for written, line in exp.outputs_measured:
        found = SOURCE.match(written)
        who, name = found.group(1), found.group(2)
        if who == EVERYONE:
            exp.problems.error(exp.path, f"`{written}`: a metric reads one member's answer; name the member",
                               line=line, value=written)
            continue
        if members is None:
            continue
        if who not in members:
            from . import vocabulary as V
            hint = V.suggest(who, list(members))
            exp.problems.error(exp.path, f"`{written}`: no condition has a member `{who}`", line=line,
                               value=written, fix=f"did you mean `{hint}`?" if hint else None)
            continue
        field_ = members[who].get(name)
        if field_ is None:
            from . import vocabulary as V
            hint = V.suggest(name, list(members[who]))
            exp.problems.error(exp.path, f"`{written}`: {who} has no typed output `{name}`", line=line,
                               value=written, fix=f"did you mean `{who}.output.{hint}`?" if hint else
                               f"{who}'s typed outputs: " + (", ".join(members[who]) or "none"))
            continue
        if field_.type == "text":
            exp.problems.error(exp.path, f"`{written}` is text, which cannot be counted or averaged",
                               line=line, value=written, fix="to grade text, write an evaluator in metrics/")
            continue
        out.append(OutputMeasure(written, Source(who, name), field_.type, tuple(field_.options)))
    return out


def _text(value) -> str:
    return value if isinstance(value, str) else json.dumps(value, ensure_ascii=False)


def _given(events: list[dict], src: Source | None):
    """What the team answered: a member's last answer (at a stage), or everyone's, gathered."""
    if src is None:
        return None
    said = [e for e in events if e.get("event") == "agent_message" and src.field in (e.get("output") or {})
            and (src.stage is None or e.get("stage") == src.stage)]
    if src.who == EVERYONE:
        return [e["output"][src.field] for e in said] if said else None
    mine = [e for e in said if e.get("from") == src.who]
    return mine[-1]["output"][src.field] if mine else None


def _items(value, parse: str | None) -> frozenset[str] | None:
    if value is None:
        return None
    values = value if isinstance(value, list) else [value]
    out = set()
    for v in values:
        if parse == "line_numbers":
            out |= {str(int(n)) for n in re.findall(r"\d+", _text(v))}
        elif parse == "list":
            out |= {part.strip() for part in _text(v).split(",") if part.strip()}
        else:
            out.add(_text(v).strip())
    return frozenset(out)


# --- reading metric files ----------------------------------------------------------------

def read_metrics(paths: list[Path], problems, members: dict[str, set[str]] | None = None) -> list[Metric]:
    """Every metric file, checked; problems go to `problems`. `members`: each member
    name the conditions have, with its typed output fields, to check `source:`."""
    from .experiment import _yaml
    out = []
    for path in paths:
        text = path.read_text(encoding="utf-8")
        if path.suffix == ".md":
            parts = split_frontmatter(text)
            if parts is None:
                problems.error(path, "an evaluator starts with a settings block (`---`), "
                               "with `kind: evaluator`, `model:` and `output:`")
                continue
            front, body = parts[0], parts[1] or ""
        else:
            front, body = text, None
        try:
            data = _yaml.load(front) or {}
        except Exception as e:
            problems.error(path, f"the settings could not be read: {str(e).splitlines()[0]}")
            continue
        if not isinstance(data, dict):
            problems.error(path, "a metric file holds settings: `kind:` and what that kind needs")
            continue
        from .schema_check import plain
        data = plain(data)
        kind = data.get("kind")
        if kind == ANSWER_KEY and body is None:
            metric = _answer_key(path, data, problems, members)
        elif kind == EVALUATOR and body is not None:
            metric = _evaluator(path, data, body, problems, members)
        elif kind in (ANSWER_KEY, EVALUATOR):
            want = ".yaml" if kind == ANSWER_KEY else ".md"
            problems.error(path, f"a `kind: {kind}` metric is a {want} file")
            continue
        else:
            problems.error(path, f"`kind:` is `{ANSWER_KEY}` or `{EVALUATOR}`"
                           + (f", not `{kind}`" if kind else ""))
            continue
        if metric is not None:
            out.append(metric)
    names = [m.name for m in out]
    for name in sorted({n for n in names if names.count(n) > 1}):
        problems.error(paths[0].parent, f"two metrics are called `{name}`; each needs its own name")
    return out


def _unknown(path, data, allowed, problems) -> None:
    from . import vocabulary as V
    for key in data:
        if key not in allowed:
            hint = V.suggest(str(key), allowed)
            problems.error(path, f"`{key}` is not a setting of this kind of metric",
                           fix=f"did you mean `{hint}`?" if hint else "settings: " + ", ".join(sorted(allowed - {'id'})))


def _source(path, written, stage, problems, members, what: str) -> Source | None:
    found = SOURCE.match(written) if isinstance(written, str) else None
    if found is None:
        problems.error(path, f"`{what}` names a member's typed answer: `<member>.output.<field>`, "
                       f"or `everyone.output.<field>`", value=str(written))
        return None
    src = Source(found.group(1), found.group(2), stage if isinstance(stage, str) else None)
    if members is not None:
        if src.who != EVERYONE and src.who not in members:
            problems.error(path, f"`{what}`: no condition has a member `{src.who}`", value=written)
        elif src.who != EVERYONE and src.field not in members[src.who]:
            problems.error(path, f"`{what}`: {src.who} has no typed output `{src.field}`", value=written)
        elif src.who == EVERYONE and not any(src.field in f for f in members.values()):
            problems.error(path, f"`{what}`: no member has a typed output `{src.field}`", value=written)
    return src


def _answer_key(path, data, problems, members) -> AnswerKey | None:
    errors = len(problems.errors)
    _unknown(path, data, ANSWER_KEY_WORDS, problems)
    answer = data.get("answer")
    if answer is None or answer == []:
        problems.error(path, "`answer:` is the known answer: one value, or a list")
    parse = data.get("parse")
    if parse is not None and parse not in PARSES:
        problems.error(path, f"`parse:` is {' or '.join(f'`{p}`' for p in PARSES)}", value=str(parse))
    reports = data.get("reports")
    if not isinstance(reports, list) or not reports:
        problems.error(path, "`reports:` lists what to count")
        reports = []
    source = raised = reported = None
    if "source" in data and "compare" in data:
        problems.error(path, "a metric has `source:` or `compare:`, not both")
    elif "source" in data:
        source = _source(path, data["source"], data.get("stage"), problems, members, "source")
        allowed = SOURCE_REPORTS
    elif isinstance(data.get("compare"), dict) and set(data["compare"]) == {"raised", "reported"}:
        allowed = COMPARE_REPORTS
        for key in ("raised", "reported"):
            part = data["compare"][key]
            part = part if isinstance(part, dict) else {"source": part}
            src = _source(path, part.get("source"), part.get("stage"), problems, members, f"compare.{key}")
            raised, reported = (src, reported) if key == "raised" else (raised, src)
    else:
        problems.error(path, "an answer key says where the team's answer is: `source:`, or "
                       "`compare: {raised: …, reported: …}`")
        allowed = ()
    for r in reports:
        if allowed and r not in allowed:
            problems.error(path, f"`{r}` is not something this metric can report",
                           value=str(r), fix="it can report " + ", ".join(f"`{a}`" for a in allowed))
    if len(problems.errors) > errors:
        return None
    values = answer if isinstance(answer, list) else [answer]
    return AnswerKey(str(data.get("name") or path.stem), path, _items(values, parse) or frozenset(),
                     [str(r) for r in reports], source, raised, reported, parse)


def _evaluator(path, data, body, problems, members) -> Evaluator | None:
    errors = len(problems.errors)
    _unknown(path, data, EVALUATOR_WORDS, problems)
    model = data.get("model")
    if not isinstance(model, str) or "/" not in model:
        problems.error(path, "an evaluator pins its own `model:`, as provider/name (it never takes a team's)")
    params = data.get("params") or {}
    if not isinstance(params, dict):
        problems.error(path, "`params:` holds the model's settings, such as `temperature: 0`")
        params = {}
    output = ir.output_fields(data.get("output")) if isinstance(data.get("output"), dict) else {}
    if not output:
        problems.error(path, "`output:` names what the evaluator answers, such as `score: 0` and `reason: \"\"`")
    n = data.get("evaluations", 1)
    if not isinstance(n, int) or isinstance(n, bool) or n < 1:
        problems.error(path, "`evaluations:` is how many times each run is evaluated: 1 or more", value=str(n))
        n = 1
    used = [m.group(1) for m in PLACEHOLDER.finditer(body)]
    for name in used:
        found = SOURCE.match(name)
        if name in ("output", "transcript") or name.startswith("inputs."):
            continue
        if found and found.group(1) != EVERYONE:
            if members is not None and found.group(1) not in members:
                problems.error(path, f"`{{{{{name}}}}}`: no condition has a member `{found.group(1)}`")
            elif members is not None and found.group(2) not in members[found.group(1)]:
                problems.error(path, f"`{{{{{name}}}}}`: {found.group(1)} has no typed output `{found.group(2)}`")
            continue
        problems.error(path, f"`{{{{{name}}}}}` is not something an evaluator may read",
                       fix="an evaluator reads only {{output}}, {{transcript}}, {{inputs.<name>}} and "
                       "{{<member>.output.<field>}} — never the condition, seed, costs or models")
    if len(problems.errors) > errors:
        return None
    settings = {k: v for k, v in data.items() if k != "id"}
    canon = json.dumps({"settings": settings, "rubric": body.strip()}, sort_keys=True, ensure_ascii=False, default=str)
    version = "sha256:" + hashlib.sha256(canon.encode("utf-8")).hexdigest()
    return Evaluator(str(data.get("name") or path.stem), path, model, params, output, body, n, version,
                     path.read_text(encoding="utf-8"), placeholders=used)


def members_of(conditions) -> dict[str, dict[str, ir.OutputField]]:
    """Every member name across the conditions, with its typed output fields."""
    from .build import _team
    found: dict[str, dict[str, ir.OutputField]] = {}
    for c in conditions:
        if c.report is None or not c.report.ok:
            continue
        for m in _team(c.report).members.values():
            found.setdefault(m.name, {}).update(m.output or {})
    return found
