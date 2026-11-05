"""The flow: the body of `chair.md`, parsed and checked (SPEC §3.3, D96).

    rounds(*3) -> judge:research -> web -> judge:verdict

    rounds = for_turn -> against_turn

    for_turn = for -> fact_checker
               fact_checker --fail--> for
               fact_checker --pass,*3--> done

The lines before the first named block are the main flow; a line starting
with `->` continues the line above. Each block has one line of steps;
its other lines are decisions. A step is an occurrence, not a name: the
same member may take several turns in one block, and an arrow names a step
by `name` or `name:stage`, which must point to one of them.

Parsing reads the text alone. `check_flow` then looks the names up in the
team, reports what is wrong, and completes the flow (which output each
decision reads). Parallel work `[a, b]` and condition loops are 0.3.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from .ir import DONE, STOP, Block, Bound, Decision, Flow, OutputField, Step
from .problems import Problems
from .vocabulary import suggest

WORD = r"[A-Za-z_][A-Za-z0-9_]*"
STEP = re.compile(rf"^(?P<name>{WORD})(?::(?P<stage>{WORD}))?(?:\((?P<args>[^)]*)\))?$")
BLOCK_DEF = re.compile(rf"^(?P<name>{WORD})\s*=\s*(?P<rest>.*)$")
DECISION = re.compile(r"^(?P<decider>\S+)\s*--(?P<labels>[^>]*?)-->\s*(?P<target>.*)$")
COUNT = re.compile(r"^\*\s*(\d+)$")


@dataclass
class _Arrow:
    decider: str
    labels: list[str]
    target: str
    line: int


@dataclass
class _Draft:
    block: Block
    has_sequence: bool = False
    last_was_sequence: bool = False
    arrows: list[_Arrow] = field(default_factory=list)


# --- parsing --------------------------------------------------------------------

def parse_flow(text: str, path, first_line: int, problems: Problems) -> Flow | None:
    """Parse a chair's script. `first_line` is the file line of the text's
    first line. Returns None if nothing usable could be read."""
    errors_before = len(problems.errors)
    main = _Draft(Block("", line=first_line))
    drafts = {"": main}
    current = main

    for i, raw in enumerate(text.splitlines()):
        line_no = first_line + i
        stripped = raw.split("#", 1)[0].rstrip()
        if not stripped.strip():
            continue
        indented = stripped[0].isspace()
        stripped = stripped.strip()

        definition = BLOCK_DEF.match(stripped)
        if definition:
            name = definition["name"]
            if indented:
                problems.error(path, f"the named block `{name}` should start at the left edge",
                               line=line_no, fix="remove the spaces before its name")
            if name in drafts:
                problems.error(path, f"the block `{name}` is defined twice", line=line_no,
                               fix="rename one of them")
                current = _Draft(Block(name, line=line_no))  # parse it, then drop it
                continue
            current = _Draft(Block(name, line=line_no))
            drafts[name] = current
            if definition["rest"].strip():
                _add_sequence(current, definition["rest"], path, line_no, problems)
            continue

        if current is not main and not indented:
            problems.error(path, "this line is outside every named block",
                           line=line_no,
                           fix="indent it under its block, or move it above the first named block "
                           "if it belongs to the main flow")
            continue

        if stripped.startswith("->"):
            if not current.last_was_sequence:
                problems.error(path, "a line starting with `->` continues a line of steps, "
                               "but there is none just above it", line=line_no)
                continue
            _add_steps(current, stripped[2:], path, line_no, problems)
            continue

        arrow = DECISION.match(stripped)
        if arrow:
            current.last_was_sequence = False
            target = arrow["target"].strip()
            if "->" in target:
                problems.error(path, "a decision points to one step; put what follows it in the flow",
                               line=line_no, value=target)
                continue
            labels = [label.strip() for label in arrow["labels"].split(",")]
            current.arrows.append(_Arrow(arrow["decider"], labels, target, line_no))
            continue

        if "-->" in stripped or "--" in stripped:
            problems.error(path, "this decision should read `decider --option--> step`",
                           line=line_no, value=stripped)
            continue
        if current.has_sequence:
            problems.error(path, "a block has one line of steps; its other lines are decisions "
                           "(`decider --option--> step`)", line=line_no,
                           fix="join this line to the steps above with `->`")
            continue
        _add_sequence(current, stripped, path, line_no, problems)

    if len(problems.errors) > errors_before:
        return None
    if not main.has_sequence:
        problems.error(path, "this chair.md has no flow", line=first_line,
                       fix="write the steps, such as `for -> against -> judge`, "
                       "or delete the file and members take turns in filename order")
        return None
    for draft in drafts.values():
        _resolve_arrows(draft, path, problems)
    if len(problems.errors) > errors_before:
        return None
    blocks = {name: d.block for name, d in drafts.items() if name}
    flow = Flow(main.block, blocks, source="chair.md")
    for block in [flow.main, *blocks.values()]:
        for step in block.steps:
            if step.kind == "name":
                step.kind = "block" if step.name in blocks else "member"
    return flow


def _add_sequence(draft: _Draft, text: str, path, line_no, problems) -> None:
    draft.has_sequence = True
    _add_steps(draft, text, path, line_no, problems)


def _add_steps(draft: _Draft, text: str, path, line_no, problems) -> None:
    draft.last_was_sequence = True
    for token in text.split("->"):
        step = _parse_step(token.strip(), path, line_no, problems, allow_repeat=True)
        if step is None:
            continue
        steps = draft.block.steps
        if steps and steps[-1].kind in (DONE, STOP):
            problems.error(path, f"nothing after `{steps[-1].kind}` would ever run", line=line_no,
                           value=step.label)
        steps.append(step)
    draft.block.sequence_length = len(draft.block.steps)


def _parse_step(token: str, path, line_no, problems, allow_repeat: bool) -> Step | None:
    if not token:
        problems.error(path, "a step is missing between two arrows", line=line_no)
        return None
    if token.startswith("["):
        problems.error(path, "members working at the same time (`[a, b]`) arrives in TeamRunner 0.3",
                       line=line_no, value=token, fix="write them one after another for now: `a -> b`")
        return None
    m = STEP.match(token)
    if not m:
        problems.error(path, f"`{token}` is not a step: write a name, `name:stage` or `name(*3)`",
                       line=line_no, value=token)
        return None
    name, stage, args = m["name"], m["stage"], m["args"]
    repeat = 1
    if args is not None:
        count = COUNT.match(args.strip())
        if " is " in f" {args} ":
            problems.error(path, "a loop that leaves on a condition arrives in TeamRunner 0.3",
                           line=line_no, value=token, fix="repeat it a fixed number of times: `name(*3)`")
            return None
        if not count or int(count[1]) < 1:
            problems.error(path, f"`({args})` should be a count, such as `(*3)`", line=line_no,
                           value=token)
            return None
        if not allow_repeat:
            problems.error(path, "a decision points to a step, not a repeat", line=line_no,
                           value=token, fix="repeat it in the flow instead")
            return None
        repeat = int(count[1])
    if name in (DONE, STOP):
        if stage or args is not None:
            problems.error(path, f"`{name}` takes no stage or count", line=line_no, value=token)
            return None
        return Step(name, name, line=line_no)
    return Step("name", name, stage, repeat, line_no)


def _matches(block: Block, ref_name: str, ref_stage: str | None, upto: int | None = None) -> list[int]:
    steps = block.steps if upto is None else block.steps[:upto]
    return [i for i, s in enumerate(steps)
            if s.name == ref_name and (ref_stage is None or s.stage == ref_stage)]


def _resolve_arrows(draft: _Draft, path, problems) -> None:
    block = draft.block
    where = f"the block `{block.name}`" if block.name else "the main flow"
    for arrow in draft.arrows:
        decider = _parse_step(arrow.decider, path, arrow.line, problems, allow_repeat=False)
        if decider is None:
            continue
        if decider.kind in (DONE, STOP):
            problems.error(path, f"`{decider.name}` cannot decide anything", line=arrow.line)
            continue
        hits = _matches(block, decider.name, decider.stage, upto=block.sequence_length)
        if not hits:
            problems.error(path, f"`{decider.label}` does not take a turn in {where}",
                           line=arrow.line, value=decider.label,
                           fix="a decision belongs to the block where its decider takes a turn")
            continue
        if len(hits) > 1:
            problems.error(path, f"`{decider.label}` takes more than one turn in {where}, "
                           "so this decision could belong to either",
                           line=arrow.line, value=decider.label,
                           fix="give each turn a stage (`name:first`, `name:second`) and use it here")
            continue
        index = hits[0]
        if block.steps[index].repeat > 1:
            problems.error(path, f"`{decider.label}` is repeated with `(*N)`, so it cannot also decide",
                           line=arrow.line)
            continue

        target = _parse_step(arrow.target, path, arrow.line, problems, allow_repeat=False)
        if target is None:
            continue
        if target.kind in (DONE, STOP):
            where_to: int | str = target.kind
        else:
            found = _matches(block, target.name, target.stage)
            if len(found) > 1:
                problems.error(path, f"`{target.label}` takes more than one turn in {where}, "
                               "so this arrow could point to either", line=arrow.line,
                               value=target.label,
                               fix="give each turn a stage (`name:first`, `name:second`) and use it here")
                continue
            if found:
                where_to = found[0]
            else:  # a step reached only by this decision (SPEC §3.3's `con:reply`)
                block.steps.append(target)
                where_to = len(block.steps) - 1

        decision = block.decisions.setdefault(index, Decision("", {}, line=arrow.line))
        if not any(arrow.labels) or "" in arrow.labels:
            problems.error(path, "an arrow label is empty: write `--option-->` or `--*3-->`",
                           line=arrow.line)
            continue
        for label in arrow.labels:
            count = COUNT.match(label)
            if count:
                if decision.bound is not None:
                    problems.error(path, f"`{decider.label}` has two counted ways out; keep one",
                                   line=arrow.line, value=label)
                elif int(count[1]) < 1:
                    problems.error(path, "a count starts at 1", line=arrow.line, value=label)
                else:
                    decision.bound = Bound(int(count[1]), where_to)
            elif not re.fullmatch(r"[A-Za-z0-9_]+", label):
                problems.error(path, f"`{label}` is not an option: options are single words",
                               line=arrow.line, value=label)
            elif label in decision.routes:
                problems.error(path, f"`{decider.label}` has two arrows for `{label}`; keep one",
                               line=arrow.line, value=label)
            else:
                decision.routes[label] = where_to


# --- the default flow -----------------------------------------------------------

def round_robin(workers: list[str]) -> Flow:
    """No chair.md: members take one turn each in filename order (D59)."""
    steps = [Step("member", name) for name in workers]
    return Flow(Block("", steps, sequence_length=len(steps)), source="round-robin")


# --- checking against the team ----------------------------------------------------

@dataclass
class Cast:
    """What the flow checks need to know about the team's members."""
    kinds: dict[str, str]  # name -> llm | toolkeeper | team | unreadable (already reported)
    outputs: dict[str, dict[str, OutputField]]
    may_ask: dict[str, object]  # toolkeeper name -> callable(asker) -> bool


