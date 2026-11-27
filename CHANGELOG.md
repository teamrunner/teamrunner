# Changelog

TeamRunner is an early release: a 0.0.x version may change in ways that
break teams and run folders made with an earlier one. Each such change is
listed here.

## 0.0.8 — crash reports, and every run drawn as a picture

- **A crash sends a small report, and you can file the details yourself.** A
  crash is an error TeamRunner did not foresee: a fault of its own, not a
  missing key or a mistake in a file. When one happens TeamRunner now sends
  one report to `https://version.teamrunner.ai/crash`: its version, the
  Python version and the platform, the random install id (the same two
  headers as the daily version check), the kind of error (such as
  `KeyError`), and the file and line in TeamRunner's own code. Never the
  error's message, a traceback, a path of yours, a team, model, file, key or
  command; the server does not store your IP address. At most 5 a day, and
  the crash's message says when one was sent.
  **`TEAMRUNNER_NO_CRASH_REPORTS=1` turns them off.** This changes the
  README's and LEGAL.md's statement about telemetry, which now reads "apart
  from this check and crash reports".
- **`teamrunner report`** opens a bug report on GitHub in your browser,
  filled in with your version, your system and where the last crash
  happened. Nothing is posted until you press Submit. The full details stay
  on your computer in `~/.teamrunner/last-error.txt`, for you to read and
  paste from if you choose.
- **In the browser page, a crash shows a "Report this problem" button**
  beside its message, which opens the same filled-in bug report. Before, a
  run or an action that crashed in the page said only that it could not
  finish.
