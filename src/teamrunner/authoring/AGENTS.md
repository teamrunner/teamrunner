# Writing a TeamRunner team

These are instructions for an AI assistant asked to write a TeamRunner team
from a plain-language description. TeamRunner runs teams of AI agents from
text files: **you write files, never code.** The person you are helping may
not read YAML; they will check your work with `teamrunner explain`.

This describes TeamRunner 0.1. Anything not here does not exist yet, and
`teamrunner validate` rejects it.

## How to work

1. **Understand the job.** Who is on the team, what each member does, in
   what order, who decides what, which tools anyone needs, and what the
   team is given to work on. If the description leaves a choice open, make
   the simplest choice and say so at the end.
2. **Start from an example when one fits** (`teamrunner new <name> --from <example>`),
   or from nothing (`teamrunner new <name>`). The examples, simplest first:
   - `round_robin` — three members, no `chair.md`: one turn each in filename order
   - `multi_agent_debate` — a chair: two debaters for three rounds, then a judge
   - `hiring_committee` — three members choose a candidate, each knowing facts
     the others don't, and a clerk records the decision
   - `orchestrators_lite` — five engineers review code, sharing a model profile,
     with two inputs
   - `fact_checked` — the debate with a fact checker who can send a speech
     back, a web search toolkeeper and a Python sandbox toolkeeper
   - `reference_check` — checks a paper's references: a file as input, three
     toolkeepers, and a decision that can stop the run
3. **Write the files** described below.
4. **Run `teamrunner validate --json <folder>`** and fix every error. Each
   error has a file, a line, the offending value and a suggested fix.
   Repeat until it reports `"ok": true`.
5. **Run `teamrunner run --rehearse <folder>`.** It runs the flow with no
   model, placeholder words and random answers, for free. Check the turns
   come in the order you meant.
6. **Show the person `teamrunner explain <folder>`** and tell them what you
   chose that the description did not say. Do not run the team for real
   unless asked: real runs cost money.

## The files

A team is a folder:

```
my_team/
├── team.yaml          the team's charter (required)
├── chair.md           who acts when (optional)
├── for.md             a member: its file name is its name
├── judge.md
├── web.md             a toolkeeper: a member who holds a tool
└── models/            model profiles (optional, top of the package only)
    └── careful.yaml
```

Never write `id:` or `parent_id:` lines; TeamRunner writes them. Never put an
API key in any file. Keep every name to letters, digits and underscores.

### `team.yaml`

```yaml
name: debate
description: Two debaters argue a topic for three rounds; a judge decides

message_goes_to: topic          # which input `--message "..."` fills (optional)

default_inputs:                 # last: each input the prompts use, with its default
  topic: "Should cities ban cars downtown?"
```

