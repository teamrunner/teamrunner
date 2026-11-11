# What's coming

Designed, not built yet. **Nothing on this page runs in the current preview, and none of it is in the tutorial.** The shapes below are agreed, but details may change before they ship. What works today is in the [README](README.md); the order things arrive in is under [Status and roadmap](README.md#status-and-roadmap).

- [Who sees what: the whiteboard](#who-sees-what-the-whiteboard-v03) **[v0.3]**

Experiments, designed here first, are implemented in the current source checkout but unreleased: see [part 2 of the tutorial](README.md#tutorial-part-2-your-first-experiment).

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