def _loops(index: int, route) -> bool:
    """An arrow back to the decider or an earlier step is a loop (D96)."""
    return isinstance(route, int) and route <= index


def check_flow(flow: Flow, cast: Cast, path, problems: Problems) -> None:
    """Report every flow problem, and fill in which output each decision reads."""
    blocks = flow.blocks
    used_blocks: set[str] = set()
    on_stage: set[str] = set()
    names = sorted(set(cast.kinds) | set(blocks))

    for block in [flow.main, *blocks.values()]:
        for i, step in enumerate(block.steps):
            if step.kind == "block":
                used_blocks.add(step.name)
                if step.stage:
                    problems.error(path, f"`{step.label}`: a stage belongs to a member's turn, "
                                   f"and `{step.name}` is a block", line=step.line)
            elif step.kind == "member":
                if step.name not in cast.kinds:
                    hint = suggest(step.name, names)
                    problems.error(path, f"`{step.name}` is not a member of this team or a named block",
                                   line=step.line, value=step.name,
                                   fix=f"did you mean `{hint}`?" if hint else
                                   "members are named by their file names")
                    continue
                on_stage.add(step.name)
                if cast.kinds[step.name] == "toolkeeper":
                    _check_toolkeeper_step(block, i, cast, path, problems)
        for index, decision in block.decisions.items():
            _check_decision(block, index, decision, cast, path, problems)

    for name, block in blocks.items():
        if name in cast.kinds:
            problems.error(path, f"the block `{name}` has the same name as a member", line=block.line,
                           fix="rename the block")
        elif name not in used_blocks:
            problems.warning(path, f"the block `{name}` is never used", line=block.line)
    _check_recursion(flow, path, problems)

    has_subteams = "team" in cast.kinds.values()  # members may act inside them (0.3)
    for name, kind in cast.kinds.items():
        if kind == "llm" and name not in on_stage and not has_subteams:
            problems.warning(path, f"`{name}` never takes a turn in this flow",
                             fix=f"add `{name}` to the flow, or move the file out of the team")