- `name` (defaults to the folder's name), `description` (one line).
- `default_model:` — a model for every member that names none (see Models).
- `message_goes_to:` — needed only when there are several inputs. With one
  input, or one named `message`, it is found by itself.
- `default_inputs:` — every `{{input}}` the prompts use, with the value used
  when a run gives none. An input with no default must be given at every run.
  A team whose prompts use no `{{input}}` takes no task and runs as it is.
- Nothing else. There is no `defaults:`, `uses:` or `report_from:`.

### A member: `<name>.md`

Settings on top between `---` lines, then the member's instructions as plain
prose, written to the member ("You are…").

```markdown
---
description: Checks the factual claims in the latest speech
model: openai/gpt-6-luna
params:
  reasoning_effort: low
output:
  verdict: [pass, fail]
  reasons: ""
---
You are a neutral fact checker. Examine the latest speech on {{topic}}
for checkable factual claims...

Set verdict to fail only if a claim is materially false...
```

- **The file name is the member's name.** Leading digits and underscores are
  dropped (`01_for.md` is `for`). There is no `name:` line.
- `description` — one line, for people.
- `model` — every member needs one, here or as the team's `default_model`.
- `params` — model settings (`reasoning_effort`, `max_tokens`, `temperature`, …). Models
  refuse some settings: GPT-5 takes only temperature 1 while it reasons, so leave
  `temperature` out for reasoning models. `run` and `validate --online` check every
  member's settings before any turn.
- `output` — typed answers the member gives besides its words. Only typed
  answers can steer the flow.
  - `verdict: [pass, fail]` — a choice; options are single words
  - `reasons: ""` — text
  - `score: 0` — a number; `ok: false` — true or false
  - long form: `verdict: {type: choice, options: [pass, fail]}`
- The prompt may use the team's inputs as `{{topic}}`. A member whose prompt
  does not use an input is not given it.
- Each turn the member reads its instructions, then everything said so far,
  each part labelled with who said it, ending "It is your turn." When its
  step has a stage (`judge:verdict`) it reads "It is your turn. Stage:
  verdict." — so a member that acts more than once can be told in its
  instructions what to do at each stage. It is not told the round number;
  say how many rounds there are in its instructions if it should know.
- A file in the folder without the `---` block on top (a README, notes) is
  not a member and is ignored.

### A toolkeeper: a member who holds a tool

Only toolkeepers touch tools. A toolkeeper has no model and no prompt: it
decides who may use its tool by its `access:` table, and its text is the
sign on its door, which every member it lets in is shown.

```markdown
---
description: Searches the web for the fact checker and the judge
kind: toolkeeper
tools:
  web_search_brave: {max_results: 5, calls_per_turn: 2}   # one tool; the base terms
access:
  fact_checker: {calls_per_turn: 6}   # changed terms: these replace the base ones
  judge: standard                     # the base terms
---
I search the web. Send me a query and I return the top five results.
```

- **One tool per toolkeeper.** A member who needs two tools asks two
  toolkeepers. Name the file after what it offers (`web.md`, `sandbox.md`).
- `access:` lists members by name: `standard`, `deny`, or changed terms.
  **Anyone not listed is refused.** `others: standard` lets everyone else in.
- The tools, and the limits each understands:

| Tool | Does | Its limits |
|---|---|---|
| `web_search_brave` | a query → titles, links and snippets (Brave Search); the run log keeps the query, not the results, unless `keep_results: true` | `max_results`, `domains`, `keep_results` |
| `read_page` | a link → the page's text, PDFs too | `domains`, `max_chars`, `types: [pdf]` |
| `read_file` | a file the run was given with `--file` → its text | `max_chars`, `types: [pdf]` |
| `run_code` | Python → its output and errors, in a sandbox | `network: true` (off by default), `paths: [folder]` to read — the code finds it at `/package/<folder>` |

  Every tool also understands `calls_per_turn` (5 if not written),
  `calls_per_run` and `timeout` (seconds). Any other limit is an error.
- **Members use a toolkeeper during their own turn,** as often as their
  limits allow, if they choose to. Say in a member's instructions when it
  should ask ("verify claims by searching the web — ask the web toolkeeper").
- To make a member **always** ask, put the toolkeeper in the flow right after
  it: `judge:research -> web -> judge:verdict`. The member must end its turn
  with a request; the reply goes to that member alone, which reads it at its
  next step. A turn that ends with such a request gives no typed answers.

### `chair.md`: who acts when

Without `chair.md`, members take one turn each in filename order (use `01_`,
`02_` prefixes to set it), and toolkeepers take no turns. With it, its text
is the flow — no `---` block, `#` for notes:

```
# Three rounds; each speech is fact-checked; then the judge decides.

rounds(*3) -> judge

rounds = for_turn -> against_turn

for_turn = for -> fact_checker
           fact_checker --fail--> for
           fact_checker --pass,*3--> done

against_turn = against -> fact_checker
           fact_checker --fail--> against
           fact_checker --pass,*3--> done
```

- `a -> b -> c` — one after another. A line starting with `->` continues
  the line above.
- `name:stage` — a turn with a stage, which the member is told.
- `name(*3)` — exactly three times, a member or a block.
- `block = ...` — a named block, defined at the left edge, its further lines
  indented under it. The lines before the first block are the main flow.
  Each block has one line of steps; its other lines are decisions.
- `decider --option--> step` — a **decision**. The decider is a member with
  a choice in its `output:`; each option needs exactly one arrow. The arrow
  may point to a step in the same block, to `done` (end this block; at the
  top, end the run) or to `stop` (end the run now).
- **A loop must be counted.** An arrow back to the decider or an earlier
  step is a loop, and some arrow from the same decider must carry `*N`:
  `--pass,*3-->` means "on pass, or the third time" — the third time, a
  choice that would loop is overridden. `--*10--> stop` alone works too.
  The count starts afresh each time its block runs.
- A member that decides cannot also be the one who must ask a toolkeeper
  step; give it a separate turn (`fact_checker:ask -> web -> ...`).

### Models

- `model: provider/name` — `openai/gpt-6-luna`, `anthropic/claude-sonnet-4-5`,
  `gemini/gemini-2.5-pro`, `ollama/llama3.2` (a local model).
- Or a profile: `model: careful.yaml`, a file in `models/` at the top of the
  package holding a model and its settings:

```yaml
name: openai/gpt-6-luna
params:
  reasoning_effort: high
```

  A member's own `params` override the profile's, setting by setting.
- Backups: `model:` or `default_model:` may name several, separated by
  commas, in order of preference (`openai/gpt-6-luna, anthropic/claude-sonnet-4-5`).
  The first one available runs, and the run says so. A profile's own `name:`
  names one model; to have a backup with settings, list several profiles.
- Use the model the person asked for. If they named none, use one model for
  the whole team through `default_model:` in `team.yaml`, and say which. Add
  backups only when the person asks for them.

## Not in 0.1

Do not write these; `validate` rejects them as arriving in a later version:
a team inside a team (a subfolder with `team.yaml`), `manager.md`, a rules
chair (`kind: rules`), `whiteboard.yaml` or `{{whiteboard.…}}`, `reads:` /
`writes:`, parallel steps `[a, b]`, loops that leave on a condition
(`name(deal is agreed, *12)`), `kind: human`, `kind: announcer`, custom
tools or a `tools/` folder. If the job needs one, build the closest thing
0.1 allows and tell the person what is missing.
