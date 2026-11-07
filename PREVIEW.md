# What's coming

Designed, not built yet. **Nothing on this page runs in the current preview, and none of it is in the tutorial.** The shapes below are agreed, but details may change before they ship. What works today is in the [README](README.md); the order things arrive in is under [Status and roadmap](README.md#status-and-roadmap).

- [Who sees what: the whiteboard](#who-sees-what-the-whiteboard-v03) **[v0.3]**
- [Experiments](#experiments-v02) **[v0.2]**

---

## Who sees what: the whiteboard **[v0.3]**

Everything an agent knows arrives through one of:

| Layer | Org analogue | Who reads | Who writes |
|---|---|---|---|
| **The whiteboard** (`whiteboard.yaml`) | the meeting, and the official record | everyone, unless a `read_by` line says otherwise | per item `written_by`; whatever anyone says is posted automatically; always logged |
| **Memory** | what you were in the room for | you | nobody — history is append-only |

Everything said is posted on the whiteboard, open to all by default, so a team with no whiteboard file is a meeting room — and that is all of v0.1. **[v0.3]** `whiteboard.yaml` adds the rest: seal the reviewers from each other, or make the team a relay where each person hears only who spoke before them, with a `read_by` line for what was said — one line each.

Information asymmetry is not a feature we had to invent; it is a narrow `read_by`. Incentives are numeric items on the whiteboard. Private briefs are items only one party can read.

### An example

The debate doesn't need this, but the moment you study organizations you will. `whiteboard.yaml` is the team's whiteboard: declared, typed items, each with who may read and write it. Everything anyone says is posted there too, open to all unless a `read_by` line says otherwise.

```yaml
# whiteboard.yaml

# Bare list = a choice, starting at the first option.
# Read by everyone, written by the chair — those are the defaults.
stage: [opening, rebuttal, counter, closing, done]

# A bare value = a starting value, read and written by the defaults.
rounds_left: 3

# Spell it out only when you change who reads or writes it.
score:
  starts_as: 0
  written_by: [judge]

# Information asymmetry IS a read permission.
buyer_ceiling:
  starts_as: 850
  read_by: [buyer]
```

Every field is declared up front — no ad-hoc keys appearing mid-run. Every write is harness-mediated and logged as a `state_write` event: an agent listed in `written_by` returns the new value as a declared output field; nothing pokes state directly. Agents reference readable fields in their prompts as `{{whiteboard.buyer_ceiling}}`, and the validator checks that permission before you spend a cent.

This one file is doing a surprising amount of work. Private valuations, confidential negotiating briefs, payoffs and incentives, task inputs, an artifact handed from one department to the next — all of them are just whiteboard items with the right permissions.

---

## Experiments **[v0.2]**

Your package stays clean and runnable. An `experiment.yaml` lives *outside* it, names it with `base:`, and declares factors as *patches* against it, plus replications and a seed. Single runs and 200-run factorials go through the same engine. When a variant wins, `teamrunner promote` copies the exact version that ran back into the package — same agent, new version — and `export` hands you a standalone copy for production.

### Your first experiment

Here's the question that motivated the whole project: **does it matter which model plays the advocate?**

Don't touch the debate. Make a folder next to it with one file:

```yaml
# experiments/advocate_model/experiment.yaml
name: advocate-model-ab
base: ../../debate
task: tasks/remote_work

factors:
  advocate_model:
    - label: gpt
      patch: {advocate.model: "openai/gpt-6-luna"}
    - label: claude
      patch: {advocate.model: "anthropic/claude-sonnet-4-5"}

replications: 20        # 2 conditions × 20 = 40 runs
seed: 42

limits:
  max_concurrent: 4
  max_cost_usd: 40.00
```

```bash
teamrunner experiment .
```

What just became possible:

**Factors are patches, so the base spec stays clean.** `advocate.model` means "the `model` field of the component `advocate`". Patch paths address any field of any component, including the prompt body (the field is called `prompt`), and any value can be `file:<path>` to swap in a whole file. `patch: {}` is the *unmodified base spec* — the control condition, written honestly as a level rather than smuggled in as "the normal run."

**Anything in a file is a factor.** That's not a slogan, it's the mechanism. A few real examples from our worked recasts:

```yaml
# A prompt/strategy A/B — same agent, two personalities
- {label: tough, patch: {buyer.prompt: file:variants/buyer_tough.md}}

# A treatment: one member is secretly self-interested
- {label: saboteur, patch: {council.prompt: file:variants/saboteur.md}}

# The communication structure ITSELF as the factor:
# same reviewers, same paper — only who can read whose review changes
- label: public
  patch:
    whiteboard.said.reviewer_1.read_by: everyone
    whiteboard.said.reviewer_2.read_by: everyone
    whiteboard.said.reviewer_3.read_by: everyone
```

That last one is the founding claim of the product. Studying whether peer reviewers herd when they can see each other's reviews is, in TeamRunner, a two-level factor on who may read what each reviewer says.

**`replications` has a minimum of 2.** Writing `replications: 1` requires an explicit `allow_single_run: true`. LLMs are stochastic; single runs are anecdotes; we made the anecdote the awkward thing to write.

**One master `seed` fixes everything the harness controls** — condition assignment, ordering, per-run seed derivation. It does not, and cannot, make the model deterministic. See [Why we built it](README.md#3-almost-nothing-published-about-multi-agent-systems-is-reproducible).

**`limits` is a real budget ceiling.** `max_cost_usd` stops the experiment; `max_concurrent` keeps you inside your rate limits.

Then get the data out:

```bash
teamrunner runs                     # browse what you've run
teamrunner export --experiment advocate-model-ab --format csv
```

Long format, one row per observation, straight into R, Stata, or pandas. Your analysis never lives inside our dashboard — that's a promise, not an oversight.

The declarative metrics layer (ground truth, LLM-as-judge with a separately pinned judge model, cost and time) is specified but its file format is still open. Today's `metrics:` references in the examples are placeholders.