def _check_toolkeeper_step(block: Block, i: int, cast: Cast, path, problems) -> None:
    """D85: the member just before a toolkeeper step asks it."""
    step = block.steps[i]
    if step.repeat > 1:
        problems.error(path, f"a toolkeeper step cannot be repeated: `{step.label}`", line=step.line)
    if i == 0:
        problems.error(path, f"`{step.name}` is a toolkeeper, so it cannot be the first step: "
                       "someone must ask it", line=step.line)
        return
    if i >= block.sequence_length:
        problems.error(path, f"a decision cannot point to the toolkeeper `{step.name}`: "
                       "the member before it must ask it", line=step.line)
        return
    asker = block.steps[i - 1]
    if asker.kind != "member" or asker.name not in cast.kinds:
        problems.error(path, f"the step before the toolkeeper `{step.name}` should be the member who asks it",
                       line=step.line)
        return
    if cast.kinds[asker.name] == "unreadable":
        return
    if i - 1 in block.decisions:
        problems.error(path, f"`{asker.label}` decides here, so its turn cannot also end with "
                       f"a request to `{step.name}`", line=step.line,
                       fix=f"give the request its own turn: `{asker.name}:ask -> {step.name} -> {asker.label}`")
        return
    if cast.kinds[asker.name] == "toolkeeper":
        problems.error(path, f"`{step.name}` follows another toolkeeper, `{asker.name}`; "
                       "a member must ask it", line=step.line)
        return
    if not cast.may_ask[step.name](asker.name):
        problems.error(path, f"`{asker.name}` must ask `{step.name}` here, but `{step.name}`'s "
                       f"`access:` does not let `{asker.name}` in", line=step.line,
                       fix=f"add `{asker.name}: standard` under `access:` in {step.name}.md")
    after = block.steps[i + 1] if i + 1 < block.sequence_length else None
    if after is not None and after.kind == "member" and after.name != asker.name:
        problems.warning(path, f"`{step.name}`'s reply goes to `{asker.name}`, who asked, "
                         f"not to `{after.name}`, who speaks next", line=step.line,
                         fix=f"if `{after.name}` needs the result, let `{asker.name}` pass it on: "
                         f"`{asker.label} -> {step.name} -> {asker.name}:… -> {after.label}`")


