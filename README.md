# TeamRunner

**The experimentation platform for agent organizations.**

> **Preview — a soft launch.** TeamRunner 0.0.x is published so friends and testers can try it before it is announced. Until the announced 0.1.0 it may change in ways that break teams and run folders made with an earlier version. Comments are very welcome: [open an issue](https://github.com/teamrunner/teamrunner/issues) or write to hello@teamrunner.ai.

Run a multi-agent system entirely from text files and folders. Change one line and run it again; **[v0.2]** run it eighty times and get a CSV of what changed.

```
debate/                      ← a package: this folder gets a debate done, anywhere you copy it
├── team.yaml                ← this folder is a team, and its default topic
├── advocate.md              ← an agent: config on top, prompt as prose
├── critic.md
├── judge.md                 ← no chair.md, so they speak in name order: advocate, critic, judge
└── runs/                    ← every run of this team, logged here (git-ignored)

experiments/advocate_model/  [v0.2]
└── experiment.yaml          ← base: ../../debate — GPT vs. Claude as advocate, 20 replications
```

> Everything unmarked works today, in the preview. Marked parts are designed but not built yet: **[v0.2]** arrives with experiments; **[v0.3]** with richer organizations (managers, private information, subteams, parallel work).

```bash
teamrunner run debate --message "Remote work makes teams more productive"
```

No Python. No graph-building API. No framework vocabulary. If you can edit a text file and run one command, you can run — and *study* — an organization of agents.

---

## Contents

- [Why we built it](#why-we-built-it)
- [The five ideas](#the-five-ideas)
- [Tutorial: your first debate in 10 minutes](#tutorial-your-first-debate-in-10-minutes)
- [Use TeamRunner in your browser](#use-teamrunner-in-your-browser)
- [What lands in a run folder](#what-lands-in-a-run-folder)
  - [What your runs keep, and where your words go](#what-your-runs-keep-and-where-your-words-go)
- [Going further: the worked examples](#going-further-the-worked-examples)
- [What TeamRunner is not](#what-teamrunner-is-not)
- [Status and roadmap](#status-and-roadmap)
- [Questions, bugs and ideas](#questions-bugs-and-ideas)
- [What's coming](PREVIEW.md): designed, not built yet

---

## Why we built it

### 1. The people who study organizations are not the people who write agent code

Management scientists, operations researchers, and behavioural economists have spent a century studying how the *structure* of a team changes its output: hierarchy vs. flat, who reports to whom, who sees which numbers, what happens when one member has a private incentive.

Suddenly there is a laboratory where you can actually run those experiments — teams of LLM agents, cheap, repeatable, fully observable. And the entry price is: learn Python, learn an agent framework, and write a few hundred lines of orchestration code before you can ask your first question.

That is the wrong tax on the wrong people. In TeamRunner an agent is a Markdown file, a team is a folder, and a hierarchy is a folder inside a folder. The org chart is the directory tree. You edit prose, not code.

### 2. Changing the structure should be a one-line change, not a refactor

In code-first frameworks, the organizational structure is *dissolved into the program*. "What if the fact-checker were a neutral referee instead of each side's own editor?" is a question about org design; answering it means rewriting your graph.

In TeamRunner the structure lives in files, and experiments are **overlays that patch those files**. The founding rule:

> **If it's in a file, it's a factor.**

Models, temperatures, prompts, who sees what, the flow itself, even which sub-team does the work — all of them are legal things to vary in a designed experiment, because all of them are just values in text files.

### 3. Almost nothing published about multi-agent systems is reproducible

Most multi-agent results are `n = 1` demos: one run, one prompt, one screenshot. LLMs are stochastic; a single run is an anecdote. And even a careful author can rarely hand you the exact artifact that produced their result.

TeamRunner treats reproducibility as a feature, not a chore, with a deliberately honest standard:

**Deterministic where the system controls randomness, statistically rigorous where the model does, fully auditable everywhere.**

- **Deterministic harness.** **[v0.2]** Every source of randomness *we* own — condition assignment, turn order, sampling of tasks — is seeded from one master seed. Same seed, same experimental design.
- **Statistical, not bitwise, replication.** **[v0.2]** We do not pretend API models can be replayed from a seed (they can't: some providers offer best-effort seeds, some none, and `temperature=0` is not determinism). So the unit of a result is a *distribution over replications*, with confidence intervals and effect sizes. `replications: 1` is deliberately awkward to write.
- **Total artifact capture.** Already in 0.1: every member exactly as it ran (model, settings, full prompt), everything said, the model that actually answered, token counts, timings, costs, and the Python, LiteLLM and Deno versions go into the run folder, so you can compare two runs line by line. **[v0.2]** Every raw request and response, and a fully expanded copy of the spec.

A team folder is a shareable, forkable, git-diffable package. "Clone this repo and reproduce this study" is the whole point, and it is what 0.2 completes.

### 4. Orchestration is crowded. Experimentation is empty.

There are many good ways to *build* a multi-agent application. There is almost no way to *run a designed experiment on one*. TeamRunner is aimed squarely at the second: factorial designs, seeded replications, an evaluation layer, a canonical event log, and a long-format CSV at the end that goes straight into R, Stata, or pandas. The event log is in 0.1, under every run; the rest arrives with **[v0.2]**.

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

> {{motion}}

Decide which side argued better — argument quality and logical clarity,
not your own opinion of the motion.
```

**2. A folder is a team if it contains `team.yaml`.** Everything inside is a member, and a member is one of three things: a **worker** (an agent with a goal), a **team** (**[v0.3]** a subfolder with its own `team.yaml` — just another node in its parent's flow: *its turn means running its own internal flow to completion*, like a subcommittee: it deliberates on its own whiteboard, and the parent hears only its report — `report_from:` in its `team.yaml` says whose words those are), or a **toolkeeper** (the member who holds the keys to a tool). Complexity is contained by hierarchy, not by syntax. There is no registry file listing your agents; the file browser is the org chart.

The whole folder — team and toolkeepers — is a **package**: copy it anywhere and it gets the same job done. The work itself comes from outside: you hand the team a topic when you run it, or it uses the default written in `team.yaml`.

**3. Every team has a chair, and there are no hidden if-statements.** Someone always decides who acts next, and TeamRunner makes you say who:

- a **chair** (`chair.md`), which follows the script you wrote, or (**[v0.3]**) a decision table — deterministic, zero model calls;
- a **manager** (`manager.md`, **[v0.3]**) — an LLM whose prompt says "you are the moderator, decide who speaks next";
- or neither, and members simply take turns in alphabetical order, everyone hearing everyone.

Every routing decision is logged as an event attributed to whoever made it. Which means *protocol vs. discretion* — a script vs. a manager exercising judgment — is itself something you can A/B test by swapping one file for the other (**[v0.3]**).

**4. Tools sit with a toolkeeper.** Tools are not members. An organization doesn't hand every worker every tool; a factory has a tool crib, with someone at the window who checks who may take what, and everyone else asks there. So the only members who touch tools are toolkeepers — a toolkeeper holds the guardrails (timeouts, no network, which folders), decides by a plain access list rather than a prompt, and its text body is simply what the team is told about it. Who may use a toolkeeper, and on what terms, is up to the toolkeeper's own access list — one toolkeeper can give different members different terms, the way a library gives professors and students different cards.

**5. Every run is kept, and no agent can touch the record.** Every run gets its own folder in `runs/` with every prompt, everything said, every decision about who acts next, every tool call, the model that answered, tokens, time and cost (see [What lands in a run folder](#what-lands-in-a-run-folder)). The run log is the researcher's camera, outside the simulation: **no agent can ever read or write it** — a hard guarantee, without which every study of who knows what leaks.

Two more ideas are designed but not built yet — a whiteboard for who sees what, and experiments as overlays — and are described in [PREVIEW.md](PREVIEW.md).

---

## Tutorial: your first debate in 10 minutes

We'll build a three-agent debate, run it, give it rounds, a fact checker and the web. Zero code throughout. Turning it into a 40-run experiment comes with 0.2: see [PREVIEW.md](PREVIEW.md#experiments-v02).

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

A team on another provider takes that provider's key instead (`ANTHROPIC_API_KEY`, `GEMINI_API_KEY`, …). The shipped examples run on OpenRouter, so they need only `OPENROUTER_API_KEY`. If a key is missing, the run stops before spending anything and says which one. You can try every step without a key: `teamrunner run --rehearse` runs the whole team with no model, for free.

Keys stay with you, never with your teams: team folders get copied, zipped and sent to people, and a key inside one goes with it. If you already have a key set in your shell, TeamRunner offers to save it to the key file the first time it needs it (or to use it just this once). If it finds a `.env` inside a team folder, it offers to move the key out and delete it there. And it will **refuse to run** if it finds something that looks like an API key inside a spec file.

Models on your own machine work too, free: install [Ollama](https://ollama.com), pull a model (`ollama pull gpt-oss:20b`), and write `default_model: ollama/gpt-oss:20b` in `team.yaml`. If Ollama runs somewhere other than this computer, put its address in the key file as `OLLAMA_API_BASE`. Choose one of about 20 billion parameters or more: the smallest models often fail at exactly what teams need most — answering in a fixed form and asking toolkeepers properly — so they test the wiring better than they stand in for a real team. Before the first turn of every run, TeamRunner checks that each model your team names is available and lists every problem it finds in one table, so you never pay for half a run. `teamrunner validate --online` runs the same check without a run.

Or open `teamrunner ui` and choose **Set up keys** after selecting a team. It saves keys to the same key file and shows whether each key is present, never its saved value. **Check keys and models online** is a separate check.

### Step 1 — make a team

```bash
teamrunner new debate
cd debate
```

`new` only makes files — no model is called. It creates the `debate/` folder, a starter `team.yaml`, and a `.gitignore` that keeps run logs out of git.

Two other ways to start, when you'd rather not begin from nothing:

- `teamrunner new my_debate --from fact_checked` copies one of the examples that ship with TeamRunner (or any team folder you name), with fresh ids; each file remembers what it was copied from.
- `teamrunner new my_debate --describe "two debaters argue a topic, a fact checker checks each speech, and a judge decides"` asks a model to write the team for you. It checks its own work with `validate`, never overwrites files, tells you what it cost, and ends by printing `teamrunner explain`, so you can read what it made before you run it. (If you work with an AI coding assistant, `teamrunner skill` gives it the same instructions: it installs a skill for Claude Code and Codex, and `teamrunner skill --agents-md <folder>` writes an `AGENTS.md` for other assistants that read one.)

Or run `teamrunner ui` in the folder where you want to keep your teams. Choose **New team**, enter `debate`, and select an empty team or a shipped example. Select `team.yaml` on the left to edit it.

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

In the browser, select the team, choose **New member** for each Markdown file below, and paste its contents into the editor. **Save** (Ctrl/Cmd+S), then use **Show as it will run** to read the instructions with inputs filled in.

```markdown
<!-- advocate.md -->
---
description: Argues in favor of the topic
---
You are debating in favor of this topic:

> {{topic}}

Each round, make your strongest remaining argument in one short
paragraph. From round two onward, also rebut your opponent's most
recent point before adding your own. Be persuasive but honest.
```

```markdown
<!-- critic.md -->
---
description: Argues against the topic
---
You are debating against this topic:

> {{topic}}

Each round, rebut your opponent's most recent point, then make your
strongest remaining argument against the topic in one short paragraph.
Be persuasive but honest.
```

```markdown
<!-- judge.md -->
---
description: Decides who argued better
output:
  winner: [advocate, critic]
  reasoning: ""
---
You have watched a debate on:

> {{topic}}

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

`validate` is the compiler for people who don't write code, and we treat its error messages as a headline feature: a typo in a flow arrow, a decision option with no arrow, an agent with no model, a worker holding a tool instead of asking a toolkeeper, an input with neither a value nor a default — all of it is caught before a single token is spent.

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

In the browser, choose **Runs** to reopen the conversation, output, path or record. Select two runs with the checkboxes and choose **Compare selected runs** to see member versions, outputs, cost and paths side by side.

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
- `name = …` — a **named block**: any step name can be defined below, as `rounds` is here. Its further lines are indented under it.
- `#` starts a **note** for the reader. If the chair ever needs settings, they go in a frontmatter block at the top, like any member file.
- **[v0.3]** `[a, b]` — **at the same time**: `editor -> [reviewer_1, reviewer_2] -> editor` sends the paper to both reviewers at once, and the editor waits for both reports. Neither sees the other's work in progress.
- `done` ends the flow — or, inside a named block, just that block; `stop` ends the whole run immediately.

Now the debate runs `advocate → critic → advocate → critic → advocate → critic → judge`. To see it as a picture, `teamrunner explain --diagram` opens the flow as a flowchart in your browser. Or, in the local interface, refresh the file tree after adding `chair.md`, select it, and choose **Flow diagram**. Save an edit to redraw the diagram and plain-words summary.

### Step 6 — add a fact checker and a decision

Suppose we want each speech fact-checked before it stands. Write a fact checker with a declared `output:`:

```markdown
<!-- fact_checker.md -->
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

`--pass,*3--> done` reads "on pass, or the third time, go to done": the comma means "or", and `*3` means "the third time". Here `done` ends only `advocate_turn` (or `critic_turn`), not the debate: that speaker's turn is over and the debate moves on to the next speaker. A speech that fails three times still stands — but everything said is heard by everyone, so the judge has heard the fact checker fail it, and the judge's prompt says to weigh that. Want a different ending? `fact_checker --*3--> stop` ends the whole run instead (`examples/deb8flow` does this); a forfeit that hands the debate to the other side needs a whiteboard item, which comes in 0.3.

An agent whose declared options have labelled outgoing arrows is a **decider**, and its labelled arrows are a **decision**. That's a role, not a different file format — any agent can be one. `done` inside a named block ends that block, so a passed speech hands over to the next speaker.

Two rules make this safe:

**Branch labels are checked against declared options.** `--puss-->` is a validation error, not a 2 a.m. surprise. Every option needs exactly one arrow.

**Loops are legal only through a decider, and only with a limit.** An arrow back to an earlier step needs a counted way out: some arrow from the same decider must carry `*N`. On the Nth time the decider's choice still counts if it leaves the loop (a pass on the third try is a pass); only a choice to go round again is overridden. When running out should go somewhere else, give the count its own arrow: `fact_checker --*3--> stop`. There is no `goto`, no labels-and-jumps: a flow reads top to bottom and always ends.

And note the split that makes components reusable: the fact checker's **`.md` owns how to decide**; the **flow owns what each decision means here**. The same fact checker checks `advocate` and `critic`, because neither is written into its file.

### Step 7 — give the team the web, through a toolkeeper

The fact checker's prompt says "ask the web toolkeeper", so the team needs one. Its searches go through Brave Search, so add a Brave Search API key to the key file as `BRAVE_API_KEY`. Searches cost half a cent each at Brave's list price, and a run's cost counts them: the totals show models and tools separately. Agents never hold tools themselves. A tool is reached through a **toolkeeper**: a member with no model and no prompt, which holds the tool, checks who is asking, and enforces the limits — like the attendant at a factory's tool crib, who checks who may take which tool and how many, and logs it.

```markdown
<!-- web.md -->
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

TeamRunner 0.1 has six built-in tools: `web_search_brave` (Brave Search), `read_page` (a link in, the page's text out), `read_file` (a file the run was given with `--file`, its text out), `run_code` (Python in a sandbox: no internet unless the toolkeeper allows it, and files written only in the run's own work folder), `read_email` (one email from a folder of `.eml` files in the package) and `save_draft` (a reply saved in the run's `work/outbox/`, never sent, addressed only to the email's sender unless `reply_only: false`). The sandbox is Python running in WebAssembly, and it installs with TeamRunner — nothing else to set up; it can use packages built for it (numpy, pandas, scipy, sympy and many more), but not every package. `examples/fact_checked` gives the fact checker a second toolkeeper, `sandbox.md`, so it can check the sums in a claim by running them. A toolkeeper holds one tool in 0.1, so its limits always mean one thing; a member who needs two tools asks two toolkeepers, as the fact checker does here. `examples/reference_check` uses `read_page` and `read_file`: it checks every reference in a paper.

---

## Use TeamRunner in your browser

From 0.0.4, open a team folder with:

```bash
teamrunner ui debate
```

`teamrunner ui` opens the current folder when you leave out `debate`; `teamrunner gui` is an alias. To create teams, open their parent folder instead. The command opens a browser tab and prints its local address. Keep that terminal running while you work; press Ctrl+C there to stop the server. If the tab does not open, paste the full printed address, including its token, into your browser.

The page works on the same files as the CLI. Choose a team or file on the left. You can create an empty team or copy a shipped example, add members, edit and save, rename members, or move files to the system trash. The editor warns about unsaved edits and files changed by another editor. Validation points to the file, line and suggested fix. **Show as it will run** fills in a member's instructions; **Flow diagram** shows the team's flow and summary. For a ready-made flow to edit, start from the `fact_checked` example.

**Run…** accepts team defaults, a message, a file or a YAML task file. Rehearsal is selected by default for a team with no prior run; it uses placeholder words and random answers, needs no key and costs nothing. A model run uses your configured provider and its normal charges. The interface validates before starting, allows one active run at a time, streams the recorded events, and can stop the run cleanly. Key setup uses `~/.teamrunner/.env`; stored key values are never displayed.

**Runs** is the archive. Filter by team or status, search the message and output, then open a run:

- **Conversation:** member messages, decisions, tool calls, retries, tokens, models and cost, with a toggle for what each member could read.
- **Path:** the saved flow with taken arrows highlighted and transition counts. Older logs without a saved engine path show only observed completed-turn transitions, labelled accordingly.
- **Output:** the rendered `output.md`.
- **Record:** inputs, numbered member versions, recorded settings and files. Version numbers come from the available run history.

Select two runs to compare their outputs, cost, time, paths and member versions. Exact member source diffs are available for new logs that record the source and its fingerprint; older logs show their recorded prompts, which may include substituted inputs.

**Re-run with these inputs…** uses the current team with the old run's input values and copied input files. It does not restore old member versions. **Edit a copy of the saved team…** appears only when a run contains `resolved/team.yaml`; it creates an independent team with fresh IDs and lineage. Open the teams' parent folder to give that copy a place outside the original team. Ordinary runs do not yet save a complete resolved team, so their original files cannot be reconstructed from prompts alone.

Every run has **Open folder** and raw-file download links. The editor stays inside the folder you opened, and run records remain read-only. The server listens only on this computer at `127.0.0.1`, with a random access token. Editor, Markdown and diagram assets are bundled for offline use; model calls and online checks still need their usual services. The page adds no database or separate project format, and shows equivalent CLI commands alongside actions.

## What lands in a run folder

Every run is logged in a `runs/` folder at the top of the package you ran — even when you run just one subteam (**[v0.3]**). The folder is git-ignored, so sharing the package never shares its history.

Today a run folder holds three things: `run.json` (when, which team, the message you sent, a fingerprint of the package, cost), `events.jsonl` (below) and `output.md`, in a folder named for when and what you ran (`runs/2026-10-12_143201_debate/`) — plus `inputs/`, a copy of any file the run was given, and `work/`, where `run_code` keeps what it writes. **[v0.2]** The fuller record that follows arrives with experiments, which keep their runs in the experiment's own folder:

```
runs/01JZXG7T3M9QKWP5RVB2C8HD6N/
├── manifest.json     ← the receipt
├── events.jsonl      ← the camera
├── raw/              ← every request and response payload, by hash
└── resolved/         ← the spec, with every default and patch expanded
```

**`manifest.json`** records the condition and its patches, the master and derived seeds, timestamps, resolved model versions (what `openai/gpt-6-luna` *actually was* that day), your Python and package versions, cost and token totals, and a component inventory linking every instance path to its permanent ID and content hash.

**`events.jsonl`** is one JSON object per line, append-only, in TeamRunner's own schema:

| Event | What it records |
|---|---|
| `run_started` | the inputs, and every member exactly as it will run — model, settings, prompt; from 0.0.4, also each member's source text, content fingerprints and the declared flow graph |
| `agent_message` | from, **to** (the *realized* topology), stage, attempt, **what it could read**, everything it said, typed outputs, the model actually used, tokens, cost, time |
| `route_decision` | who decided, which option, which arrow — the audit trail for "no hidden if-statements" |
| `tool_call` | who asked, which toolkeeper, the toolkeeper's yes or no, tool, arguments, the full result (for a search, how many results came back, unless `keep_results: true`) |
| `state_write` **[v0.3]** | item, old → new, writer |
| `check`, `retry`, `warning`, `error`, `run_finished` | the pre-run check, retries, warnings, failures, and the totals; from 0.0.4, `run_finished` also saves the path taken, for arrow counts |

A field never changes its meaning between versions — later versions only add — so an analysis script you write today keeps working.

The `to` field is worth a pause. **[v0.3]** The whiteboard's `read_by` lines for what is said declare the *permitted* topology; the log captures the *realized* one. **The gap between them is itself a research finding** — you gave the team an org chart, and now you can measure the one it actually used.

Query it with DuckDB directly over the files today; **[v0.2]** export to CSV or Parquet when you want to leave.

**`resolved/`** **[v0.2]** is the quiet favourite. It is a mirror of your spec tree with every default and every patch expanded to its explicit value — full frontmatter, explicit flow, explicit permissions. `diff -r debate/ runs/<id>/resolved/` shows you exactly what the defaults filled in. And:

```bash
teamrunner run runs/01JZXG7T3M9QKWP5RVB2C8HD6N/resolved
```

replays that exact spec. Every run folder is a self-contained replication package. Defaults are for authors; records get the fully-resolved truth.

### What your runs keep, and where your words go

**A run folder keeps everything, in plain text, on your machine**: every prompt, everything every member said, every page a toolkeeper read, a copy of any file you gave the run, and whatever `run_code` wrote. Open any of it in a text editor. That is what makes a run checkable, and it also means a run folder holds whatever you put into the run, so share one only when you would share all of it.

**Your keys are never in it.** `run.json` records only where each key came from (`"OPENAI_API_KEY": "key_file"`), never the key. Keys live in `~/.teamrunner/.env`, which only you can read, outside every team.

**Sharing a team through git does not share its runs**: `teamrunner new` puts `runs/` in the team's `.gitignore`. If you zip or copy the folder yourself, leave `runs/` out.

**What you send a hosted model goes to its provider** — OpenAI, Anthropic, Google — under that provider's terms, as with any app that uses them. A local model (through Ollama) keeps everything on your machine. A `web_search_brave` sends its query to Brave; `read_page` fetches the page from its site. Search results are the one thing a run does not keep: the member reads them, the log keeps the query and how many came back, because Brave's terms allow storing results only on plans with storage rights. If yours has them, `web_search_brave: {keep_results: true}` keeps them. Brave's terms also forbid using its results to evaluate or benchmark AI models; check them before a study.

**TeamRunner itself sends nothing anywhere.** No telemetry, no usage statistics. The only other things it fetches are Pyodide, the first time `run_code` is used, and the packages your code imports.

---

## Going further: the worked examples

Six examples ship with TeamRunner, in `examples/`, simplest first — each adds something the one before it doesn't have. Copy one by name to start from it: `teamrunner new my_team --from orchestrators_lite`. Most are published multi-agent papers or projects, recast as TeamRunner files. All six use `openrouter/google/gemma-4-31b-it` and need only an OpenRouter API key, with credits for model usage. They do not require OpenAI, Anthropic, Brave or catalogue keys.

Set the key outside the team folder (never commit it):

```bash
export OPENROUTER_API_KEY="your-openrouter-key"
teamrunner new my_debate --from round_robin
teamrunner run my_debate
```

On PowerShell, set `$env:OPENROUTER_API_KEY="your-openrouter-key"` instead. You can also keep `OPENROUTER_API_KEY` in `~/.teamrunner/.env`. Add `--rehearse` to check a flow without calling a model; a rehearsal does not verify live model or tool behavior. The two source-checking examples read public pages and Crossref without extra keys; access restrictions, rate limits and missing abstracts or PDFs can still limit their evidence. The fact checker's Python sandbox downloads its runtime on first use. The code-review example uses demo model settings rather than the source paper's model.

| Example | Recast of | What it adds |
|---|---|---|
| **`round_robin/`** | — | The simplest team: three members, no chair file — one turn each, in filename order, and a typed verdict. |
| **`multi_agent_debate/`** | muthuspark/multi-agent-debate | A chair: the debate as a script, with a repeated block of rounds. |
| **`hiring_committee/`** | Stasser & Titus 1985 (hidden profile) | A committee picks a candidate when the key facts are spread across its members: facts only one member knows, kept in that member's own file; a stage in the chair; a clerk who records the decision; and both meeting formats in one `chair.md`, one commented out. |
| **`orchestrators_lite/`** | Fukui 2026 (lite) | Five engineers review code against planted bugs: a model profile the whole team shares, and two inputs. |
| **`fact_checked/`** | — | The debate with a fact checker: decisions that send a speech back, counted tries, and toolkeepers — a public-source reader with different terms for different members, a required source-reading step, and a Python sandbox. |
| **`reference_check/`** | sciwrite-lint (Samsonau 2026) | Checks every reference in a paper: that it exists, that its details are right, and that the cited paper says what the citing sentence claims. A paper given as a link or a file (`--file`), toolkeepers holding `read_page` and `read_file`, the same tool under two toolkeepers with different `domains`, a decision that can stop the run, and batches of ten as a loop with a counted exit. |

**Coming with 0.3**, recast on paper and waiting for the features they need: **Deb8flow** (subteams — each side a debater plus its own fact checker), **NegotiationGym** (private valuations, a loop either side can end), **LLM-Deliberation** (four parties, confidential briefs, one secretly given a hidden agenda), **AgentReview** (three reviewers at once, sealed reviews — do reviewers herd when reviews are public?), **ChatDev** (a three-level company, each department on its own whiteboard), and the full **Invisible Orchestrators** study (a hidden orchestrator whose words reach the team only as unsigned "policy updates").

The recasting campaign is the point, not decoration: each one is run *before* the corresponding code exists, so the spec breaks on paper where breaking is cheap. Two of the open questions in our spec today exist because `agent_review` and `chat_dev` refused to be expressed cleanly.

---

## What TeamRunner is not

**Not another agent framework — yet.** TeamRunner is for asking *questions about* teams of agents and, with **[v0.2]**'s replications, getting statistically defensible answers. Its constraints (declared outputs, bounded loops, no free-text routing) are what make the answers trustworthy, and they are also what an organization wants from a team it runs for real: every decision logged, every loop bounded. A package that won its experiment is meant to be shipped — **[v0.2]** `export` hands you a standalone copy — and running packages in production, logged and improved over time, is where we intend to go. If you need a general-purpose agent framework today, use one.

**Not a programming language.** Conditions are exactly `<field> is <value>` and `is not`. There is no arithmetic, no boolean algebra, no expressions. Every time this has been tempting, the answer has been the same: the judgment belongs in an agent's prompt, and the wiring belongs in the flow. That line is load-bearing, and we intend to hold it.

**Not an open-world simulation.** Structured teams with defined roles, protocols, and rules for who sees what — not emergent town-of-agents worlds. We picked a boundary and we'd rather fail cleanly at it than blur.

**Not a data roach motel.** JSONL on your disk and DuckDB over your own files today; **[v0.2]** Parquet and long-format CSV export. The dashboard, when it comes, is a convenience, never the only door.

---

## Status and roadmap

**Preview.** What the roadmap calls 0.1 is out as a soft launch, numbered 0.0.x: every command in the tutorial works; what is designed but not built yet is in [PREVIEW.md](PREVIEW.md). It may still change in ways that break earlier teams and runs until 0.1.0 is announced. What changed in each version is in the [changelog](https://github.com/teamrunner/teamrunner/blob/main/CHANGELOG.md).

| Version | |
|---|---|
| **Design** | Spec drafted; paper recasts written as pure text ✅ |
| **0.1 · The org package** ← now: in preview as 0.0.x | A folder that gets a job done: validation with friendly errors, its own runner, the event log, toolkeepers and four built-in tools, rehearsals, `run`, `runs`, `explain`, `new`, the authoring skill ✅ |
| **Before 0.2 · Local browser interface** | In 0.0.4 ✅ `teamrunner ui` / `gui`: edit, validate, rehearse or run, browse recorded conversations and paths, compare two runs |
| **0.2 · Experiments** | Experiments outside the package (`base:`), factorial designs, seeded replications, manifests, response cache, the answer-key metric, `teamrunner experiment`, export; `promote` / `export`. First real result: leader vs. flat |
| **0.3 · Richer organizations** | Managers and rules-table chairs, `whiteboard.yaml` (private information, sealed and conditional visibility, addressed messages), subteams, parallel work, announcers; the recasts run for real, and the Invisible Orchestrators replication |
| **0.4+ · Later** | LLM-as-judge metrics, communication-network statistics and visualization, template library of canonical org structures, human-in-the-loop, automated improvement, running packages in production |

Human-in-the-loop nodes (`kind: human` — the runner pauses and asks a person) are for mixed human–agent studies.

**License:** [GPL-3.0-or-later](https://github.com/teamrunner/teamrunner/blob/main/LICENSE), like R. Your teams, runs and results are yours, under any licence you choose; the shipped examples, the files `teamrunner new` writes and the authoring guide are MIT-0. Pull requests are not open yet — see [CONTRIBUTING.md](https://github.com/teamrunner/teamrunner/blob/main/CONTRIBUTING.md).

---

## Questions, bugs and ideas

All of them are welcome, and every one is read. [Open an issue](https://github.com/teamrunner/teamrunner/issues/new/choose) on GitHub: there is a short form for a bug and one for a question or idea. No GitHub account? Write to **hello@teamrunner.ai**. Either way, please never include an API key, and remember that a run folder holds everything said in the run.

A security problem? Please don't open an issue: write to security@teamrunner.ai, as [SECURITY.md](https://github.com/teamrunner/teamrunner/blob/main/SECURITY.md) explains.

---

## Appendix A: file reference at a glance

Inside a package, these names are reserved. **Every one of them is optional except `team.yaml`.** Each file answers one question: `team.yaml` *what is this team?* (its charter), `chair.md` or `manager.md` *how is it run?*, `whiteboard.yaml` *who sees what?*, and the member files *who does the work?* A folder grows one file per feature you use.

| File | Purpose | If absent |
|---|---|---|
| `team.yaml` | Marks the folder as a team; what it does; `default_model`; `message_goes_to`; for a subteam, `report_from` **[v0.3]**; by-reference members (`uses:`, **[v0.3]**); last, `default_inputs` if the prompts take inputs | *Required* |
| `*.md` | A member: frontmatter + body. `kind:` is `llm` (default) or `toolkeeper`; `rules` and `announcer` are **[v0.3]**, `human` later. The name is the filename, minus any numeric prefix | — |
| `*.md` with `kind: toolkeeper` | The member who holds a tool: `tools:` with the base terms, `access:` for who may use it on what terms, and the sign on the door as the body | No tools can be used |
| `chair.md` | How a protocol team is run: the script, as the file's body, or a `kind: rules` table **[v0.3]** | Everyone may talk to everyone; members act in alphabetical filename order, one turn each |
| `manager.md` **[v0.3]** | How a managed team is run: an LLM that decides who acts next; only it may use input placeholders. A team has at most one of `chair.md` and `manager.md` | — |
| `whiteboard.yaml` **[v0.3]** | Who sees what: declared items with `read_by` / `written_by`, and who can read what each member says (`said`) | Everyone reads everything said; built-in items only (e.g. `stage`) |
| `tools/` | Reserved for a package's own tools, which come after 0.1. In 0.1 toolkeepers use the built-ins: `web_search_brave`, `read_page`, `read_file`, `run_code`, `read_email`, `save_draft` | Built-ins only |
| `models/` | Model profiles: a model and its settings in one `.yaml`, named by agents as `model: careful.yaml` or by `team.yaml` as `default_model:` | Agents name provider models directly |
| `runs/` | Where every run of this package is logged, whichever team you started; git-ignored | Created on the first run |
| *subfolder with `team.yaml`* **[v0.3]** | A subteam — a node in the parent flow, with its own whiteboard; the parent hears only its report (`report_from:`) | — |

**[v0.2]** Outside the package, in a folder of its own: `experiment.yaml` (names the package with `base:`; factors as patches, replications, seed, limits) with the study's `tasks/`, `metrics/` (format still open), and `variants/`. An experiment's runs land in the experiment's own folder.

References use bare names within a folder (`judge`) and paths across scopes (`../fact_checker`, `analytics/critic`); a model profile keeps its `.yaml`. Links to shared files outside the package are fine while you work — `validate` and `run` warn about each one loudly, and `teamrunner gather` copies them in before you hand the package over. YAML is canonical; JSON is accepted. Quote your strings — `no` is a word, not a boolean.
