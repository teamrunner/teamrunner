# Changelog

TeamRunner is in preview: 0.0.x versions are a soft launch, and until 0.1.0
is announced a version may change in ways that break teams and run folders
made with an earlier one. Each such change is listed here.

## 0.0.4

- **Local browser interface:** `teamrunner ui [folder]` (`gui` is an alias)
  opens the team's files in a browser. Edit with offline syntax highlighting,
  save, validate with clickable problems, inspect resolved instructions and
  flow diagrams, create teams from examples, manage members and set up keys.
- Start one run at a time with defaults, a message, a file or a task file;
  rehearse for free, follow events live, stop cleanly and re-run saved inputs
  with the current team. Browse and search run history, read conversations,
  outputs and records, inspect what members could read, and download raw files.
- Compare two runs' numbered member versions, outputs, cost and paths. New
  event logs keep exact member source text and fingerprints, the declared
  flow graph and the recorded engine path for source diffs and arrow counts.
  Older logs remain readable and label missing historical data explicitly.
  A saved `resolved/team.yaml`, when present, can be copied to a new team for
  editing; ordinary runs do not yet save a complete resolved team.
- The interface uses the CLI's Python functions, adds no Python runtime
  dependencies or database, and bundles its editor, Markdown renderer and
  Mermaid diagrams for offline use. It binds only to `127.0.0.1`, requires a
  random access token, confines editing to the opened folder, keeps run
  records read-only and never displays stored key values.
- The README's tutorial gives a browser alternative beside each CLI step.
- UI review fixes: teams under an unrelated folder named `runs` remain editable;
  actual run records stay read-only. Case-only renames work on macOS, rename
  selects the old filename, and new-member names are checked before creation.
  Non-UTF-8 files and validation in an empty workspace show friendly messages.
- Files changed on disk reload automatically when there are no unsaved edits.
  The file tree refreshes automatically and after runs finish. macOS uses its
  native Trash operation, preserving Finder's Put Back support. The runs list
  keeps comparison controls aligned and offers column navigation on narrow screens.
- Live transcripts append new cards and populate read context on demand;
  member-version history is cached in memory and invalidated when run files change.
  The historical diagram helpers have been reformatted for readability.
- **Two email tools**: `read_email` hands a member one email from a folder of
  `.eml` files in the package (`mailbox`, `max_chars`); `save_draft` saves a
  reply in the run's `work/outbox/` and never sends it. By default a draft can
  answer only the email's sender (`reply_only: false` lifts that).
- Runs started in the same second get their own folders instead of failing
  with `FileExistsError`.

## 0.0.3

- **Backup models**: `model:` and `default_model:` may name several models,
  separated by commas, in order of preference
  (`default_model: openai/gpt-6-luna, anthropic/claude-sonnet-4-5`). Before the
  first turn each member gets the first one available, for the whole run; the
  console says so whenever a member is not on its first choice, and `run.json`,
  `events.jsonl` and `teamrunner runs` record it. A profile's `name:` still
  names one model.
- The examples name their model once, as `default_model:` in `team.yaml`.
- All six examples use Gemma 4 31B through OpenRouter with one API key.
  The fact checker reads public sources instead of requiring Brave Search;
  reference lookups use Crossref without a catalogue key. Reference lists
  and findings are shared in the meeting messages so the next checker and
  the editor can read them.
- Gemma 4 tool calls on OpenRouter no longer compete with strict JSON
  formatting: during tool use the final format is requested in the prompt,
  and the runner still validates the declared outputs.
- The README's tutorial and its five ideas hold only what works today; what is
  designed but not built yet (experiments, the whiteboard) is in `PREVIEW.md`.
  The README installs with uv or pip and needs one key.
- Comments in the examples, and the `.gitignore` `teamrunner new` writes, no
  longer cite internal design notes.

## 0.0.2 — the first preview

What the roadmap calls 0.1, the org package: a folder of text files that gets
a job done.

- **Teams from files**: a folder with `team.yaml` is a team; each member is a
  Markdown file (settings on top, the prompt as prose); `chair.md` is the
  script of who acts when, with rounds and decisions; with no chair, members
  take turns in name order. Model profiles in `models/` share a model and its
  settings across a team.
- **Commands**: `new` (empty, `--from` an example or another team, or
  `--describe` for a team written by a model), `validate` (with `--online`
  to check the models), `explain`, `run` (with `--message`, `--file`,
  `--task-file`; `--rehearse` runs the whole flow with no model and no cost),
  `runs`, `show`, `gather`, `skill`.
- **Toolkeepers** hold the tools, with an access list and guardrails, and four
  built-in tools: `web_search_brave`, `read_page`, `read_file`, and `run_code`
  (Python in a WebAssembly sandbox).
- **Every run is logged** in the team's `runs/` folder: `run.json`,
  `events.jsonl` and `output.md`, with costs, and the Python, LiteLLM and Deno
  versions it ran on. Search results are read by the team but not kept,
  unless `keep_results: true`.
- **Six examples ship**: `round_robin`, `multi_agent_debate`,
  `orchestrators_lite`, `fact_checked`, `reference_check`, and
  `hiring_committee` (a hidden-profile task, after Stasser and Titus 1985).
- Models from OpenAI, Anthropic and Google, and local models through Ollama.
  macOS, Linux and Windows; Python 3.10 to 3.13.
- Licensed GPL-3.0-or-later; the examples, the files `new` writes and the
  authoring guide are MIT-0.

## 0.0.1

Reserved the name on PyPI. No working code.