def _check_decision(block: Block, index: int, decision: Decision, cast: Cast, path, problems) -> None:
    step = block.steps[index]
    if step.kind != "member" or step.name not in cast.kinds:
        if step.kind == "block":
            problems.error(path, f"`{step.name}` is a block, and only a member decides",
                           line=decision.line)
        return
    if cast.kinds[step.name] == "unreadable":
        return
    if cast.kinds[step.name] == "toolkeeper":
        problems.error(path, f"`{step.name}` is a toolkeeper, and a toolkeeper does not decide",
                       line=decision.line)
        return
    choices = {f.name: f for f in cast.outputs.get(step.name, {}).values() if f.type == "choice"}
    labels = set(decision.routes)
    if not choices:
        problems.error(path, f"`{step.name}` decides here, but its file declares no options",
                       line=decision.line,
                       fix=f"add, for example, `verdict: [pass, fail]` under `output:` in {step.name}.md")
        return
    if not labels:
        problems.error(path, f"`{step.label}` has a counted way out but no options",
                       line=decision.line,
                       fix=f"add an arrow for each option, such as `{step.label} --pass--> …`")
        return
    fitting = [f for f in choices.values() if labels <= set(f.options)]
    if len(fitting) > 1:
        problems.error(path, f"the options on `{step.label}`'s arrows fit more than one of its outputs: "
                       + ", ".join(f"`{f.name}`" for f in fitting), line=decision.line,
                       fix="give the outputs different options")
        return
    if not fitting:
        every = sorted({o for f in choices.values() for o in f.options})
        for label in sorted(labels - set(every)) or sorted(labels):
            hint = suggest(label, every)
            problems.error(path, f"`{label}` is not one of `{step.name}`'s options",
                           line=decision.line, value=label,
                           fix=f"did you mean `{hint}`?" if hint else
                           "its options are " + ", ".join(f"`{o}`" for o in every))
        return
    out = fitting[0]
    decision.field = out.name
    for option in out.options:
        if option not in decision.routes:
            problems.error(path, f"`{step.label}` can answer `{option}`, but no arrow says where that goes",
                           line=decision.line, value=option,
                           fix=f"add `{step.label} --{option}--> …`")
    looping = [o for o, r in decision.routes.items() if _loops(index, r)]
    if looping and decision.bound is None:
        problems.error(path, f"`{step.label} --{looping[0]}-->` loops back, so `{step.label}` needs "
                       "a counted way out", line=decision.line,
                       fix=f"add a count to an arrow that leaves, such as `{step.label} --*3--> stop`")
    if decision.bound is not None and _loops(index, decision.bound.target):
        problems.error(path, "the counted way out loops back too, so the loop would never end",
                       line=decision.line, fix="point the counted arrow forward, to `done` or to `stop`")


def _check_recursion(flow: Flow, path, problems) -> None:
    reported: set[frozenset[str]] = set()

    def visit(name: str, trail: list[str]) -> None:
        if name in trail:
            cycle = trail[trail.index(name):]
            if frozenset(cycle) not in reported:
                reported.add(frozenset(cycle))
                problems.error(path, "a block runs itself: " + " → ".join(cycle + [name]),
                               line=flow.blocks[name].line,
                               fix="a block cannot contain itself; use `(*N)` to repeat it")
            return
        for step in flow.blocks[name].steps:
            if step.kind == "block":
                visit(step.name, trail + [name])

    for name in flow.blocks:
        visit(name, [])
