"""The intermediate representation: a team as it will run.

Everything here is resolved — every default filled in, every reference
looked up — so the compiler never reads a file and never guesses (D57:
defaults are for authors, not for records). The IR is engine-agnostic
(D17): it says what happens, not how LangGraph is asked to do it.

How a flow runs, as the compiler must implement it:

- A block's steps run in order. A step is a member's turn, a block (run
  to its end), `done` (end this block) or `stop` (end the run).
- `repeat` runs a step that many times in a row (`rounds(*3)`).
- After a decider's turn, its `Decision` picks the next step from the
  option it chose. Without a decision the next step is the one after it.
- A decider's count is how many times it has decided in the current run of
  its block. When the count reaches `bound.count` and the chosen route
  would loop back (to the decider or an earlier step), `bound.target` is
  taken instead (D96).
- A step reached by a decision that is not in the block's sequence ends
  the block after its turn.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

DONE, STOP = "done", "stop"

# Default limit on a member's calls to one toolkeeper in one turn (D86).
CALLS_PER_TURN = 5


@dataclass(frozen=True)
class OutputField:
    name: str
    type: str  # choice | text | number | flag
    options: tuple[str, ...] = ()
    start: object = None  # the value written in the one-line form, if any


@dataclass
class Step:
    kind: str  # member | block | done | stop
    name: str
    stage: str | None = None
    repeat: int = 1
    line: int | None = None

    @property
    def label(self) -> str:
        """How a person refers to this step: `judge:verdict`."""
        return f"{self.name}:{self.stage}" if self.stage else self.name


@dataclass
class Bound:
    count: int
    target: int | str  # a step index in the block, DONE or STOP


@dataclass
class Decision:
    field: str  # the decider's choice output the arrows read
    routes: dict[str, int | str]  # option -> step index, DONE or STOP
    bound: Bound | None = None
    line: int | None = None


@dataclass
class Block:
    name: str  # "" for the main flow
    steps: list[Step] = field(default_factory=list)
    decisions: dict[int, Decision] = field(default_factory=dict)  # by decider's step index
    # Steps from index `sequence_length` on were added by decisions only.
    sequence_length: int = 0
    line: int | None = None


@dataclass
class Flow:
    main: Block
    blocks: dict[str, Block] = field(default_factory=dict)
    source: str = "round-robin"  # or "chair.md"


@dataclass
class Setting:
    value: object
    source: str  # where it came from, for `teamrunner show` (D93)


@dataclass
class Terms:
    """What one member may do with a toolkeeper's tool (D77, D95)."""
    guardrails: dict[str, object]


@dataclass
class Member:
    name: str
    path: Path
    kind: str  # llm | toolkeeper
    id: str | None
    description: str | None
    body: str  # a worker's prompt; a toolkeeper's sign on the door
    # Workers
    model: Setting | None = None
    params: dict[str, Setting] = field(default_factory=dict)
    output: dict[str, OutputField] = field(default_factory=dict)
    inputs: tuple[str, ...] = ()  # the placeholders its prompt uses
    # Toolkeepers
    tool: str | None = None
    base: Terms | None = None
    access: dict[str, Terms | None] = field(default_factory=dict)  # None = deny
    others: Terms | None = None  # anyone not listed; None = refused

    def terms_for(self, asker: str) -> Terms | None:
        if asker in self.access:
            return self.access[asker]
        return self.others


@dataclass
class Input:
    name: str
    default: object = None
    has_default: bool = False


@dataclass
class Team:
    name: str
    folder: Path
    package_top: Path
    id: str | None
    description: str | None
    members: dict[str, Member]
    flow: Flow
    inputs: dict[str, Input]
    message_goes_to: str | None  # the input `--message` fills, resolved (§2)


def output_fields(data) -> dict[str, OutputField]:
    """An `output:` block in its resolved form (D45, D57's inference)."""
    fields = {}
    for name, spec in (data or {}).items():
        if isinstance(spec, dict):
            fields[name] = OutputField(name, spec["type"], tuple(spec.get("options") or ()))
        elif isinstance(spec, list):
            fields[name] = OutputField(name, "choice", tuple(str(o) for o in spec), spec[0])
        elif isinstance(spec, bool):
            fields[name] = OutputField(name, "flag", start=spec)
        elif isinstance(spec, (int, float)):
            fields[name] = OutputField(name, "number", start=spec)
        else:
            fields[name] = OutputField(name, "text", start=spec or None)
    return fields
