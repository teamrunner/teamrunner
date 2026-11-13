# TeamRunner

**The experimentation platform for agent organizations.**

> **Early release (0.0.x).** Teams and run folders may need changes between versions; every such change is listed in the [changelog](CHANGELOG.md). Comments are very welcome: [open an issue](https://github.com/teamrunner/teamrunner/issues) or write to hello@teamrunner.ai.

Run a multi-agent system entirely from text files and folders. Change one line and run it again; run it eighty times and get a CSV of what changed.

```
debate/                      ← a package: this folder gets a debate done, anywhere you copy it
├── team.yaml                ← this folder is a team, and its default topic
├── advocate.md              ← an agent: config on top, prompt as prose
├── critic.md
├── judge.md                 ← no chair.md, so they speak in name order: advocate, critic, judge
└── runs/                    ← every run of this team, logged here (git-ignored)

experiments/advocate_model/
└── experiment.md            ← two versions of debate — GPT vs. Claude as advocate, 20 runs each
```

> **This README describes TeamRunner 0.0.5.1**, which finds your teams and experiments by name from anywhere; 0.0.5 added experiments, `record.json`, the remembered workspace, revised browser controls and the hosted demo provider. See [CHANGELOG.md](CHANGELOG.md).

```bash
teamrunner run debate --message "Remote work makes teams more productive"
```

No Python. No graph-building API. No framework vocabulary. If you can edit a text file and run one command, you can run — and *study* — an organization of agents.

### Try the hosted demo

The new `demo` example uses `teamrunner/demoLLM`, a limited, free model service
at `demollm.teamrunner.ai`. It needs no user account or API key. Install TeamRunner
0.0.5 or later, then run:

```bash
teamrunner new first_debate --from demo
teamrunner run first_debate --message "A four-day workweek improves productivity"
```

Prompts leave your computer and go through the demo gateway operated by Serenity
Partners LLC, OpenRouter, and the upstream model provider. The gateway stores
IP-based quota counters. Do not send private or sensitive information to the
demo. For other inputs, choose a model whose data practices meet your needs;
use your own model for larger studies or toolkeepers. The actual responding model
is recorded in each run. Operators:
see the [AWS deployment guide](deploy/demo-gateway/README.md).

---

## Contents

- [Why we built it](#why-we-built-it)
- [The five ideas](#the-five-ideas)
- [Tutorial: your first debate in 10 minutes](#tutorial-your-first-debate-in-10-minutes)
- [Tutorial part 2: your first experiment](#tutorial-part-2-your-first-experiment)
- [Use TeamRunner in your browser](#use-teamrunner-in-your-browser)
- [What lands in a run folder](#what-lands-in-a-run-folder)
  - [What your runs keep, and where your words go](#what-your-runs-keep-and-where-your-words-go)
- [Going further: the worked examples](#going-further-the-worked-examples)
- [What TeamRunner is not](#what-teamrunner-is-not)
- [Status](#status)
- [Publisher, license, and responsible use](#publisher-license-and-responsible-use)
- [Questions, bugs and ideas](#questions-bugs-and-ideas)

---

## Why we built it

### 1. The people who study organizations are not the people who write agent code

Management scientists, operations researchers, and behavioural economists have spent a century studying how the *structure* of a team changes its output: hierarchy vs. flat, who reports to whom, who sees which numbers, what happens when one member has a private incentive.

Suddenly there is a laboratory where you can actually run those experiments — teams of LLM agents, cheap, repeatable, fully observable. And the entry price is: learn Python, learn an agent framework, and write a few hundred lines of orchestration code before you can ask your first question.

That is the wrong tax on the wrong people. In TeamRunner an agent is a Markdown file, and a team is a folder. You edit prose, not code.

### 2. Changing the structure should be a one-line change, not a refactor

In code-first frameworks, the organizational structure is *dissolved into the program*. "What if the fact-checker were a neutral referee instead of each side's own editor?" is a question about org design; answering it means rewriting your graph.

In TeamRunner the structure lives in files, and an experiment **changes copies of those files**, never the files themselves. The founding rule:

> **If it's in a file, it's a factor.**

Models, temperatures, prompts, the flow itself, even who is on the team — all of them are legal things to vary in a designed experiment, because all of them are just values in text files.

### 3. Almost nothing published about multi-agent systems is reproducible

Most multi-agent results are `n = 1` demos: one run, one prompt, one screenshot. LLMs are stochastic; a single run is an anecdote. And even a careful author can rarely hand you the exact artifact that produced their result.

TeamRunner treats reproducibility as a feature, not a chore, with a deliberately honest standard:

**Deterministic where the system controls randomness, statistically rigorous where the model does, fully auditable everywhere.**

- **Deterministic harness.** Every source of randomness *we* own — condition assignment, turn order, sampling of tasks — is seeded from one master seed. Same seed, same experimental design.
- **Statistical, not bitwise, replication.** We do not pretend API models can be replayed from a seed (they can't: some providers offer best-effort seeds, some none, and `temperature=0` is not determinism). So the unit of a result is a *distribution over replications*: the summary gives each condition's mean or shares with a 95% interval, and the tests are yours, in your own analysis. `replications: 1` is deliberately awkward to write.
- **Total artifact capture.** Already now: every member exactly as it ran (model, settings, full prompt), everything said, the model that actually answered, token counts, timings, costs, and the Python, LiteLLM and Deno versions go into the run folder, so you can compare two runs line by line. Every experiment run also keeps an exact copy of the team as it ran.

A team folder is a shareable, forkable, git-diffable package. "Clone this repo and reproduce this study" is the whole point, and experiments, from 0.0.5, complete that workflow.

### 4. Orchestration is crowded. Experimentation is empty.

There are many good ways to *build* a multi-agent application. There is almost no way to *run a designed experiment on one*. TeamRunner is aimed squarely at the second: factorial designs, seeded replications, an evaluation layer, a canonical event log, and a long-format CSV at the end that goes straight into R, Stata, or pandas. All of it works from 0.0.5, from one `experiment.md`.

**Who it's for, today:** researchers studying agent behaviour and organizational structure. **Who it's for next:** eval and prompt-optimization teams in industry, who have exactly the same problem with a different vocabulary — they call it a prompt A/B test, we call it a two-level factor.

---

## The five ideas

**1. An agent is a Markdown file.** YAML frontmatter for configuration, the body is the system prompt as prose. Non-programmers edit prompts in the format prompts actually want to be written in.

```markdown
---
model: openai/gpt-6-luna
params: {reasoning_effort: high}
output:
  winner: [advocate, critic]
  reasoning: ""
---
You are an impartial judge of a formal debate on the motion:

{{motion}}

Decide which side argued better — argument quality and logical clarity,
not your own opinion of the motion.
```

**2. A folder is a team if it contains `team.yaml`.** Everything inside is a member, and a member is one of two things: a **worker** (an agent with a goal) or a **toolkeeper** (the member who holds the keys to a tool). There is no registry file listing your agents; the file browser is the org chart.

The whole folder — team and toolkeepers — is a **package**: copy it anywhere and it gets the same job done. The work itself comes from outside: you hand the team a topic when you run it, or it uses the default written in `team.yaml`.

**3. Every team has a chair, and there are no hidden if-statements.** Someone always decides who acts next, and TeamRunner makes you say who:

- a **chair** (`chair.md`), which follows the script you wrote — deterministic, zero model calls;
- or neither, and members simply take turns in alphabetical order, everyone hearing everyone.

Every routing decision is logged as an event attributed to whoever made it.

**4. Tools sit with a toolkeeper.** Tools are not members. An organization doesn't hand every worker every tool; a factory has a tool crib, with someone at the window who checks who may take what, and everyone else asks there. So the only members who touch tools are toolkeepers — a toolkeeper holds the guardrails (timeouts, no network, which folders), decides by a plain access list rather than a prompt, and its text body is simply what the team is told about it. Who may use a toolkeeper, and on what terms, is up to the toolkeeper's own access list — one toolkeeper can give different members different terms, the way a library gives professors and students different cards.

**5. Every run is kept, and no agent can touch the record.** Every run gets its own folder in `runs/` with every prompt, everything said, every decision about who acts next, every tool call, the model that answered, tokens, time and cost (see [What lands in a run folder](#what-lands-in-a-run-folder)). The run log is the researcher's camera, outside the simulation: the engine does not expose the record to agents as conversation context or as a tool. Keep run folders out of any toolkeeper paths you grant, and review unfamiliar tool configurations; see [SECURITY.md](SECURITY.md).

Experiments are [part 2 of the tutorial](#tutorial-part-2-your-first-experiment).

---

## Tutorial: your first debate in 10 minutes

We'll build a three-agent debate, run it, give it rounds, a fact checker and the web. Zero code throughout. [Part 2](#tutorial-part-2-your-first-experiment) turns it into a 40-run experiment.

> Every step works in a terminal, and from 0.0.4 also in your browser: see [Use TeamRunner in your browser](#use-teamrunner-in-your-browser).

### Step 0 — install

```bash
uv tool install teamrunner      # uv fetches the right Python for you: https://docs.astral.sh/uv/
pip install teamrunner          # or this, if you already have Python 3.10 or later
```

Put your key in TeamRunner's key file, `~/.teamrunner/.env` (in your home folder, outside every team). One key is enough: the one for the provider your team's model uses. This tutorial's team runs on OpenAI:

```
OPENAI_API_KEY=sk-...
```

A team on another provider takes that provider's key instead (`ANTHROPIC_API_KEY`, `GEMINI_API_KEY`, …). The shipped `demo`, `round_robin`, and `multi_agent_debate` examples use `teamrunner/demoLLM` with no key. The longer committee/code-review and tool-using examples use Claude Haiku 5.5 directly and need `ANTHROPIC_API_KEY`. If a required key is missing, the run stops before spending anything and says which one. You can try every step without a key: `teamrunner run --rehearse` runs the whole team with no model, for free.

Keys stay with you, never with your teams: team folders get copied, zipped and sent to people, and a key inside one goes with it. If you already have a key set in your shell, TeamRunner offers to save it to the key file the first time it needs it (or to use it just this once). If it finds a `.env` inside a team folder, it offers to move the key out and delete it there. And it will **refuse to run** if it finds something that looks like an API key inside a spec file.

Models on your own machine work too, free: install [Ollama](https://ollama.com), pull a model (`ollama pull gpt-oss:20b`), and write `default_model: ollama/gpt-oss:20b` in `team.yaml`. If Ollama runs somewhere other than this computer, put its address in the key file as `OLLAMA_API_BASE`. Choose one of about 20 billion parameters or more: the smallest models often fail at exactly what teams need most — answering in a fixed form and asking toolkeepers properly — so they test the wiring better than they stand in for a real team. Before the first turn of a model run, TeamRunner checks the configured models and lists availability problems in one table. Providers can still fail after a run starts, and a failed run can incur charges. `teamrunner validate` runs the same check without a run.

Or open `teamrunner ui` and choose **Set up keys** after selecting a team. It saves keys to the same key file and shows whether each key is present, never its saved value. **Validate** then checks the keys and the models along with the files.

### Step 1 — make a team

```bash
teamrunner new debate
cd ~/teamrunner/teams/debate
```

`new` only makes files — no model is called. It creates the `debate/` folder, a starter `team.yaml`, and a `.gitignore` that keeps run logs out of git. A team named alone goes in your **teamrunner workspace**, the one folder for all your work: the first time, `new` asks where that should be (press Enter for `~/teamrunner`), then makes `debate` in its `teams/` folder and says where. (`teamrunner new ./debate` makes it right where you are instead.)

Two other ways to start, when you'd rather not begin from nothing:

- `teamrunner new my_debate --from fact_checked` copies one of the examples that ship with TeamRunner (or any team folder you name), with fresh ids; each file remembers what it was copied from.
- `teamrunner new my_debate --describe "two debaters argue a topic, a fact checker checks each speech, and a judge decides"` asks a model to write the team for you. It checks its own work with `validate`, never overwrites files, tells you what it cost, and ends by printing `teamrunner explain`, so you can read what it made before you run it. (If you work with an AI coding assistant, `teamrunner skill` gives it the same instructions: it installs a skill for Claude Code and Codex, and `teamrunner skill --agents-md <folder>` writes an `AGENTS.md` for other assistants that read one.)

Or run `teamrunner ui`, which opens your workspace (asking where it should be, if nothing has asked yet); new teams go in its `teams/` folder. Choose **+ Team**, enter `debate`, and select an empty team or a shipped example. Select `team.yaml` on the left to edit it.

This tutorial starts from nothing, so you see every file.

`team.yaml` is the only required file. It marks the folder as a team, says what the team does, and names the model its agents run on:

```yaml
# team.yaml
name: debate
description: Two debaters argue a topic; a judge decides

default_model: openai/gpt-6-luna
```

Two things worth knowing now:

- **There is no version line to write.** TeamRunner keeps one rule instead: **a word in these files never changes its meaning.** When something has to mean something new, it gets a new word, and the old one is retired. So if your TeamRunner meets a word it doesn't know — a newer feature, or a retired one — it stops before spending anything and tells you what to do (`pip install -U teamrunner`, or which word replaced the old one). Every run records the exact TeamRunner version it used, so your 2026 study can be rerun in 2028.
- **You never number your own versions.** The first time you validate or run, TeamRunner adds an `id:` line to each file, with a comment saying what it is: a permanent tag that stays the same when you edit the file, rename it, or copy it to another project. Wherever TeamRunner shows an id, it shows the last eight characters after a "…" (`judge (…KBMCNDPE)`) — search for those to find the full id. That is what later lets you ask "show me every run of *this* judge, across all five of my projects." Each time you edit a prompt, TeamRunner notices the content changed and records it as a new version of the same agent — every run's log keeps a fingerprint of each member's content — automatically, with nothing for you to write.

### Step 2 — write three agents

An agent file is Markdown. Frontmatter configures, body prompts.

In the browser, select the team, choose **+ Member** for each Markdown file below, and paste its contents into the editor. **Save** (Ctrl/Cmd+S), then use **As it will run** to read the instructions with inputs filled in.

`advocate.md`:

```markdown
---
description: Argues in favor of the topic
---
You are debating in favor of this topic:

{{topic}}

Each round, make your strongest remaining argument in one short
paragraph. From round two onward, also rebut your opponent's most
recent point before adding your own. Be persuasive but honest.
```

`critic.md`:

```markdown
---
description: Argues against the topic
---
You are debating against this topic:

{{topic}}

Each round, rebut your opponent's most recent point, then make your
strongest remaining argument against the topic in one short paragraph.
Be persuasive but honest.
```

`judge.md`:

```markdown
---
description: Decides who argued better
output:
  winner: [advocate, critic]
  reasoning: ""
---
You have watched a debate on:

{{topic}}

Decide which side argued better — judge argument quality and logical
clarity, not your own opinion of the topic. Announce the winner and
explain your decision in one paragraph.
```

Four things just happened:

**The team names the model once.** No agent above names a model, so all three run on `default_model:` from `team.yaml`. An agent that should run on a different one says so itself (`model: anthropic/…`), and its own line wins. A model with its settings can live in a profile, `models/careful.yaml`, named as `default_model: careful.yaml` or `model: careful.yaml`. `teamrunner show judge` prints exactly which model and settings the judge will run with, and where each came from. `examples/orchestrators_lite` does both: its five engineers share one profile, `models/engineer.yaml`, named once as the team's `default_model:`. Models churn, so either line can name backups in order of preference, separated by commas: `default_model: openai/gpt-6-luna, anthropic/claude-sonnet-4-5`. Before the first turn TeamRunner picks the first one available, keeps it for the whole run, and says so whenever it isn't the first (`↪ judge runs on anthropic/claude-sonnet-4-5, its 2nd choice — …`); the run's record says so too.

**Alphabetical order is the flow.** With no `chair.md`, members act in alphabetical filename order, one turn each — here `advocate → critic → judge`. That is also why the files above have no `name:` line: an agent's name is its filename. The folder listing *is* the running order, visible in any file browser. Rename to reorder.

**`{{topic}}` is an input**, filled in the next step. Prompts stay reusable; the specific question is a controlled variable, not something an agent invents on the fly.

**`output:` makes the judge's verdict machine-readable.** The judge now returns typed fields (`winner`, a choice; `reasoning`, text) alongside its prose. This matters more than it looks: **declared outputs are the only thing routing and metrics are ever allowed to read.** TeamRunner never regex-scrapes free text to decide what happens next. If control flow depends on it, it has to be declared.

The one-line forms above (`winner: [advocate, critic]` means "a choice between these"; `reasoning: ""` means "text") expand to a long form when you need it: `winner: {type: choice, options: [advocate, critic]}`. The four types are `choice`, `text`, `number`, `flag`.

### Step 3 — give them something to argue about

Add these lines to the end of `team.yaml`:

```yaml
message_goes_to: topic

default_inputs:
  topic: "Remote work makes teams more productive"
```

`default_inputs` is what the team argues about when you don't say otherwise. It can hold as many inputs as the prompts use — one line each, say `topic:` and `audience:` — and a task file (`--task-file`) can give several at once. It comes last because a team only needs it if its prompts take inputs. `message_goes_to` says that a message you send from the command line becomes the `topic`, so the prompts can keep a name that reads well. The topic itself comes from outside the team: changing it — or making it a factor — is how you check that a finding isn't an artifact of one question.

### Step 4 — validate, then run

```bash
teamrunner validate .
```

`validate` is the compiler for people who don't write code, and we treat its error messages as a headline feature: a typo in a flow arrow, a decision option with no arrow, an agent with no model, a worker holding a tool instead of asking a toolkeeper, an input with neither a value nor a default — all of it is caught before a single token is spent. Once the files are right, `validate` also looks for the keys your models need and asks each provider whether it serves them (free, and no run). No keys yet, or no connection? `teamrunner validate . --offline` checks the files only, and a rehearsal needs no key.

```bash
teamrunner run . --rehearse
```

A **rehearsal** runs the team with no model at all: every member says placeholder words, and each typed answer is picked at random from what its file allows, so decisions and loops really happen. It costs nothing and needs no key. It tells you whether the flow goes where you meant — not whether the answers are any good.

```bash
teamrunner run .
```

```bash
teamrunner run . --message "Four-day weeks make teams more productive"
```

The first runs the default topic; the second replaces it. Either way, the run log records the topic and whether it was given or the default.

Or do the editing, validation and running in `teamrunner ui .`: **Validate** checks the same files, and clicking a problem opens its file at the line to fix. Choose **Run…**, keep **Rehearse (free, no model)** checked for a free test, and start. To use a model, clear that checkbox; choose team defaults, a message, a file or a task file. The page shows the conversation as it happens and a **Stop run** button.

What you see while it runs (the words are invented; the layout is the real one):

```
run · debate · default input topic: "Remote work makes teams more productive"
✓ 1 model · 1 key
advocate                                                     3.1s   $0.0060
    “Remote work lets people do focused work without…”
critic                                                       3.3s   $0.0060
    “Collaboration suffers when nobody shares a room…”
judge                       winner = advocate                2.1s   $0.0040
    “The advocate gave the stronger evidence and…”

completed · 3 turns · 8.5s · ≈ $0.0160
runs/2026-10-12_143201_debate

**judge**

The advocate gave the stronger evidence and answered every point…

- **winner:** advocate
- **reasoning:** stronger evidence
```

Each turn shows who spoke, what they decided, how long it took, what it cost and the start of what they said. Costs are estimates — providers report tokens, not prices, so TeamRunner multiplies exact token counts by list prices — hence the `≈`. If you pay different prices, write yours in `~/.teamrunner/prices.yaml`; every run records each price it used and where it came from. `--verbose` shows everything said in full, with tokens and the exact model used; `--quiet` prints nothing at all, for scripts and experiments, and reports how the run went through its exit code and the run folder.

Later, `teamrunner runs` lists every run of this package — when, which team, the input, the result (`winner = advocate`), status, time and cost — and `teamrunner runs latest` (or `teamrunner runs 14:32`) shows one again exactly as it looked while it ran.

In the browser, choose **Run history** and open a run to reread its conversation, output, path or record in the **Run** tab. Select two runs with the checkboxes and choose **Compare selected runs** to see member versions, outputs, cost and paths side by side.

That's a working multi-agent system: four text files, one command.

### Step 5 — take control of the flow

Alphabetical order got us one turn each. A real debate has rounds. Give the team a chair — add `chair.md`, which is simply the script:

```
# Three rounds of advocate and critic, then the judge decides.

rounds(*3) -> judge

rounds = advocate -> critic
```

The flow grammar is deliberately tiny — it is a whiteboard sketch that happens to be executable:

- `a -> b` — sequence. `b:label` attaches a **stage** annotation, which is recorded in the events and injected into the agent's context so it knows where it is (`advocate:opening`, `critic:closing`).
- `rounds(*3)` — **repeat** a step exactly three times.
- `name = …` — a **named block**: any step name can be defined on its own line, as `rounds` is here.
- **Every line stands on its own.** Write the lines in any order, indented or not: `rounds = advocate -> critic` may come before `rounds(*3) -> judge` or after it. Lines join where one ends and the next begins, so a long flow can be written as `a -> b -> c` and `c -> d -> e`.
- `#` starts a **note** for the reader. If the chair ever needs settings, they go in a frontmatter block at the top, like any member file.
- `done` ends the flow — or, inside a named block, just that block; `stop` ends the whole run immediately.

Now the debate runs `advocate → critic → advocate → critic → advocate → critic → judge`. To see it as a picture, `teamrunner explain --diagram` opens the flow as a flowchart in your browser. Or, in the local interface, refresh the file tree after adding `chair.md`, select it, and choose **Flow diagram**. Save an edit to redraw the diagram and plain-words summary.

### Step 6 — add a fact checker and a decision

Suppose we want each speech fact-checked before it stands. Write a fact checker with a declared `output:`:

`fact_checker.md`:

```markdown
---
description: Checks the factual claims in the latest speech
output:
  verdict: [pass, fail]
  reasons: ""
---
You are a neutral fact checker in a formal debate. Examine the latest
speech for checkable factual claims and verify them by searching the
web (ask the web toolkeeper). Search only for claims you cannot confirm
from what you know; most speeches need one or two searches. Minor
exaggeration is fine; invented statistics, misattributed quotes and
false claims are not.

Set verdict to fail only if a claim is materially false, and list each
false claim with a one-line correction in reasons. Otherwise set it to
pass.
```

…and wire its decisions into the chair's script:

```
# chair.md
# Three rounds. Each speech is fact-checked; a failed speech gets two
# more tries, then the debate moves on. Then the judge decides.

rounds(*3) -> judge

rounds = advocate_turn -> critic_turn

advocate_turn = advocate -> fact_checker
                fact_checker --fail--> advocate
                fact_checker --pass,*3--> done

critic_turn = critic -> fact_checker
              fact_checker --fail--> critic
              fact_checker --pass,*3--> done
```

`--pass,*3--> done` reads "on pass, or the third time, go to done": the comma means "or", and `*3` means "the third time". Here `done` ends only `advocate_turn` (or `critic_turn`), not the debate: that speaker's turn is over and the debate moves on to the next speaker. A speech that fails three times still stands — but everything said is heard by everyone, so the judge has heard the fact checker fail it, and the judge's prompt says to weigh that. Want a different ending? `fact_checker --*3--> stop` ends the whole run instead (the future Deb8flow recast uses this pattern); a forfeit that hands the debate to the other side needs a whiteboard item, which comes in 0.3.

The indentation is only for reading. The fact checker takes a turn in both blocks, so each decision line belongs with the nearest line above it that holds the fact checker. A labelled arrow may also sit inside a line of steps, so this says the same thing:

```
advocate_turn = advocate -> fact_checker --pass,*3--> done
fact_checker --fail--> advocate
critic_turn = critic -> fact_checker --pass,*3--> done
fact_checker --fail--> critic
```

An agent whose declared options have labelled outgoing arrows is a **decider**, and its labelled arrows are a **decision**. That's a role, not a different file format — any agent can be one. `done` inside a named block ends that block, so a passed speech hands over to the next speaker.

Two rules make this safe:

**Branch labels are checked against declared options.** `--puss-->` is a validation error, not a 2 a.m. surprise. Every option needs exactly one arrow.

**Loops are legal only through a decider, and only with a limit.** An arrow back to an earlier step needs a counted way out: some arrow from the same decider must carry `*N`. On the Nth time the decider's choice still counts if it leaves the loop (a pass on the third try is a pass); only a choice to go round again is overridden. When running out should go somewhere else, give the count its own arrow: `fact_checker --*3--> stop`. There is no `goto`, no labels-and-jumps: a flow reads top to bottom and always ends.

And note the split that makes components reusable: the fact checker's **`.md` owns how to decide**; the **flow owns what each decision means here**. The same fact checker checks `advocate` and `critic`, because neither is written into its file.

### Step 7 — give the team the web, through a toolkeeper

The fact checker's prompt says "ask the web toolkeeper", so the team needs one. Its searches go through Brave Search, so add a Brave Search API key to the key file as `BRAVE_API_KEY`. Searches cost half a cent each at Brave's list price, and a run's cost counts them: the totals show models and tools separately. Agents never hold tools themselves. A tool is reached through a **toolkeeper**: a member with no model and no prompt, which holds the tool, checks who is asking, and enforces the limits — like the attendant at a factory's tool crib, who checks who may take which tool and how many, and logs it.

`web.md`:

```markdown
---
description: The web toolkeeper — searches the web, on different terms for different members
kind: toolkeeper
tools:
  web_search_brave: {max_results: 5, calls_per_turn: 2}   # the base terms: the strictest
access:
  fact_checker: {calls_per_turn: 3}                 # a few claims a speech may need checking
  judge: standard                                   # the base terms
---
I search the web. Send me a search query and I return the top five
results with their sources.
```

Read the file top to bottom:

- **`tools:`** names the tool and its **base terms** — five results, two searches a turn.
- **`access:`** lists who may use it, one line per member. The fact checker gets a better card (three searches a turn); the judge gets the `standard` terms. Anyone not listed is refused — here the debaters, who argue from what they know. One toolkeeper, different terms for different members — the way a library gives professors and students different cards. (A toolkeeper open to everyone says `others: standard` beside the table.)
- **The body is the sign on the door.** It is what the members who may use the toolkeeper are told about it. They are never shown the limits; they simply get "limit reached" if they go past them. TeamRunner enforces the terms, not the agent's good behaviour.

**Using the toolkeeper is a side trip, not a step.** Nothing in the flow mentions `web`. During its own turn the fact checker may search as often as it needs — or not at all — up to its limit, and each result comes back to it alone. The choice to search, the toolkeeper's yes or no, and the search itself are all logged. (If you write no limit, a member gets five calls a turn, so a side trip always ends.)

**When a search must happen, put the toolkeeper in the flow.** Say the judge must check the most disputed claims before ruling. Make it a step:

```
rounds(*3) -> judge:research -> web -> judge:verdict
```

A toolkeeper step means the member just before it must end its turn with a request to the toolkeeper (that turn's answer is the request, so it gives no typed outputs); the reply goes back to that member, who reads it at its next step. The labels `research` and `verdict` tell the judge which part of its job it is on — its prompt says what to do at each (see `examples/fact_checked/judge.md`). Optional use stays out of the flow; required use is a step. Both are visible. Nothing in the flow line marks `web` as a toolkeeper — its own file says `kind: toolkeeper` — but `teamrunner explain --diagram` draws toolkeeper steps in their own shape, so you can tell them from members' turns at a glance.

`teamrunner explain .` prints, in plain English, who may use which toolkeeper and on what terms.

TeamRunner has six built-in tools: `web_search_brave` (Brave Search), `read_page` (a link in, the page's text out), `read_file` (a file the run was given with `--file`, its text out), `run_code` (Python in a sandbox: no internet unless the toolkeeper allows it, and files written only in the run's own work folder), `read_email` (one email from a folder of `.eml` files in the package) and `save_draft` (a reply saved in the run's `work/outbox/`, never sent, addressed only to the email's sender unless `reply_only: false`). The sandbox is Python running in WebAssembly. Deno installs with TeamRunner, and Pyodide is downloaded from npm on first use; it can use packages built for it (numpy, pandas, scipy, sympy and many more), but not every package. `examples/fact_checked` gives the fact checker a second toolkeeper, `sandbox.md`, so it can check the sums in a claim by running them. A toolkeeper holds one tool for now, so its limits always mean one thing; a member who needs two tools asks two toolkeepers, as the fact checker does here. `examples/reference_check` uses `read_page` and `read_file`: it checks every reference in a paper.

---

## Tutorial part 2: your first experiment

Here's the question that started the whole project: **does it matter which model plays the advocate?** A single run can't tell you. Models answer differently every time, so one win is an anecdote. You need each version many times, in a fair order, counted the same way. That's an experiment, and it's one more file.

### Step 8 — write the experiment

Don't touch the debate. Experiments go in your workspace's `experiments/` folder:

```bash
mkdir -p ~/teamrunner/experiments/advocate_model
```

and in it, one file, `experiment.md`:

```markdown
---
name: advocate-model
conditions:
  gpt:
    from: debate
    change:
      advocate.model: openai/gpt-6-luna
  claude:
    from: debate
    change:
      advocate.model: anthropic/claude-sonnet-4-5

replications: 20        # 2 conditions × 20 = 40 runs
seed: 42

metrics:
  - judge.output.winner

limits:
  max_concurrent: 4
  max_cost_usd: 40.00
---

## Question

Does the model playing the advocate change who wins the debate?
```

Read it top to bottom:

- **`conditions:`** are the versions you compare. Each one starts `from:` a team and says what to `change:` in it. `advocate.model` means the `model` setting of the member `advocate`; `advocate.prompt: file:variants/bold.md` would swap its prompt, and `judge: file:variants/strict_judge.md` a whole file. A condition with no `change:` (`control: debate`) is the team as it is — the control, written as a condition like any other. A name alone (`from: debate`) is the team `teams/debate` in your teamrunner workspace; anything with a slash (`../../debate`, `~/projects/debate`) is a path from the experiment's folder.
- **Your team is never edited.** Each changed condition is built as a folder of its own under `experiments/advocate_model/conditions/`, rebuilt before every run, so you can open it, rehearse it or run it alone.
- **`replications: 20`** runs each condition twenty times. Fewer than two needs `allow_single_run: true`: we made the anecdote the awkward thing to write.
- **`seed: 42`** fixes everything TeamRunner controls: the order of the runs and each run's own seed. It can't make a model give the same answer twice; it makes the design the same every time.
- **`metrics:`** says what to measure. `judge.output.winner` is the judge's typed answer — declared outputs are the only thing metrics ever read. A metric can also be a file in `metrics/`: an answer key that scores against a known answer, or an evaluator, a model with its own rubric that grades each run without being told which condition it came from. The rubric shows the evaluator the run through placeholders: `{{transcript}}` (everything said), `{{output}}` (the run's `output.md`), `{{judge.output.winner}}` (a member's typed answer) and `{{inputs.topic}}` (an input). An evaluator with none of the first three is refused, since it would grade nothing.
- **`limits:`** — `max_concurrent` keeps you inside your provider's rate limits; `max_cost_usd` limits estimated spending within TeamRunner (below), not provider billing.
- **The body** — your question, hypothesis, reasons — is for people. Every run records it.

For many factors at once, use `base: debate` and `factors:` instead of `conditions:`. Each factor lists levels with a `label:` and a `change:` mapping; TeamRunner generates every combination of levels.

### Step 9 — plan, rehearse, run

```bash
teamrunner experiment advocate_model --plan
```

`--plan` builds every condition, checks it as a team, and shows what would happen — spending nothing: the conditions, the difference between them file by file (``advocate.md  changed  `model`: openai/gpt-6-luna → anthropic/claude-sonnet-4-5``), the order of runs, and the cost estimate. The runs go in **rounds**: each round runs every condition once, in an order drawn from the seed, so if anything stops the experiment early, the conditions are at most one run apart.

```bash
teamrunner experiment advocate_model --rehearse
```

A rehearsal runs all 40 with no model, for free, so you see the whole thing work end to end — and it ends with what the real experiment would cost. Then run it for real. Besides the Brave key from step 7, it needs an Anthropic key as well as the OpenAI one:

```bash
teamrunner experiment advocate_model
```

```
experiment · advocate-model · 40 runs (2 conditions × 20)
Estimated cost ≈ $2.20. 6% of max ($40.00)
  ✓ round  1 · claude   completed  13 turns   41.2s   $0.0820  runs/2026-11-12_101502_claude_r01
  ✓ round  1 · gpt      completed  13 turns   38.7s   $0.0790  runs/2026-11-12_101502_gpt_r01
  …
```

**Budget checks use estimated costs.** Before every model call, TeamRunner reserves an estimated upper cost; a call that does not fit the remaining estimated budget is not sent, and that run stops. Actual provider charges can exceed this budget; the check is not a provider billing cap. See the cost-overrun notice below. Nothing asks you anything: the estimate says what to expect, and a second line appears once it passes 60% of the ceiling, a warning after 90%. If the estimate is over the ceiling, nothing starts. To allow more, raise `max_cost_usd`, or give `--max-cost <usd>` for this one session.

**Running it again picks up where it left off.** Stopped with Ctrl-C, cut off at the ceiling, or a provider failed? Run the same command again: completed runs are kept, and each interrupted one is redone in its slot with the same seed, reusing its recorded turns and calling models only from where it stopped. If you edit the team or a condition after runs were made, the command stops, says what changed, and offers `--restart` (the changed study from round 1) or `--keep-unchanged` (keep the runs of conditions that didn't change).

### Step 10 — read the result, and take it with you

When the runs finish, the summary prints (the numbers are invented; the layout is real):

```
advocate-model · 40 of 40 runs completed · $2.31 runs

judge.output.winner    n   share by answer, 95% interval
  gpt                 20   advocate 65% [43%, 82%] · critic 35% [18%, 57%]
  claude              20   advocate 45% [26%, 66%] · critic 55% [34%, 74%]

cost_usd               n    mean     sd   95% interval
  gpt                 20    0.08   0.01   [0.07, 0.09]
  claude              20    0.08   0.01   [0.08, 0.09]
```

It describes; it does not test. Per condition: how many runs, and for each measure its mean or shares with a 95% interval. Cost and time are always there. `teamrunner experiment advocate_model --summary` prints it again at any time, calling nothing. Tests belong in your own analysis, so take the data out:

```bash
teamrunner experiment advocate_model --export results/
```

Five long-format tables, joined by `run_id`: `runs.csv` (one row per run: condition, round, seed, status, cost), `turns.csv` (everything said), `outputs.csv` (every typed answer), `metrics.csv` and `evaluations.csv`. Straight into R:

```r
library(readr); library(dplyr)
runs    <- read_csv("results/runs.csv") |> filter(counts)
metrics <- read_csv("results/metrics.csv")
metrics |> inner_join(runs, by = "run_id") |>
  count(condition, value)              # who won, per condition
```

Every run's folder keeps `resolved/`, an exact copy of the team as it ran, so a condition can be rerun years later, or handed to someone who wants to check your result.

---

## Use TeamRunner in your browser

The local browser interface was introduced in 0.0.4. The workspace layout and controls below are those of 0.0.5:

```bash
teamrunner ui
```

Plain `teamrunner ui` opens your **teamrunner workspace**, the one folder that holds all your work, from wherever you type it: your teams in its `teams/` folder and your experiments in `experiments/`. The first time (here or in `teamrunner new`), it asks once where your workspace should be; pressing Enter makes a `teamrunner` folder in your home folder, with `teams/` and `experiments/` inside. The answer is kept as `TEAMRUNNER_WORKSPACE` in `~/.teamrunner/.env`, beside your keys; the same variable set in your shell wins. **+ Team** in the page, like `teamrunner new <name>`, puts the new team in `teams/`; a name with a slash is used as the path it says.

The commands find your work by name from anywhere: `teamrunner new debate`, `run debate`, `validate debate` and `runs debate` all mean `teams/debate` in your workspace, and `teamrunner experiment study` means `experiments/study`; an experiment's `from: debate` means the same team. Anything that starts with `/`, `~`, `./` or `../`, or has a slash in it (`teams/debate`), is a path from where you are, used as written: `teamrunner run .` runs the team you are in, and `teamrunner new ./debate` makes a team right here.

- `teamrunner ui debate` opens another folder, this once: a single team, or a folder of examples. Your workspace stays as it was.
- `teamrunner ui <folder> --remember` makes that folder your teamrunner workspace from now on.
- `teamrunner gui` is an alias.

The command opens a browser tab and prints its local address. Keep that terminal running while you work; press Ctrl+C there to stop the server. If the tab does not open, paste the full printed address, including its token, into your browser.

The page works on the same files as the CLI. Choose a team or file on the left. You can create an empty team or copy a shipped example, add members, edit and save, rename members, or move files to the system trash. The editor warns about unsaved edits and files changed by another editor. Validation points to the file, line and suggested fix. **As it will run** fills in a member's instructions; **Flow diagram** shows the team's flow and summary. For a ready-made flow to edit, start from the `fact_checked` example.

**Run…** accepts team defaults, a message, a file or a YAML task file. Rehearsal is selected by default for a team with no prior run; it uses placeholder words and random answers, needs no key and costs nothing. A model run uses your configured provider and its normal charges. The interface validates before starting, allows one active run at a time, streams the recorded events, and can stop the run cleanly. Key setup uses `~/.teamrunner/.env`; stored key values are never displayed.

The page has three tabs. **Files** is the team. **Run** shows one run: the one in progress, as it happens, and once it is saved, the same run with its output; a dot on the tab means a run is in progress. **Run history** is the archive: filter by team or status, search the message and output, and open a run, which shows it in **Run**:

- **Conversation:** member messages, decisions, tool calls, retries, tokens, models and cost, with a toggle for what each member could read.
- **Path:** the saved flow with taken arrows highlighted and transition counts. Older logs without a saved engine path show only observed completed-turn transitions, labelled accordingly.
- **Output:** the rendered `output.md`.
- **Record:** inputs, numbered member versions, recorded settings and files. Version numbers come from the available run history.

Select two runs to compare their outputs, cost, time, paths and member versions. Exact member source diffs are available for new logs that record the source and its fingerprint; older logs show their recorded prompts, which may include substituted inputs.

**Re-run with these inputs…** uses the current team with the old run's input values and copied input files. It does not restore old member versions. **Edit a copy of the saved team…** appears only when a run contains `resolved/team.yaml`; it creates an independent team with fresh IDs and lineage. Open the teams' parent folder to give that copy a place outside the original team. Ordinary runs do not yet save a complete resolved team, so their original files cannot be reconstructed from prompts alone.

Every run has **Open folder** and raw-file download links. The editor stays inside the folder you opened, and run records remain read-only. The server listens only on this computer at `127.0.0.1`, with a random access token. Editor, Markdown and diagram assets are bundled for offline use; model calls and online checks still need their usual services. The page adds no database or separate project format, and shows equivalent CLI commands alongside actions.

## What lands in a run folder

Every run is logged in a `runs/` folder at the top of the package you ran. The folder is git-ignored, so sharing the package never shares its history.

A run folder holds three things: `record.json` (when, which team, the inputs, a fingerprint of the package, the TeamRunner, Python and LiteLLM versions, status, cost and tokens), `events.jsonl` (below) and `output.md`, in a folder named for when and what you ran (`runs/2026-10-12_143201_debate/`) — plus `inputs/`, a copy of any file the run was given, and `work/`, where `run_code` keeps what it writes. (Folders from before 0.0.5 have `run.json` instead, and are still read.) An experiment keeps its runs in its own folder, each with two more parts:

```
experiments/advocate_model/runs/2026-11-12_101502_claude_r07/
├── record.json       ← the receipt: + condition, replicate, round, seeds, the study's hash
├── events.jsonl      ← the camera
├── output.md
├── resolved/         ← the exact team this condition ran
└── evaluations/      ← one line per evaluation, when an evaluator grades the run
```

**`record.json`** in an experiment run also records the condition and what it changed, the master and run seeds, the round, and a hash of the study as it stood, so a later edit never silently mixes with earlier runs. Every member's model as it actually answered (what `openai/gpt-6-luna` *actually was* that day) is in `events.jsonl`. Costs are estimates based on reported token counts and configured or bundled list prices; provider-reported charges, when available, are recorded separately. Neither field guarantees the final bill.

**`events.jsonl`** is one JSON object per line, append-only, in TeamRunner's own schema:

| Event | What it records |
|---|---|
| `run_started` | the inputs, and every member exactly as it will run — model, settings, prompt; from 0.0.4, also each member's source text, content fingerprints and the declared flow graph |
| `agent_message` | from, **to** (the *realized* topology), stage, attempt, **what it could read**, everything it said, typed outputs, the model actually used, tokens, cost, time |
| `route_decision` | who decided, which option, which arrow — the audit trail for "no hidden if-statements" |
| `tool_call` | who asked, which toolkeeper, the toolkeeper's yes or no, tool, arguments, the full result (for a search, how many results came back, unless `keep_results: true`) |
| `check`, `retry`, `warning`, `error`, `run_finished` | the pre-run check, retries, warnings, failures, and the totals; from 0.0.4, `run_finished` also saves the path taken, for arrow counts |

A field never changes its meaning between versions — later versions only add — so an analysis script you write today keeps working.

Query it with DuckDB directly over the files, or `--export` an experiment to CSV when you want to leave.

**`resolved/`** is the quiet favourite. In every experiment run it is an exact copy of the team that ran, with the condition's changes applied — nothing added, nothing left out. `diff -r` between two runs' `resolved/` shows exactly what differed between their conditions. And:

```bash
teamrunner run ~/teamrunner/experiments/advocate_model/runs/2026-11-12_101502_claude_r07/resolved
```

runs that exact team again. Every experiment run folder is a self-contained replication package.

### What your runs keep, and where your words go

**A run folder keeps everything, in plain text, on your machine**: every prompt, everything every member said, every page a toolkeeper read, a copy of any file you gave the run, and whatever `run_code` wrote. Open any of it in a text editor. That is what makes a run checkable, and it also means a run folder holds whatever you put into the run, so share one only when you would share all of it.

**Key values are not deliberately recorded.** Do not paste secrets into inputs or prompts: those are logged. `record.json` records only where each configured key came from (`"OPENAI_API_KEY": "key_file"`), never the key. Keys live in `~/.teamrunner/.env`, which only you can read, outside every team.

**Sharing a team through git does not share its runs**: `teamrunner new` puts `runs/` in the team's `.gitignore`. If you zip or copy the folder yourself, leave `runs/` out.

**What you send a hosted model goes to its provider** — OpenAI, Anthropic, Google — under that provider's terms, as with any app that uses them. A local Ollama endpoint processes model requests on your machine; a remote Ollama endpoint receives them over the network. Tools, checks and downloads can still contact other services. A `web_search_brave` sends its query to Brave; `read_page` fetches the page from its site. Search results are the one thing a run does not keep: the member reads them, the log keeps the query and how many came back, because Brave's terms allow storing results only on plans with storage rights. If yours has them, `web_search_brave: {keep_results: true}` keeps them. Brave's terms also forbid using its results to evaluate or benchmark AI models; check them before a study.

**Network requests and the demo gateway.** Hosted model requests go to the
configured API endpoint using your selected models and credentials. The
`teamrunner/demoLLM` demo instead sends prompts and conversation context through
the gateway operated by Serenity Partners LLC, then OpenRouter and its upstream
model provider. Do not send private or sensitive information to the demo. The
gateway stores IP-based quota counters; its supplied configuration does not log
prompt/response bodies or keys. Upstream providers have their own data practices.

The local client sends no separate usage telemetry to the publisher. Model
requests, web tools, provider model-list checks, runtime/package downloads,
diagram assets, and configured integrations can still contact external services.
Local models do not prevent those other network requests. See
[LEGAL.md](LEGAL.md) for the complete data and risk disclosures.

---

## Going further: the worked examples

Seven examples ship with TeamRunner, in `examples/`, simplest first. Copy one by name to start from it: `teamrunner new my_team --from orchestrators_lite`. Most are published multi-agent papers or projects, recast as TeamRunner files.

The introductory `demo`, `round_robin`, and `multi_agent_debate` examples use the free, limited `teamrunner/demoLLM` service. No API key is needed:

```bash
teamrunner new my_debate --from round_robin
teamrunner run my_debate
```

`hiring_committee`, `orchestrators_lite`, `fact_checked`, and `reference_check` use `anthropic/claude-haiku-5-5` directly. Add `ANTHROPIC_API_KEY=your-key` to `~/.teamrunner/.env`, outside every team folder, with API credits available. The committee and code-review meetings are longer than the introductory demos, so they use your own model allowance. No example requires an OpenRouter key.

Add `--rehearse` to check a flow without calling a model; a rehearsal does not verify live model or tool behavior. The two source-checking examples read public pages and Crossref without extra keys; access restrictions, rate limits and missing abstracts or PDFs can still limit their evidence. The fact checker's Python sandbox downloads its runtime on first use. The code-review example uses illustrative model settings rather than the source paper's model.

| Example | Recast of | What it adds |
|---|---|---|
| **`demo/`** | — | A short first debate with concise speeches and a typed verdict, using the free hosted model. |
| **`round_robin/`** | — | The simplest team: three members, no chair file — one turn each, in filename order, and a typed verdict. |
| **`multi_agent_debate/`** | muthuspark/multi-agent-debate | A chair: the debate as a script, with a repeated block of rounds. |
| **`hiring_committee/`** | Stasser & Titus 1985 (hidden profile) | A committee picks a candidate when the key facts are spread across its members: facts only one member knows, kept in that member's own file; a stage in the chair; a clerk who records the decision; and both meeting formats in one `chair.md`, one commented out. |
| **`orchestrators_lite/`** | Fukui 2026 (lite) | Five engineers review code against planted bugs: a model profile the whole team shares, and two inputs. |
| **`fact_checked/`** | — | The debate with a fact checker: decisions that send a speech back, counted tries, and toolkeepers — a public-source reader with different terms for different members, a required source-reading step, and a Python sandbox. |
| **`reference_check/`** | sciwrite-lint (Samsonau 2026) | Checks every reference in a paper: that it exists, that its details are right, and that the cited paper says what the citing sentence claims. A paper given as a link or a file (`--file`), toolkeepers holding `read_page` and `read_file`, the same tool under two toolkeepers with different `domains`, a decision that can stop the run, and batches of ten as a loop with a counted exit. |

---

## What TeamRunner is not

**Not another agent framework.** TeamRunner is for asking *questions about* teams of agents and, with replications, getting statistically defensible answers. Its constraints (declared outputs, bounded loops, no free-text routing) are what make the answers trustworthy, and they are also what an organization wants from a team it runs for real: every decision logged, every loop bounded. If you need a general-purpose agent framework today, use one.

**Not a programming language.** Conditions are exactly `<field> is <value>` and `is not`. There is no arithmetic, no boolean algebra, no expressions. Every time this has been tempting, the answer has been the same: the judgment belongs in an agent's prompt, and the wiring belongs in the flow. That line is load-bearing, and we intend to hold it.

**Not an open-world simulation.** Structured teams with defined roles, protocols, and rules for who sees what — not emergent town-of-agents worlds. We picked a boundary and we'd rather fail cleanly at it than blur.

**Not a data roach motel.** JSONL on your disk, DuckDB over your own files, and long-format CSV export. The browser interface is a convenience, never the only door.

---

## Status

**Early release.** The current release is 0.0.5.1. Teams and run folders may need changes between 0.0.x versions; each such change, and everything else that changed in each version, is in the [changelog](https://github.com/teamrunner/teamrunner/blob/main/CHANGELOG.md).

**License:** [GPL-3.0-or-later](https://github.com/teamrunner/teamrunner/blob/main/LICENSE), like R. Using TeamRunner does not by itself put your teams, runs or results under the GPL. You choose terms for material you own; provider terms and third-party rights still apply, and AI output ownership or exclusivity is not guaranteed; the shipped examples, the files `teamrunner new` writes and the authoring guide use MIT-0. Pull requests are not open yet — see [CONTRIBUTING.md](https://github.com/teamrunner/teamrunner/blob/main/CONTRIBUTING.md).

---

## Publisher, license, and responsible use

**Author and copyright holder:** Kumar Muthuraman. **Publisher and maintainer:**
Serenity Partners LLC, which also operates the sponsored demo gateway.
Contact: hello@teamrunner.ai.

TeamRunner is provided **as is, without warranty**, subject to applicable law.
The engine and its documentation are GPL-3.0-or-later; the designated examples, starter files and authoring guide are MIT-0.
See [LICENSE](LICENSE), [NOTICE](NOTICE), and [LEGAL.md](LEGAL.md).

**All monetary costs are estimates. Actual charges can exceed estimates and
configured budgets.** Retries, tool calls, concurrent requests, incomplete usage
reports, pricing changes, and software or provider failures can cause overruns.
Budget checks do not cap your provider's billing. Monitor charges and set
provider-side spending controls where available. You are responsible for charges
on your accounts. To the maximum extent permitted by applicable law, Serenity
Partners LLC, Kumar Muthuraman, and other authors, copyright holders, and
contributors disclaim liability for provider charges and cost overruns, as
explained in [LEGAL.md](LEGAL.md).

Verify AI outputs and research conclusions independently. Review unfamiliar teams
and tool permissions before running them. Inputs can go to external providers;
run folders can retain sensitive information. Do not submit private or sensitive
information to the sponsored demo. Review and redact logs before sharing them.

---

## Questions, bugs and ideas

All of them are welcome, and every one is read. [Open an issue](https://github.com/teamrunner/teamrunner/issues/new/choose) on GitHub: there is a short form for a bug and one for a question or idea. No GitHub account? Write to **hello@teamrunner.ai**. Either way, please never include an API key, and remember that a run folder holds everything said in the run.

A security problem? Please don't open an issue: write to security@teamrunner.ai, as [SECURITY.md](https://github.com/teamrunner/teamrunner/blob/main/SECURITY.md) explains.

Everyone taking part in the project's spaces, issues included, is asked to follow the [code of conduct](https://github.com/teamrunner/teamrunner/blob/main/CODE_OF_CONDUCT.md).

---

## Appendix A: file reference at a glance

Inside a package, these names are reserved. **Every one of them is optional except `team.yaml`.** Each file answers one question: `team.yaml` *what is this team?* (its charter), `chair.md` *how is it run?*, and the member files *who does the work?* A folder grows one file per feature you use.

| File | Purpose | If absent |
|---|---|---|
| `team.yaml` | Marks the folder as a team; what it does; `default_model`; `message_goes_to`; last, `default_inputs` if the prompts take inputs | *Required* |
| `*.md` | A member: frontmatter + body. `kind:` is `llm` (default) or `toolkeeper`. The name is the filename, minus any numeric prefix | — |
| `*.md` with `kind: toolkeeper` | The member who holds a tool: `tools:` with the base terms, `access:` for who may use it on what terms, and the sign on the door as the body | No tools can be used |
| `chair.md` | How a protocol team is run: the script, as the file's body | Everyone may talk to everyone; members act in alphabetical filename order, one turn each |
| `tools/` | Reserved. Toolkeepers use the built-ins: `web_search_brave`, `read_page`, `read_file`, `run_code`, `read_email`, `save_draft` | Built-ins only |
| `models/` | Model profiles: a model and its settings in one `.yaml`, named by agents as `model: careful.yaml` or by `team.yaml` as `default_model:` | Agents name provider models directly |
| `runs/` | Where every run of this package is logged, whichever team you started; git-ignored | Created on the first run |

Outside the package, in a folder of its own: `experiment.md` (the conditions — each `from:` a team, with what to `change:` — or `factors:` for a full factorial; replications, seed, metrics, limits; your question as the body) with the study's `variants/`, `metrics/` (answer keys as `.yaml`, evaluators as `.md`) and `tasks/`. Changed conditions are built in `conditions/`, and the experiment's runs land in its own `runs/`.

References use bare names within a folder (`judge`) and paths across scopes (`../fact_checker`, `analytics/critic`); a model profile keeps its `.yaml`. Links to shared files outside the package are fine while you work — `validate` and `run` warn about each one loudly, and `teamrunner gather` copies them in before you hand the package over. YAML is canonical; JSON is accepted. Quote your strings — `no` is a word, not a boolean.