- **In the browser page, every run is drawn as a picture.** A run you open
  starts with its timeline: one cell per turn, coloured by the value of an
  output you choose (such as the judge's `winner`); click a cell to go to
  its message. **Runs → Show as: Wall** draws all of a team's runs that
  way, and an experiment's **Runs** tab has **Grid** and **Wall**, the wall
  grouped by condition.
- **`--input name=value` (`-i`) gives inputs by name**, repeated for
  several: `teamrunner run debate -i motion="Should zoos be abolished?" -i
  opening_side=pro`. The value is the text after the first `=`, as typed;
  a number or a list still comes from a task file. It can be used with
  `--message` and `--task-file`; an input given twice stops the run and
  names both.
- **When team.yaml does not say where `--message` goes, the run says where
  it went:** `your message went to topic, the team's only input`, on the
  line under the run line. The input's entry in `record.json` gains
  `via: --message`. Where it goes is unchanged.
- **A tab in a YAML file no longer stops it being read.** A tab between
  words or after `key:` is read as a space, as YAML allows; a tab that
  indents a line is named on its line: "this line is indented with a tab;
  YAML needs spaces to indent". An unreadable `team.yaml` is now the only
  problem reported, without "has no model" for every member.
- **About & notices in the page shows the version.**
- **A copy too old to run says so calmly.** When a version must be updated
  before it runs, it now says "TeamRunner 0.0.8 needs an update before it
  runs: version 0.0.9 or later", and that your teams, runs and keys stay as
  they are, instead of "has a known security problem and will not run".
- **Security: TeamRunner no longer runs the `git` program.** A run's record
  keeps the commit the team's folder is at. That was asked of git, and git
  obeys settings in the folder's own `.git/config`; 0.0.7 switched off the
  ones known then, but a team shared as a zip with a `.git` folder set up as a
  partial clone could still run a program on `run`, even `--rehearse`. The
  commit is now read from `.git/HEAD` as plain text, with or without git
  installed. **A record no longer has `git_dirty`**; `content_hash` already
  says whether two runs used the same files, committed or not.
- **Security: `conditions/` and `runs/` in an experiment must be its own
  folders.** An experiment someone sent could ship `conditions` as a link to
  a folder elsewhere, and `teamrunner experiment --plan` (or opening it in
  the page) copied a team's files there. A linked `conditions/`, condition
  folder or `runs/` is now an error and nothing is written through it. If
  you linked `runs/` to another disk yourself, make it a real folder.
- **Security: `teamrunner --update` no longer reads Python files from the
  folder you are in.** A `pip.py` or `teamrunner.py` in a downloaded folder
  was run in place of the real one. The update now runs from an empty
  folder, and on Windows a `uv` or `pipx` found only in the current folder
  is not used.
- **Lowering an evaluator's `evaluations:` counts only that many.** After
  going from 3 to 1, a run evaluated no, yes, no was read as "yes". The later
  evaluations stay in the file and count again if the number is raised.
- **A share of evaluations saying yes keeps its interval inside 0 to 1.**
- **`--keep-unchanged` never brings back runs from before a `--restart`.** A
  condition that had not changed counted its oldest runs, the ones the
  restart had set aside.
- **A task file edited while an experiment runs does not reach its runs.**
  Every run gets the inputs read when the session started; the edit shows as
  a changed study the next time.
- **A `.md` data file is part of the study.** A `.md` with no settings block
  is a note only in a team's own folder (beside `team.yaml`). Anywhere else,
  such as `docs/policy.md`, it is data a run can read, and editing it now
  changes the study as editing a `.txt` always did. Runs made by an earlier
  version still count when the files they ran on are the same as today's.
- **One session at a time runs an experiment.** A second `teamrunner
  experiment` on the same folder, or the page and the command line together,
  each ran every slot and each counted toward `max_cost_usd` alone. The
  second is now told the experiment is already running and starts nothing.
  `--plan`, `--summary`, `--export` and `--rehearse` are not held back. The
  lock is a hidden `.session.lock` in the experiment's folder, removed when
  the session ends.
- **A killed session's spending is counted.** `kill` and closing the
  terminal now stop a command as Ctrl-C does, so runs in progress write
  their records. Where a session still ended with no record (a crash, a
  power cut), the next session says so and counts what those runs had spent
  toward the ceiling; their slots run again.
- **A turn that does not finish keeps the cost of its calls.** A turn that
  paid for a call and then failed, or was stopped by the ceiling or Ctrl-C,
  left that cost out of the run's total. It is now written as a `retry`
  event of kind `unfinished` and counted.
- **A key file saved by Notepad or PowerShell is read.** A file starting
  with a byte-order mark lost its first key, and a UTF-16 file (PowerShell
  5's `echo KEY=… > .env`) brought every command down with a traceback. Both
  are now read; a file that is not text at all is named in one line.
- **An experiment names every missing key before anything runs.** Without
  keys it printed a raw `Problem(...)`, named one key per attempt and left
  failed run folders. Now every condition's and evaluator's keys are checked
  first, and `--plan` ends with a "Keys needed" line saying which are found.
- **In the page, Set up keys works with an experiment selected, or with
  nothing selected.** It answered "Choose a team folder", so a new workspace
  with no team yet had no way to set the key it asked for.
- **A free model can run under a ceiling.** The page's own experiment
  template writes `max_cost_usd:`, and on the demo team the page said it
  could not run, because a free model has no known longest reply.
- **`validate` names a `.md` file that is a note, not a member.** A `.md`
  with no settings block is left alone, as before, but `validate` now says
  so in a line, and a chair that names such a file says why it is not a
  member.
- **Security: sandboxed code cannot import a module from the internet.**
  `run_code` switched the network off, but Deno keeps a separate permission
  for importing modules, which by default allows six hosts (esm.sh, jsr.io
  and a GitHub one among them). It is now denied, with `network: true` too.
- **Security: `read_page` and `run_code`'s fetch check the name they
  connect to.** For a host name with `ß` (and a few other letters), the name
  checked and the name connected to differed, so a link could lead to this
  computer or its local network. Both are now checked.
- **The kept crash details hide keys from your shell, and are yours alone.**
  `~/.teamrunner/last-error.txt` hid only the keys in the key file; a key
  set only in the shell, and the command line, were kept as typed. Values of
  environment variables named like keys (`…KEY`, `…TOKEN`, `…SECRET`,
  `…PASSWORD`) are now hidden as well, and the file is readable only by you.
- **`TEAMRUNNER_NO_CRASH_REPORTS` set to any value turns reports off**,
  except `0`, `false` and `no`. Before, `on` still sent.
- **A link that leads nowhere in a team is not a crash.** With one in a
  team's data folder, `experiment --plan` stopped as a fault of
  TeamRunner's own and sent a crash report. The copy now leaves the link
  out, as the team itself cannot read it; `validate` names it.
- **A repeat count is at most 1,000, and `replications:` at most 10,000.**
  `rounds(*50000000)` validated, and a slip such as `*30000` is real money.
  **Breaking** only for a flow or an experiment with a larger number.
- **A model profile is named exactly.** `model: Careful.yaml` for the file
  `models/careful.yaml` passed `validate` on a Mac or Windows and then
  failed everywhere else; it is now an error that suggests the right name.
- **An unquoted date in a task file or `default_inputs` is the text you
  wrote.** `topic: 2026-01-01` crashed the run. An input with nothing after
  its colon is now asked for, in place of reaching the prompt as `null`.
- **A single run with `--max-cost` checks the ceiling can be kept before it
  starts**, as an experiment does: a model with no list price or no known
  longest reply used to stop the run at that member's first turn, after
  earlier turns were paid.
- **`explain` gives a range when a decision can jump forward.** It said "5
  turns" for a flow that took 3 or 5.
- **`new --from` reads files saved by Windows tools**, and a copy that
  cannot be finished leaves nothing behind.
- **Ids are written safely when several runs start at once**: beside the
  file and moved into place, so no run reads a half-written `team.yaml`.
- **`validate` warns when a team has no member that speaks**, refuses a
  block called `done` or `stop`, and stops with an error on a link back to
  the team's own folder, which used to hang it.
- **`gather` changes nothing when a link leads nowhere**, where it used to
  stop part-way.
- **`prices.yaml` refuses `.inf`, and so does `max_cost_usd:`.**
- **The two switches can be set in the key file.** A line
  `TEAMRUNNER_NO_CRASH_REPORTS=1` (or `TEAMRUNNER_NO_VERSION_CHECK=1`) in
  `~/.teamrunner/.env` now works, for anyone who has never set a shell
  variable.
- **Experiments: a condition that changes nothing is warned about.** A slip
  in a name under `change:` made two identical teams, and the plan said so
  only as information.
- **Experiments: one variant file can be used for two members.** After the
  first run the experiment was refused for good, as a duplicate id.
- **Experiments: a linked folder in the team no longer stops every run**
  with "changed while the experiment was running".
- **Experiments: `--plan`, `--summary` and `--export` leave a built
  condition alone when nothing in it changed**, so they cannot disturb an
  experiment that is running.
- **Experiments: `--plan` with a ceiling and a model with no list price
  says which model**, where it crashed.
- **Experiments: the summary's first line gives what the counted runs
  cost**, the figure its rows add up to; before, it left out turns a
  continued run reused. The estimate after round 1 counts that round's own
  evaluations. Marginal means are labelled "pooled over runs".
- **Experiments: `evaluations.csv` gives an evaluation's cost once**, on its
  first row, so the column adds up to what was spent; a reply that never
  fit, and was paid for, now has a row.
- **A value that is not one of a setting's choices is named in words**:
  "should be `pdf`", not "'html' is not one of ['pdf']".
- **Security, in the page: + Variant, + Metric and + Task stay inside the
  opened folder**, and "Open run folder" on a Mac shows the folder in Finder
  and never starts anything in it.
- **In the page, the cost estimate no longer holds up the Run dialog.** It
  is asked for apart from the dialog, only for a real run, and stops after
  two seconds on a very long flow, saying so.
- **In the page, one failed request no longer declares TeamRunner
  stopped.** The page asks once more first; a file that is too large is
  answered, not dropped.
- **In the page: + Team never makes a team inside a team**; a run cannot be
  started in a condition's built folder, where the next build deleted it;
  the copy of a saved team goes in `teams/`; Refresh no longer blanks an
  open comparison; the bar, + Chair and the trash follow the file you
  clicked; Plan, Summary and the + buttons act on the experiment on screen;
  and dialogs give plain sentences, without Python's own error text or
  whole paths.

- **The examples use one seed, 1.** The README's experiment and the shipped
  `leader_vs_flat` example now say `seed: 1`, as the starter `experiment.md`
  already did. A copy of `leader_vs_flat` you made earlier keeps its own seed
  and its runs.

## 0.0.7 — a team someone else wrote stays in its own folder

Four security fixes. Each closes a way for a team or experiment you
downloaded to do something `validate`, `explain` and `--plan` gave no hint
of. Update if you run teams other people wrote.

- **A member's `params:` takes model settings only.** Allowed:
  `temperature`, `top_p`, `top_k`, `max_tokens`, `max_completion_tokens`,
  `seed`, `stop`, `presence_penalty`, `frequency_penalty`,
  `reasoning_effort`, `verbosity`, `thinking`. Before, any word was passed
  to LiteLLM, so a team could set `api_base` and send your API key and the
  conversation to another server, or set `mock_response` and record made-up
  replies as real, paid turns. The same list applies to model profiles,
  evaluators' `params:` and settings an experiment's `change:` builds.
  **Breaking:** a team that used another setting (for example Ollama's
  `num_ctx`) now fails `validate`, which names the line and suggests the
  nearest allowed word.
- **An experiment's `change:` cannot reach outside the team.** A key with
  `..`, a backslash or an absolute path, or one that resolves outside the
  condition's copy of the team, is an error naming its line in
  `experiment.md`, and nothing is written. Before, `--plan` (and opening an
  experiment in the page) could overwrite or delete files elsewhere on your
  computer. A change also no longer writes or deletes through a symbolic
  link.
- **Git settings in a team's folder run no command.** Recording which commit
  a team is at used `git status`, which obeys a repository's own
  `core.fsmonitor` and content filters, so a team shared as a zip with a
  `.git` folder could run a program on `run`, even `--rehearse`. Git is now
  called with those switched off. When the repository's own config names a
  filter or an include (Git LFS does), the record keeps the commit and the
  content hash but has no `git_dirty`.
- **A metric's name is a plain word**: letters, digits, `_` and `-`. It
  becomes a file name under `evaluations/`, and before, `name: ../../x`
  wrote prompts and replies outside the experiment. **Breaking:** a metric
  file whose own name has a dot or a space, and no `name:`, is now refused.

Also:

- **Two conditions whose names differ only in capitals are an error**, as
  are two levels of a factor that do. On macOS and Windows `Short` and
  `short` are one folder, so every `Short` run ran `short`'s team under
  `Short`'s name. Refused on every system, so an experiment works wherever
  it is opened. **Breaking** for an experiment that had such a pair.
- **A condition cannot be called `con`, `nul`, `aux`, `prn`, `com0`–`com9`
  or `lpt0`–`lpt9`**, in any capitals: Windows makes no folder of that name.
- **Adding an id never breaks a file.** A `team.yaml` that opens with `---`
  (as many editors write it) got its `id:` above that line and no longer
  read; the id now goes below it. And if an id cannot go into a file without
  breaking it (a `team.yaml` written as JSON, for example), the file is left
  exactly as it was, `validate` says so, and `new --from` makes nothing.
- **A decision arrow cannot jump to a toolkeeper.** In
  `a -> web -> b` with `b --again--> web`, `validate` said OK, and the run
  stopped when `b` answered `again`, after paid turns: a toolkeeper answers
  only the member whose turn just asked it, and `b` only decided. It is now
  an error that suggests pointing the arrow at the member who asks
  (`b --again--> a`), after whom the flow goes through `web` again.
  **Breaking** for a `chair.md` with such an arrow, which could never
  complete that way.
- **`run_code` with `network: true` reaches the internet only**: never this
  computer, the local network (IPv6 included) or a cloud machine's metadata
  address. Before, it could reach Ollama, the TeamRunner page and anything
  else on your network. The code now has no network of its own: its
  `fetch()` is handed to TeamRunner, which makes the request with
  `read_page`'s rule, redirects included, and refuses replies over 25 MB.
  Only `fetch()` reaches the internet; Deno's own sockets are refused. Files
  the code leaves in its work folder are capped at 100 MB.
- **Exported tables write a text cell that Excel would run as a formula**
  (starting with `=`, `+`, `-`, `@`, a tab or a carriage return) with a `'`
  in front, which Excel shows as text. **Breaking** for analysis: R and
  pandas keep the `'`; the README shows how to take it off.
- **A key in a provider's error is hidden.** `events.jsonl`, `record.json`
  and `output.md` record every error with any key in it replaced by
  `[key hidden]`; before, only some errors were.
- **The key file is private from the moment it is made**, and saving a key
  again replaces the old value instead of leaving it in the file.
- **`teamrunner ui` no longer puts the page's address, with its token, on a
  command line** other people on the computer could see: it opens the
  browser at a page only you can read, which sends it on, and deletes that
  page when the server stops.
- **The browser page keeps a toolkeeper's mailbox inside the opened folder**,
  as it already did `run_code`'s folders.
- **A team's `name:` reaches its run folder's name as a plain word**:
  letters, digits, `_` and `-`, at most 60 characters. Before, `:`, `*` or
  `?` stopped the run, and on Windows `..\` could put the folder outside
  `runs/`.
- **`explain --diagram` works offline**, with the Mermaid installed with
  TeamRunner, and writes its page under a new name each time. A page saved
  with `--save` loads Mermaid 12.3.0 from the CDN, checked by its hash.
- **pypdf 6.21 or later**, since `read_page` reads PDFs from anywhere.
- **More kinds of key are refused in a team's files**: Brave, Groq and xAI
  keys too, in `.csv`, `.tsv`, `.py`, `.toml`, `.ini` and `.cfg` files as
  well as `.md`, `.yaml`, `.json` and `.txt`; and an experiment's own files,
  `experiment.md` included, are checked as a team's are.
- **A key file anyone else could read is made yours alone** when TeamRunner
  reads it, and says so once.
- **`teamrunner ui debate` opens `debate` from your workspace**, as the other
  commands do: a team, else an experiment, of that name. A folder of that
  name where you are still comes first.
- **Messages no longer name versions such as 0.1 or 0.3**, which did not
  match the releases you install: a planned feature "comes in a later
  version of TeamRunner". Nor do they cite decision numbers such as "(D120)".
- **Clearer help and messages.** `--max-cost` says it limits an estimate at
  list prices, not your bill. `new --help` and the team argument of
  `validate`, `run` and `explain` say a name is found in your workspace. A
  word TeamRunner does not know suggests updating TeamRunner (`teamrunner
  --update`, as the next entry describes).
  `validate` with a key missing says the team is "not ready to run yet"
  rather than that a run cannot start. `--plan` explains its "at most"
  figure when it is above the ceiling, and no longer prints LiteLLM's
  "Provider List" line.
- **`teamrunner --update` updates TeamRunner the way it was installed**:
  `uv tool upgrade` for a uv tool, `pipx upgrade` for pipx, `uv pip` or
  `pip` in an environment. A copy installed from a source folder says to
  update that folder instead. It works even in a version that refuses to
  run for a security problem, and the update reminders now name it in
  place of `pip install -U teamrunner`, which did not reach a uv tool.
- **Describe a team in the browser page.** + Team has a third choice,
  "Describe it": type what the team should do, or load a `.txt` or `.md`
  file into the box (it is read in your browser, not uploaded), pick a
  model whose key is set, and **Write team**. The page shows which try the
  model is on, then the cost, its choices and `explain`. On the command
  line, `teamrunner new my_team --describe-file brief.txt` reads the
  description from a file. Either way the description is kept as a comment
  at the top of `team.yaml`, and may be at most 50 KB.
- **An example experiment ships**: `teamrunner new my_study --from
  leader_vs_flat` copies it into your workspace's `experiments/`, and the
  `orchestrators_lite` team it runs into `teams/` if no team of that name
  is there. It compares a flat review team with one that has a visible
  leader, scored by two answer keys against planted bugs.
- **The README documents metric files**: how to write an answer key and an
  evaluator, what each setting means, and how a task file gives every
  condition the same inputs.
- **The README is reshaped**: it opens with your first run on the demo model,
  with no account or key, and where your words go; the reasons we built
  TeamRunner come after the tutorial; each notice (costs, the demo's
  privacy, the workspace rules) is written once; a glossary explains the
  terms. It is also checked against this version: the browser page's four
  tabs, the fact checker's steps in order, a whole block for ending the run
  on a third failure, a `cd` that keeps exports out of the team, the
  settings each toolkeeper takes, what a team someone sent you can and
  cannot do, and full links that work on PyPI.
- **A one-dash typo in `chair.md` is an error.** A line `--pass-> c`
  (meant as `--pass-->`) became a plain arrow and the decision vanished;
  `validate` now names it and suggests `--pass-->`.
- **A provider failure no longer makes a run's cost unknown.** A call the
  provider failed got no reply and is counted as costing nothing, as the
  ceiling already counted it; `record.json` says how many there were
  (`provider_failures`).
- **Broken files are problem lines, not Python errors**: a file that is not
  UTF-8 or cannot be read, `kind:` or `tools:` of the wrong shape, and a
  toolkeeper setting of the wrong type (`calls_per_turn: lots`,
  `only_input: [a]`) each name the file, the line and the fix. Anything
  unforeseen prints one line and keeps the details, keys hidden, in
  `~/.teamrunner/last-error.txt` (exit code 3).
- **A file saved by Windows Notepad is read as written.** Its invisible
  byte-order mark made a member silently not a member; it is now ignored
  when reading and kept when an id is added.
- **A repeated turn cannot come just before a toolkeeper.** In
  `judge:research(*2) -> web`, the first turn's request was dropped while the
  judge thought it was sent; `validate` refuses it and shows a repeated
  block that asks each time.
- **`new --describe` says plainly when the model fails**, and leaves nothing
  behind.
- **A single run's `--max-cost` counts tool calls**: a priced call (a web
  search) is set aside from the ceiling before it is made, and refused, as
  "the run's cost ceiling is reached", when it does not fit.
- **Smaller fixes:** `explain` shows an empty default as `""` instead of "no
  default"; `runs` shows `?` turns for a record without a count instead of
  stopping; `--max-cost inf` is refused; `show debate/judge` finds the team
  in your workspace from anywhere; `explain --save` without `--diagram` is
  an error, not ignored.
- **An unknown price says how to add it.** When a model has no list price
  (it is not in LiteLLM's table), the check before a run, the run's last
  line, `teamrunner runs` under any `$?`, the page's run dialog and `new
  --describe` say that its cost shows as unknown, that the provider's own
  charge is still recorded when it reports one, and the line to add to
  `~/.teamrunner/prices.yaml`.
- **The browser page acts on the team it names.** The bar, **Run…**,
  **Validate**, **Set up keys** and **+ Member** follow the team chosen last,
  a team's folder or a file in it; before, a folder click changed the bar
  while the buttons acted on the open file's team.
- **When TeamRunner stops, the page says so** in one banner, keeps the
  editor's text with a button to copy it, and stops trying; a live run says
  it stopped with TeamRunner. Before, it reported "This file changed on
  disk" and "Failed to fetch", and said the run continued.
- **A finished run no longer pulls you away** from another tab: the Run tab
  shows a check mark, and the run opens when you choose it.
- **The page's run views are sturdier**: a run that cannot be opened clears
  the one before it; one malformed event shows as such and the rest of the
  run opens; long runs draw 200 events at a time; the two files clicked in
  quick succession open in order; an id TeamRunner adds while you type goes
  into your text, so saving works; the open file is checked by its
  fingerprint instead of downloaded every 3 seconds; experiment progress
  never stops updating, and a refresh never cancels opening another one.
- **The run dialog estimates a run's cost** before **Run with models**, as an
  experiment's does: typical and at most, at list prices, or that the
  team's models cost nothing.
- **Commands shown in the page name what they describe** (a run's own
  team), and **Compare selected runs** says how many are ticked; ticks are
  cleared when the list changes team.
- **Accessibility:** error text in dark mode is readable (4.5:1 or more),
  disabled buttons show "not allowed" rather than "busy", and an
  experiment's cost confirmation takes focus.
- **An experiment stops if a team is edited while it runs.** A condition
  run as written reads the team's folder afresh for each run, so an edit
  made mid-experiment was filed under the old study. Now each run's copy is
  checked first; on a difference no more runs start, the runs so far are
  kept, and the next command says what changed.
- **`--restart` and `--keep-unchanged` are remembered** in the experiment's
  `runs/study.jsonl` when they are given, even when no run follows. Before,
  `--keep-unchanged` with nothing new to run was forgotten, and `--summary`
  and `--export` counted no run.
- **Runs beyond `replications:` do not count.** Lowering it left the extra
  runs counted ("9 of 6"); they are kept, said in the summary, and count
  again if it is raised.
- **An evaluation whose reply never fit is tried again**, instead of being
  reported as "evaluator changed" and skipped for good.
- **Raising an evaluator's `evaluations:` adds grades instead of redoing
  them**, and editing its `description:` changes nothing. Grades already
  made with the same rubric and settings still count.
- **The cost estimate counts evaluations still owed** from earlier sessions.
- **An evaluator's `{{inputs.<name>}}` must name an input** a condition's
  team has; a typo is an error with the nearest name, instead of "(none)"
  sent to the grader.
- **Answer keys compare numbers as numbers** (`3` = `3.0`) and `true` or
  `false` in any capitals.
- **Intervals:** a yes/no measure's marginal rows use the Wilson interval, as
  its condition rows do, so they stay between 0 and 1; and the t multiplier
  is exact for any number of runs, instead of 1.96 above 30.
- **`label: 2` is the label `2`**; `label: false` says to quote it.
- **Each factor's column in `runs.csv` is now `factor_<name>`**
  (`factor_structure`, not `structure`). A factor named like one of the
  table's own columns (`round`, `seed`, `status`, `turns`, …) made
  `--export` stop with a Python error after the runs were paid for; now any
  factor name exports. **Breaking** for an analysis script that reads a
  factor's column by its bare name.
- **Examples and messages name `anthropic/claude-sonnet-5-5`** where they
  named `anthropic/claude-sonnet-4-5`, which Anthropic retires on
  2026-11-30. This includes the README's experiment tutorial and the model
  `new --describe` offers with an Anthropic key.
- **An answer key with both `source:` and `compare:` is an error** saying to
  keep one. Before, `--plan` stopped with a Python error.
- **A `stage:` beside `compare:` is an error**, saying to move it into the
  part it is for (`reported: {…, stage: report}`). Before, it was silently
  ignored.
- **An evaluator's `output:` is checked as a member's is.** A field written
  in braces without `type:` (`s: {options: [a, b]}`) is an error naming the
  field. Before, `--plan` stopped with a Python error.
- **A model profile named with a folder (`sub/careful.yaml`) is an error**
  naming the line, with the file to move it to: profiles sit directly in
  `models/`. Before, `validate` said OK and `run` stopped with a Python
  error.
- **Two members with the same `id:` are an error** naming the second file,
  which usually is a copy; delete its `id:` line and `validate` writes a new
  one. Before, the two shared one history without a word.
- **A setting an experiment reads from a file drops the file's last line
  break**: `emma.model: file:variants/m.txt` sets `openai/gpt-5-mini`, not
  `"openai/gpt-5-mini\n"`. Only one, so a text that ends in a blank line
  keeps it.
- **+ Experiment, + Variant, + Metric and + Task in the browser page.** In
  Experiments, **+ Experiment** makes `experiments/<name>/experiment.md`
  from a team you choose: a plan, with notes on what to fill in, comparing
  the team as it is with a copy to change. With an experiment chosen,
  **+ Variant**, **+ Metric** and **+ Task** each make one file in its
  `variants/`, `metrics/` or `tasks/` folder and show the line to add to
  `experiment.md`; the page never edits that file itself.
- **The page's sidebar is laid out as your workspace is.** **Teams |
  Experiments** sits above it; `teamrunner/` heads the list with `teams/`
  and `experiments/` beneath, always in that order. The one in use folds
  open; the other is greyed, and clicking it switches. The team's name is
  now the heading of the bar whose buttons (**Validate**, **Run…**) act on
  it, and the tab row no longer repeats it.
- **A member with no instructions stops a real run.** A member whose text
  is still **+ Member**'s starter ("Write this member's instructions
  here.") or empty was sent to the model as it was; the check before a run
  now names it. A rehearsal calls no model, so it still runs.
- **The demo model is Claude Haiku 5.5, called directly at Anthropic**
  (it was reached through OpenRouter); README, `LEGAL.md` and the page's
  notice say so, and that the demo server now keeps daily call counts per
  model (no addresses, prompts or replies). Replies may be up to 1,024
  tokens.
- **The demo's limits are clearer.** When its daily or monthly allowance
  is used up, the run stops at once and says when it resets, instead of
  retrying; only the per-minute limit, which frees up within the retry
  waits, is retried. A demo call that gets no answer says "the demo model
  did not answer in time". A retry shows as a passing line in the terminal
  (kept with `--verbose`) and under the live status in the page, not in
  the conversation.

## 0.0.6 — experiments in the browser page

- **A daily version check.** Once a day TeamRunner reads
  `https://version.teamrunner.ai/version.json`. Below its `min_version` it
  says a newer version is wanted and runs anyway
  (`TEAMRUNNER_NO_VERSION_CHECK=1` hides this); below its
  `min_secure_version`, the oldest version still considered secure, it
  refuses to run until updated, even offline once it has seen that floor.
  The request sends two headers only: TeamRunner, Python and platform in
  `User-Agent`, and a random install id kept in `~/.teamrunner/install-id`.
  The server logs those, never the IP address. If the check fails,
  TeamRunner says nothing and carries on. The README and `LEGAL.md` say
  exactly what is sent.
- **The demo model is sent TeamRunner's version** in the `User-Agent` of
  each request, so the demo can stop answering versions that are too old.
- **A free model fits any cost ceiling.** `--max-cost` and `max_cost_usd`
  no longer stop the demo model or a local Ollama model for want of a
  longest reply: a call that costs nothing always fits.
- **An account out of credit fails at once**, saying so, instead of being
  retried for a minute as if it were a rate limit.
- **The browser page has an Experiments tab.** It lists the experiments
  in the opened folder; opening one shows its plan (conditions, the
  differences file by file, the order of runs and the cost estimate
  against the ceiling), its runs as a grid of conditions by replicates,
  and its summary, with marginal means for two or more factors. Each run
  opens in the Run tab. It reads the same figures as `teamrunner
  experiment --plan` and `--summary` and spends nothing.
- **Files in an experiment are checked as an experiment.** With
  `experiment.md`, or a file in its `variants/` or `metrics/`, open in the
  Files tab, the bar names the experiment, and Validate (and saving)
  checks it as `teamrunner experiment --plan` does: its settings, every
  condition's team, each change and each metric, with each problem at its
  line. No keys are needed and nothing is spent. Run… becomes Open in
  Experiments.
- **Rehearse and Stop from the Experiments tab.** Rehearse · free runs
  `teamrunner experiment --rehearse` from the page: running slots pulse on
  the grid, each run's line appears as it finishes, and the tab shows a dot
  while anything runs. Stop works as Ctrl-C does. Several experiments can
  run at once, each only once.
- **Run and Evaluate from the Experiments tab, after seeing the cost.**
  Run… shows the missing runs, the estimate, what is already spent and the
  ceiling, and what would stop it (a missing key, a model that cannot be
  reached, no list price); it starts only when you press Run ≈ $…, and only
  if nothing changed since. A changed study offers Start over or Keep
  unchanged conditions' runs. From the page an experiment needs
  `max_cost_usd`, unless every model it uses is free. Evaluate… appears
  when evaluations are missing or out of date.
- **The ceiling reads as the whole experiment's**: "6% of the ceiling:
  $40.00 for the whole experiment, running and judging (experiment.md)".
- **A condition's generated folder is read-only.** Files under an
  experiment's `conditions/` are built from `experiment.md` and built again
  before every run, so the Files tab opens them read-only and says where to
  make the change instead.
- **Runs that cost far more than usual are flagged.** In an experiment's
  summary (and its Runs grid in the browser page), a run that cost more
  than 3× the typical run of its condition is listed with both costs. It
  still counts: the flag only says it is worth reading.
- **An evaluator's settings are checked before anything is spent.** A
  setting its model refuses (`temperature: 0` on a model that takes only
  1, say) stops `teamrunner experiment` before the first run, and is listed
  on the page's Run… and Evaluate… as what would stop it. Before, the runs
  were made and paid for, and every evaluation then failed.

## 0.0.5.1 — names from anywhere

- **In `chair.md`, every line stands on its own.** The order of the lines
  and their indentation no longer matter: `rounds(*3) -> judge` may come
  after `rounds = for -> against`. Lines join where one ends and the next
  begins (`a -> b` and `b -> c`). A decision on its own line belongs
  wherever its decider takes a turn, or, if that is in several blocks,
  with the nearest line above that holds it. A labelled arrow may sit
  inside a line of steps: `for -> fact_checker --pass,*3--> done`. Every
  flow that was valid before means the same thing.
- **A team or experiment named alone is in your teamrunner workspace,
  from anywhere.** `teamrunner run debate`, `validate debate`, `explain
  debate` and `runs debate` mean `teams/debate` in your workspace;
  `teamrunner experiment study` means `experiments/study`; an experiment's
  `from: debate` means the same team. Anything that starts with `/`, `~`,
  `./` or `../`, or has a slash in it, is a path from where you are, as
  before. When nothing is found, the message says where it looked and how
  to write the other one. **This changes what `new` does:** `teamrunner new
  debate` now makes the team in your workspace's `teams/` wherever it is
  typed (asking where the workspace is, the first time); `teamrunner new
  ./debate` makes it where you are.
- **An evaluator that sees nothing of the run is refused.** Its rubric must
  use `{{transcript}}`, `{{output}}` or a member's typed answer.
- **`validate` starts faster**: it reads LiteLLM's model table without
  importing LiteLLM, and says "Checking keys and models…" while it asks the
  providers.

## 0.0.5 — experiments

- **`teamrunner experiment <folder>`** runs versions of a team side by side,
  many times each. One `experiment.md` names the versions as `conditions:` —
  each `from:` a team, with what to `change:` (a setting, a prompt, a whole
  file, or `remove`) — or as a full factorial with `factors:`. The team is
  never edited: changed conditions are built under `conditions/`.
- Seeded replications in rounds: each round runs every condition once, in an
  order drawn from the master seed, so the conditions stay balanced if the
  experiment stops early. `--plan` shows the conditions, their differences
  file by file, the order and the cost estimate, spending nothing;
  `--rehearse` runs everything with no model.
- **Budget checks:** `max_cost_usd` (or `--max-cost`) reserves each model
  call's estimated upper cost before sending it. Calls that do not fit are
  refused; an experiment whose estimate exceeds the limit does not start.
  Tool costs are added after each run and can take the recorded total over
  the limit. This is not a cap on provider billing: actual charges can exceed
  estimates or configured budgets. `teamrunner run --max-cost` uses the same
  checks for a single run. See [LEGAL.md](LEGAL.md).
- **Resume:** running the command again skips completed runs and redoes runs
  that failed or were cut off, in the same slot with the same seed, reusing
  their recorded turns. A changed study stops until `--restart` or
  `--keep-unchanged`.
- **Metrics:** a member's typed output named by its path
  (`judge.output.winner`), answer keys (`.yaml`) and evaluators (`.md`, a model
  with its own rubric, blind to the condition; `--evaluate`, `--no-evaluate`).
  The summary — n, mean or shares, 95% intervals — prints at the end and with
  `--summary`. `--export <dir>` writes five long CSV tables for R, Stata or
  pandas.
- Every run's record is now `record.json` (folders with `run.json` are still
  read). It adds `stopped_by`, `cost_reused_usd` and `cost_charged_usd` — what
  the provider says it charged, beside the list-price `cost_usd`. Experiment
  runs also keep `resolved/`, an exact copy of the team as it ran.
- **`teamrunner validate` now checks the keys and the models too**, once the
  files have no errors: the same free check `run` makes before its first turn.
  `--offline` checks the files only. `--online` is no longer needed; it is
  still accepted and does nothing. With no connection, `validate` reports the
  files and warns that the models were not checked. **This can change a
  script's result:** a missing key or an unknown model now makes plain
  `validate` end with an error.
- **Plain `teamrunner ui` opens your teamrunner workspace from anywhere**, no
  longer the current folder. The first time, it asks once where your
  workspace should be (Enter for `~/teamrunner`), makes it with `teams/` and
  `experiments/` inside, and keeps the answer as `TEAMRUNNER_WORKSPACE` in
  `~/.teamrunner/.env`. **+ Team** in the page, and `teamrunner new <name>`
  typed in the workspace, put the new team in `teams/`; a name with a slash is
  used as written. `teamrunner ui <folder>` still opens that folder, this
  once; add `--remember` to make it your workspace. **This changes what the
  command opens:** for the current folder, type `teamrunner ui .`. In a script
  with no one to ask and nothing remembered, the current folder is opened as
  before.
- `teamrunner ui`: a team can be moved to the system trash with the bin in
  the team bar; the question names the files and recorded runs that go with
  it. `runs/` is shown greyed, and nothing can be created beside a run record.
  A **Feedback** button names where bugs, questions and ideas go, with your
  version and system filled in; nothing is sent from the page.
- `teamrunner ui`: a **+ Chair** button adds `chair.md` to the selected team,
  with a commented starter for the flow. The chair is not a member, so
  **+ Member** refuses `chair.md` and points to the button.
- `teamrunner ui`: one **Validate** button does what `teamrunner validate`
  does, and its tick goes away at the next edit. Saving makes the same check,
  reusing a provider's list of models for a few minutes; the button asks again. The file list is a folder
  tree showing each file's kind (team, chair, agent, toolkeeper); team actions
  and file actions sit in separate bars; "As it will run" appears only for
  members; "Set up keys" moved to the bottom of the sidebar. The page's tabs are
  now **Files**, **Run** (the run in progress, or a saved run you opened) and
  **Run history** (the archive); a finished run opens as a saved run in place.

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
