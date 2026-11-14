// SPDX-License-Identifier: GPL-3.0-or-later
// Copyright 2026 Kumar Muthuraman
"use strict";

// The Experiments tab (D141 (4)): the experiments in this folder; one experiment's
// plan, its runs by condition and its summary. Every run opens in the Run tab.
// The same figures as teamrunner experiment --plan and --summary. Rehearse, Run, Evaluate
// and Stop run teamrunner experiment in the server (D150). A real run or an evaluation
// starts only on the confirmation the page showed, with its cost (D151).

let experimentPath = "", experimentData = null, experimentRequest = 0, slotsShown = "real";
// Experiments running in this server session (D150: several at once, each only once), by path,
// and the open experiment's job with its lines. Polled while anything runs.
let experimentJobs = {}, experimentLive = null, experimentPoll = null, experimentSeen = "", experimentQuote = null;
const expNotice = text => { byId("experiments-notice").textContent = text; };
const usd = value => typeof value !== "number" ? "unknown" : value === 0 ? "$0" : `$${value.toFixed(value < 0.1 ? 4 : 2)}`;
const num = value => value == null ? "—" : (value !== 0 && Math.abs(value) < 0.1 ? value.toFixed(4) : value.toFixed(2));
const pct = value => `${Math.round(value * 100)}%`;
const plural = (n, word, many = word + "s") => `${n} ${n === 1 ? word : many}`;

function cells(row, values, tag = "td") { for (const value of values) row.append(value instanceof Node ? (() => { const c = node(tag); c.append(value); return c; })() : node(tag, String(value))); return row; }
function tableOf(head, className = "exp-table") {
  const table = node("table", undefined, className), thead = node("thead"), body = node("tbody");
  thead.append(cells(node("tr"), head, "th")); table.append(thead, body); return [table, body];
}
function section(title, ...children) { const box = node("section", undefined, "exp-section"); box.append(node("h3", title), ...children); return box; }

async function experimentsView() {
  showView("experiments");
  if (experimentPath && experimentData) return;
  await loadExperiments();
}
async function loadExperiments() {
  experimentPath = ""; experimentData = null;
  byId("experiment-detail").hidden = true; byId("experiments-list-panel").hidden = false;
  byId("experiments-command").textContent = "teamrunner experiment <folder> --plan";
  expNotice("Looking for experiment.md files…");
  try {
    const result = await api("/api/experiments");
    const list = byId("experiments-list"); list.replaceChildren();
    byId("experiments-empty").hidden = result.experiments.length > 0;
    byId("experiments-table").hidden = !result.experiments.length;
    for (const exp of result.experiments) {
      const row = node("tr"), open = node("button", exp.name); open.type = "button";
      open.addEventListener("click", () => openExperiment(exp.path));
      const name = node("td"); name.append(open);
      if (exp.problem) name.append(node("span", exp.problem, "run-label"));
      if (experimentJobs[exp.path] && !experimentJobs[exp.path].done) name.append(node("span", experimentJobs[exp.path].rehearse ? "● rehearsing" : "● running", "run-label exp-running"));
      row.append(name, node("td", exp.path), node("td", exp.conditions ?? "—"),
        node("td", exp.runs ? `${exp.real} of ${exp.runs}` : `${exp.real}`), node("td", String(exp.rehearsals)));
      list.append(row);
    }
    expNotice("");
  } catch (error) { expNotice(error.message); }
}

async function openExperiment(path) {
  const request = ++experimentRequest;
  showView("experiments"); expNotice("Reading the experiment…");
  try {
    const data = await api("/api/experiment" + pathQuery(path));
    if (request !== experimentRequest) return;
    if (path !== experimentPath) { delete byId("experiment-detail").dataset.tab; slotsShown = "real"; closeConfirm(); }
    experimentPath = path; experimentData = data;
    byId("experiments-list-panel").hidden = true; byId("experiment-detail").hidden = false;
    experimentLive = experimentJobs[path] ? await api("/api/experiment-job" + pathQuery(path)).catch(() => null) : null;
    renderExperiment(); expNotice("");
  } catch (error) { if (request === experimentRequest) expNotice(error.message); }
}

// Re-read the open experiment without leaving the tab the person is on.
async function refreshExperiment() {
  const path = experimentPath, request = ++experimentRequest;
  try {
    const data = await api("/api/experiment" + pathQuery(path));
    if (request !== experimentRequest || path !== experimentPath) return;
    experimentData = data;
    if (!byId("experiment-detail").hidden) renderExperiment();
  } catch (error) { /* the next poll tries again */ }
}

function showJobControls() {
  const job = experimentLive, running = Boolean(job && !job.done), data = experimentData;
  const asking = !byId("experiment-confirm").hidden;
  byId("rehearse-experiment").hidden = running || asking || !data?.ready;
  byId("run-experiment").hidden = running || asking || !data?.ready || (data.summary.completed === data.runs && !data.changed);
  byId("evaluate-experiment").hidden = running || asking || !data?.ready || !data.summary.to_evaluate;
  byId("evaluate-experiment").textContent = data?.summary?.to_evaluate ? `Evaluate ${data.summary.to_evaluate}…` : "Evaluate…";
  byId("stop-experiment").hidden = !running;
  byId("stop-experiment").disabled = job?.state === "stopping";
  const line = byId("experiment-job");
  line.hidden = !job;
  if (!job) return;
  const what = job.rehearse ? "Rehearsal" : job.evaluating === "only" ? "Evaluation" : "Run";
  const finished = `${plural(job.finished, "run")} finished`, spent = job.rehearse ? "" : ` · ${usd(job.spent_usd)} spent by these runs`;
  line.textContent = {
    starting: `${what} starting…`, running: `${what} in progress · ${finished}, ${job.running.length} running${spent}`,
    stopping: `Stopping every run in progress… · ${finished}${spent}`,
    stopped: `${what} stopped · ${finished}${spent}. ` + (job.rehearse ? "A new rehearsal runs every slot again." : "Starting again picks up where it stopped."),
    finished: `${what} finished · ${finished}${spent}.`, failed: `${what} could not finish · ${finished}${spent}. The lines below say why.`,
  }[job.state] || job.state;
  line.append(" ", node("code", job.command));
}

async function startRehearsal() {
  if (!experimentData?.ready) return;
  byId("rehearse-experiment").disabled = true;
  try {
    experimentLive = await api("/api/experiment-start", {path: experimentData.path, rehearse: true});
    experimentJobs[experimentData.path] = experimentLive;
    slotsShown = "rehearsal"; byId("experiment-detail").dataset.tab = "runs";
    renderExperiment(); pollExperiments();
  } catch (error) { expNotice(error.message); }
  finally { byId("rehearse-experiment").disabled = false; }
}

// --- a real run's confirmation (D151): a quote, a budget line, a sign-off on that quote only ---

function closeConfirm() { experimentQuote = null; const box = byId("experiment-confirm"); box.hidden = true; box.replaceChildren(); showJobControls(); }

async function askToRun(change = null, evaluating = "both", why = "") {
  if (!experimentData?.ready) return;
  for (const id of ["run-experiment", "evaluate-experiment"]) byId(id).disabled = true;
  expNotice("Working out what it would cost…");
  try { renderConfirm(await api("/api/experiment-quote", {path: experimentData.path, change, evaluating}), why); expNotice(""); }
  catch (error) { expNotice(error.message); }
  finally { for (const id of ["run-experiment", "evaluate-experiment"]) byId(id).disabled = false; }
}

function renderConfirm(q, why = "") {
  experimentQuote = q;
  const box = byId("experiment-confirm"), evaluating = q.evaluating === "only";
  box.replaceChildren(); box.hidden = false; showJobControls();
  if (why) box.append(node("p", why, "exp-warning"));
  const buttons = node("div", undefined, "exp-confirm-buttons");
  const cancel = node("button", "Cancel"); cancel.type = "button"; cancel.addEventListener("click", closeConfirm);
  if (q.changed) {
    box.append(node("h3", "The study has changed since its runs were made"));
    const list = node("ul"); for (const line of q.changed.changed) list.append(node("li", line)); box.append(list);
    box.append(node("p", "Those runs are kept in runs/ but no longer count. Choose what to do; each shows its cost before anything starts.", "exp-note"));
    const over = node("button", "Start over"); over.type = "button"; over.title = "The changed study from round 1; earlier runs are kept and do not count (--restart)";
    over.addEventListener("click", () => askToRun("restart"));
    buttons.append(over);
    if (q.changed.same.length) {
      const keep = node("button", "Keep unchanged conditions' runs"); keep.type = "button";
      keep.title = `The runs of ${q.changed.same.join(", ")} still count; the changed conditions run again (--keep-unchanged)`;
      keep.addEventListener("click", () => askToRun("keep_unchanged"));
      buttons.append(keep);
    }
    buttons.append(cancel); box.append(buttons); return;
  }
  const how = {restart: " · starting over", keep_unchanged: " · keeping the unchanged conditions' runs"}[q.change] || "";
  box.append(node("h3", evaluating ? `Evaluate: ${plural(q.evaluations, "evaluation")}` : `Run ${plural(q.todo, "missing run")}${how}`));
  const facts = node("ul", undefined, "exp-facts");
  if (!evaluating) {
    if (q.done) facts.append(node("li", `${q.done} of ${q.done + q.todo} slots already have a completed run.`));
    if (q.redo) facts.append(node("li", `${plural(q.redo, "run")} redo a run that stopped or failed, reusing its turns.`));
  }
  const most = q.most_usd != null && q.most_usd > q.estimate_usd ? `; at most ≈ ${usd(q.most_usd - q.spent_usd)} if every reply ran to its max_tokens` : "";
  facts.append(node("li", q.estimate_usd == null ? "The estimate is unknown." :
    `Estimated ≈ ${usd(q.estimate_usd - q.spent_usd)}${most}.` + (q.spent_usd ? ` With the ${usd(q.spent_usd)} already spent, ≈ ${usd(q.estimate_usd)} in all.` : "")));
  if (q.ceiling) facts.append(node("li", `Ceiling: ${q.ceiling}` + (q.estimate_usd != null && q.limit_usd ? ` · the estimate is ${pct(q.estimate_usd / q.limit_usd)} of it. Runs stop before passing it.` : ".")));
  else if (q.free) facts.append(node("li", "No ceiling, and none is needed: every model it uses is free."));
  if (!evaluating && experimentData.estimate.evaluators.length) facts.append(node("li", "The runs are evaluated afterwards; the estimate includes it."));
  box.append(facts);
  for (const s of q.shared) box.append(node("p", `${s.model} is also used by ${s.experiment}, which is running: they share its rate limit, so both may slow down and their durations will run long.`, "exp-warning"));
  if (q.blockers.length) {
    box.append(node("p", "Nothing can start until this is fixed:", "exp-warning"));
    const list = node("ul", undefined, "exp-blockers");
    for (const b of q.blockers) {
      const item = node("li", b.message + (b.fix ? `. ${b.fix}` : ""));
      if (b.kind === "no_ceiling" || b.kind === "over") {
        const open = node("button", b.kind === "over" ? "Raise max_cost_usd in experiment.md" : "Add max_cost_usd to experiment.md", "exp-link");
        open.type = "button"; open.addEventListener("click", () => { const [file, line] = q.line.split(":"); fileView(); openFile(file, Number(line)); });
        item.append(" ", open);
      }
      if (b.kind === "check" && /key/i.test(b.message + b.fix)) {
        const keys = node("button", "Set up keys", "exp-link"); keys.type = "button";
        keys.addEventListener("click", () => byId("keys").click());
        item.append(" ", keys);
      }
      list.append(item);
    }
    box.append(list);
  }
  if (q.can_run) {
    const go = node("button", `${evaluating ? "Evaluate" : "Run"} ≈ ${usd(q.estimate_usd - q.spent_usd)}`, "primary"); go.type = "button";
    go.addEventListener("click", () => startReal(q, go));
    buttons.append(go);
  } else if (!q.blockers.length) box.append(node("p", evaluating ? "Every run that counts is evaluated by each evaluator's current version." : "Every slot has a completed run; nothing to do.", "exp-note"));
  buttons.append(cancel);
  box.append(buttons, node("code", q.command, "exp-command"));
}

async function startReal(q, button) {
  button.disabled = true;
  try {
    experimentLive = await api("/api/experiment-start", {path: q.path, rehearse: false, change: q.change, evaluating: q.evaluating, fingerprint: q.fingerprint});
    experimentJobs[q.path] = experimentLive;
    closeConfirm(); slotsShown = "real"; byId("experiment-detail").dataset.tab = "runs";
    renderExperiment(); pollExperiments();
  } catch (error) {
    if (error.result?.quote) renderConfirm(error.result.quote, error.message);
    else { expNotice(error.message); button.disabled = false; }
  }
}

async function stopExperiment() {
  if (!experimentLive) return;
  byId("stop-experiment").disabled = true;
  try { experimentLive = {...experimentLive, ...await api("/api/experiment-stop", {path: experimentLive.path})}; showJobControls(); }
  catch (error) { expNotice(error.message); byId("stop-experiment").disabled = false; }
}

async function pollExperiments() {
  clearTimeout(experimentPoll); experimentPoll = null;
  try {
    experimentJobs = (await api("/api/experiment-job")).jobs;
    const active = Object.values(experimentJobs).some(job => !job.done);
    byId("experiments-dot").hidden = !active;
    if (experimentPath && experimentJobs[experimentPath]) {
      const job = await api("/api/experiment-job" + pathQuery(experimentPath));
      if (job.path !== experimentPath) return;
      const seen = `${job.path} ${job.finished} ${job.done} ${job.running.length}`;
      experimentLive = job;
      if (seen !== experimentSeen) { experimentSeen = seen; await refreshExperiment(); }
      else if (!byId("experiment-detail").hidden) { showJobControls(); updateLiveLines(); }
    } else if (!experimentPath && !byId("experiments-workspace").hidden) {
      const seen = JSON.stringify(Object.entries(experimentJobs).map(([path, job]) => [path, job.done, job.finished]));
      if (seen !== experimentSeen) { experimentSeen = seen; await loadExperiments(); }  // the list's counts and running marks
    }
    if (active) experimentPoll = setTimeout(pollExperiments, 1000);
  } catch (error) { experimentPoll = setTimeout(pollExperiments, 3000); }
}

function updateLiveLines() {
  const pre = byId("experiment-lines");
  if (!pre || !experimentLive) return;
  const atEnd = pre.scrollTop + pre.clientHeight >= pre.scrollHeight - 4;
  pre.textContent = experimentLive.lines.join("\n");
  if (atEnd) pre.scrollTop = pre.scrollHeight;
}

function renderExperiment() {
  const data = experimentData;
  byId("experiment-title").textContent = data.name;
  byId("experiment-question").replaceChildren(data.question ? markdown(data.question, null) : node("p", "No notes below the settings in experiment.md."));
  byId("experiment-command").textContent = data.commands?.plan || `teamrunner experiment ${data.path} --plan`;
  byId("experiments-command").textContent = data.commands?.summary || "";
  const tabs = byId("experiment-tabs"), meta = byId("experiment-meta");
  showJobControls();
  if (!data.ready) {
    meta.textContent = `${data.file} · not ready to run`; tabs.hidden = true;
    byId("experiment-body").replaceChildren(renderProblems(data)); return;
  }
  tabs.hidden = false;
  const factors = Object.entries(data.factors);
  const shape = factors.length ? ` (${factors.map(([f, levels]) => `${f}: ${levels.length}`).join(" × ")})` : "";
  const ceiling = data.ceiling.max_cost_usd == null ? "no ceiling" : `ceiling ${data.ceiling.words}`;
  meta.textContent = `${plural(data.conditions.length, "condition")}${shape} × ${plural(data.replications, "replication")} = ${plural(data.runs, "run")} · seed ${data.seed} · ${ceiling}`;
  const counted = data.summary.completed, done = data.slots.filter(s => s.run).length;
  showExperimentTab(byId("experiment-detail").dataset.tab || (done || data.rehearsals ? "runs" : "plan"));
  byId("exp-runs-tab").textContent = `Runs · ${counted} of ${data.runs}`;
}

function showExperimentTab(name) {
  byId("experiment-detail").dataset.tab = name;
  for (const tab of ["plan", "runs", "summary"]) byId(`exp-${tab}-tab`).setAttribute("aria-pressed", String(tab === name));
  const body = byId("experiment-body");
  body.replaceChildren(...({plan: renderPlan, runs: renderSlots, summary: renderSummary}[name])(experimentData));
}

function renderProblems(data) {
  const box = node("section", undefined, "exp-section");
  box.append(node("h3", "To fix before it can run"));
  const list = node("ul", undefined, "exp-problems");
  for (const item of data.later) list.append(problemItem(item.where, `Needs a later TeamRunner: ${item.what}`));
  for (const p of data.problems) list.append(problemItem(p.where, p.message, p.fix, p.level));
  box.append(list, node("p", "Edit the files in the Files tab, then open this experiment again.", "exp-note"));
  return box;
}
function problemItem(where, message, fix, level = "error") {
  const item = node("li"), button = node("button", undefined, `problem ${level}`); button.type = "button";
  button.append(node("strong", where), node("span", ` ${message}`));
  if (fix) button.append(node("div", fix, "fix"));
  const [file, line] = where.split(":");
  button.addEventListener("click", () => { fileView(); openFile(file, line ? Number(line) : undefined); });
  item.append(button); return item;
}

function renderPlan(data) {
  const parts = [];
  const [conditions, condRows] = tableOf(["Condition", "Team folder", "How it is made", "Levels"]);
  for (const c of data.conditions) {
    const how = c.generated ? `built from ${c.base}, ${plural(c.changes, "change")}` : "as written (the folder is not changed)";
    condRows.append(cells(node("tr"), [c.name, c.folder, how, Object.entries(c.levels).map(([f, l]) => `${f}=${l}`).join(", ") || "—"]));
  }
  parts.push(section("Conditions", conditions));

  const diffs = node("div");
  for (const d of data.differences) {
    diffs.append(node("h4", `${d.from} → ${d.to}`));
    if (!d.files.length) { diffs.append(node("p", "No differences: the same team.", "exp-note")); continue; }
    const [table, rows] = tableOf(["File", "", "What"], "exp-table exp-diff");
    for (const f of d.files) { const kind = node("span", f.kind, `diff-kind ${f.kind}`); rows.append(cells(node("tr"), [f.file, kind, f.detail || ""])); }
    diffs.append(table);
  }
  if (data.differences.length) parts.push(section("Differences, file by file", diffs));

  const rounds = new Map();
  for (const slot of data.order) { if (!rounds.has(slot.round)) rounds.set(slot.round, []); rounds.get(slot.round).push(slot.condition); }
  const order = node("ul", undefined, "exp-rounds");
  for (const [round, names] of rounds) order.append(node("li", `Round ${round}: ${names.join(" · ")}`));
  parts.push(section(`Order of runs`, node("p", `Permuted rounds from seed ${data.seed}: each round holds one run of every condition${data.max_concurrent ? `, up to ${data.max_concurrent} at once` : ""}.`, "exp-note"), order));

  const e = data.estimate, est = node("div"), limit = data.ceiling.max_cost_usd;
  const share = limit && e.total_usd != null ? e.total_usd / limit : null;
  est.append(node("p", `Estimated cost ≈ ${usd(e.total_usd)}` + (share == null ? "" : ` · ${pct(share)} of the ceiling: ${data.ceiling.words}`), "exp-estimate"));
  if (limit == null && e.total_usd > 0) est.append(node("p", "No max_cost_usd: nothing caps what this spends. To run it from this page, add max_cost_usd to experiment.md.", "exp-warning"));
  if (share > 1) est.append(node("p", "Over the ceiling: a real run would not start. To allow more, raise max_cost_usd in experiment.md.", "exp-warning"));
  else if (share > 0.9) est.append(node("p", "Close to the ceiling: the experiment may stop incomplete.", "exp-warning"));
  else if (share > 0.6) est.append(node("p", "The ceiling is hard: runs stop before passing it.", "exp-note"));
  const [table, rows] = tableOf(["", "A run ≈", "Runs", "Calls", "Tokens in", "Tokens out"]);
  for (const c of e.per_condition) rows.append(cells(node("tr"), [c.condition, usd(c.per_run_usd), data.replications, Math.round(c.calls), c.tokens_in.toLocaleString(), c.tokens_out.toLocaleString()]));
  if (e.evaluators.length) rows.append(cells(node("tr"), ["evaluating", usd(e.evaluating_usd), `${e.evaluations} evaluations`, e.evaluators.join(", "), "", ""]));
  est.append(table, node("p", `At most ≈ ${usd(e.most_usd)} if every reply ran to its max_tokens. From rehearsal walks of each flow, at list prices; made again from real costs after round 1.`, "exp-note"));
  for (const line of e.no_max_tokens) est.append(node("p", line, "exp-note"));
  if (e.unpriced.length) est.append(node("p", `No list price for ${e.unpriced.join(", ")}; add it to ${e.prices}.`, "exp-warning"));
  parts.push(section("Estimate", est));
  parts.push(node("p", "Nothing was run and nothing was spent.", "exp-note"));
  return parts;
}

const SLOT_MARK = {completed: "✓", stopped: "■", failed: "✗"};
function renderSlots(data) {
  const parts = [];
  if (data.changed) {
    const box = section("The study has changed since its runs were made");
    box.append(node("p", "No run counts until teamrunner experiment is told what to do (--restart or --keep-unchanged).", "exp-warning"));
    const list = node("ul"); for (const line of data.changed.changed) list.append(node("li", line)); box.append(list);
    parts.push(box);
  }
  const toggle = node("div", undefined, "exp-toggle");
  for (const [which, label] of [["real", "Real runs"], ["rehearsal", `Rehearsals · ${data.rehearsals}`]]) {
    const button = node("button", label); button.type = "button"; button.setAttribute("aria-pressed", String(slotsShown === which));
    button.addEventListener("click", () => { slotsShown = which; showExperimentTab("runs"); });
    toggle.append(button);
  }
  parts.push(toggle);
  const reps = data.replications, byCondition = new Map(data.conditions.map(c => [c.name, []]));
  for (const slot of data.slots) byCondition.get(slot.condition)?.push(slot);
  const head = ["Condition"]; for (let r = 1; r <= reps; r++) head.push(String(r));
  const [grid, rows] = tableOf(head, "exp-table exp-grid");
  for (const [name, slots] of byCondition) {
    const row = node("tr"); row.append(node("th", name));
    slots.sort((a, b) => a.replicate - b.replicate);
    for (const slot of slots) {
      const run = slotsShown === "real" ? slot.run : slot.rehearsal, cell = node("td");
      const live = experimentLive && !experimentLive.done && (experimentLive.rehearse === (slotsShown === "rehearsal"))
        && experimentLive.running.find(r => r.condition === slot.condition && r.replicate === slot.replicate);
      if (live) {
        const mark = node("button", "●", "slot running"); mark.type = "button";
        mark.title = `Replicate ${slot.replicate}, round ${slot.round}: running now`; mark.setAttribute("aria-label", mark.title);
        mark.addEventListener("click", () => openRun(live.folder, experimentPath));
        cell.append(mark);
      } else if (!run) { cell.append(node("span", "·", "slot empty")); cell.title = `Replicate ${slot.replicate}, round ${slot.round}: not run yet`; }
      else {
        const kind = run.fills ? "completed" : run.status, mark = node("button", SLOT_MARK[kind] || "?", `slot ${kind}`); mark.type = "button";
        const why = run.status === "stopped" ? ` by ${run.stopped_by || "?"}` : "";
        const dear = run.typical_usd ? ` · over ${data.summary.costly_times}× this condition's typical run (${usd(run.typical_usd)})` : "";
        if (dear) mark.classList.add("costly");
        mark.title = `Replicate ${slot.replicate}, round ${slot.round}: ${run.status}${why} · ${usd(run.cost_usd)}${dear} · ${run.duration_s.toFixed(1)}s${run.fills ? "" : " · will be redone"}`;
        mark.setAttribute("aria-label", mark.title);
        mark.addEventListener("click", () => openRun(run.path, experimentPath));
        cell.append(mark);
      }
      row.append(cell);
    }
    rows.append(row);
  }
  const wrap = node("div", undefined, "exp-grid-wrap"); wrap.append(grid); parts.push(wrap);
  parts.push(node("p", "Columns are replicates. ✓ counts · ✗ failed and ■ stopped are redone in the same slot with the same seed · empty: not run yet. Choose a mark to read that run.", "exp-note"));
  if (slotsShown === "real" && data.summary.costly.length) parts.push(costlyNote(data.summary));
  if (slotsShown === "rehearsal") parts.push(node("p", "Rehearsals check the wiring with placeholder words and random answers. They never count.", "exp-note"));
  const not = Object.entries(data.summary.not_counted);
  if (slotsShown === "real" && not.length) parts.push(node("p", `Not counted: ${not.map(([why, n]) => `${n} ${why}`).join(", ")}.`, "exp-note"));
  if (experimentLive?.lines?.length) {
    const pre = node("pre", undefined, "exp-lines"); pre.id = "experiment-lines";
    pre.textContent = experimentLive.lines.join("\n");
    parts.push(section(experimentLive.done ? "What it printed" : "As it runs", pre));
    requestAnimationFrame(() => { pre.scrollTop = pre.scrollHeight; });
  }
  parts.push(node("p", `To run what is missing: ${data.commands.run}  ·  free rehearsal: ${data.commands.rehearse}`, "exp-command"));
  return parts;
}

function costlyNote(s) {
  // Flagged, never cut (a per-run ceiling would drop the expensive condition's long runs).
  const box = node("div", undefined, "exp-costly");
  box.append(node("p", `${plural(s.costly.length, "run")} cost over ${s.costly_times}× the typical run of ${s.costly.length === 1 ? "its" : "their"} condition. ${s.costly.length === 1 ? "It counts" : "They count"}; worth reading:`, "exp-warning"));
  const list = node("ul");
  for (const f of s.costly) {
    const item = node("li"), link = node("button", `${f.condition}, replicate ${f.replicate}`, "exp-link"); link.type = "button";
    link.addEventListener("click", () => openRun(f.path, experimentPath));
    item.append(link, ` ${usd(f.cost_usd)}, typical ${usd(f.typical_usd)}`);
    list.append(item);
  }
  box.append(list);
  return box;
}

function renderSummary(data) {
  const parts = [], s = data.summary;
  let measures = s.measures, title = `${s.completed} of ${data.runs} runs completed · ${usd(s.spent_runs_usd)} runs` + (s.spent_evaluating_usd ? ` + ${usd(s.spent_evaluating_usd)} evaluating` : "");
  if (!s.completed && data.rehearsal_summary) {
    measures = data.rehearsal_summary.measures;
    title = `No real runs yet. From ${plural(data.rehearsal_summary.completed, "rehearsal")}: placeholder values, not results`;
  }
  parts.push(node("p", title, s.completed ? "exp-estimate" : "exp-warning"));
  if (!measures.length) { parts.push(node("p", "Nothing to summarise until runs complete.", "exp-note")); return parts; }
  for (const m of measures) {
    const head = m.kind === "choice" ? ["", "n", "Share by answer, 95% interval"] : ["", "n", "Mean", "SD", "95% interval"];
    const [table, rows] = tableOf(head, "exp-table exp-summary");
    for (const row of m.rows) {
      if (!row.n) { rows.append(cells(node("tr"), m.kind === "choice" ? [row.condition, 0, ""] : [row.condition, 0, "", "", ""])); continue; }
      if (m.kind === "choice") {
        const shares = node("div", undefined, "exp-shares");
        for (const share of row.shares) {
          const bar = node("span", undefined, "exp-share"); bar.append(node("strong", share.answer), node("span", ` ${pct(share.share)} [${pct(share.ci[0])}, ${pct(share.ci[1])}]`));
          bar.style.setProperty("--share", pct(share.share)); shares.append(bar);
        }
        rows.append(cells(node("tr"), [row.condition, row.n, shares]));
      } else {
        const ci = row.ci ? `[${num(row.ci[0])}, ${num(row.ci[1])}]${row.wilson ? " (Wilson)" : ""}` : "";
        rows.append(cells(node("tr"), [row.condition, row.n, num(row.mean), row.wilson ? "" : num(row.sd), ci]));
      }
    }
    for (const row of m.marginals) {
      const ci = row.ci ? `[${num(row.ci[0])}, ${num(row.ci[1])}]` : "";
      rows.append(cells(node("tr", undefined, "marginal"), [`${row.factor} = ${row.level}`, row.n, num(row.mean), num(row.sd), ci]));
    }
    const box = section(`${m.label}${m.span ? " " + m.span : ""}`, table);
    if (m.marginals.length) box.append(node("p", "Shaded rows are marginal means: every run at that level, across the other factors.", "exp-note"));
    for (const [why, n] of Object.entries(m.left_out)) box.append(node("p", `Left out: ${plural(n, "run")} ${why}.`, "exp-note"));
    parts.push(box);
  }
  if (s.costly.length) parts.push(costlyNote(s));
  const not = Object.entries(s.not_counted);
  if (s.completed && not.length) parts.push(node("p", `Not counted: ${not.map(([why, n]) => `${n} ${why}`).join(", ")}.`, "exp-note"));
  parts.push(node("p", "Descriptive only: means and 95% intervals, no tests.", "exp-note"));
  return parts;
}

byId("experiments-view").addEventListener("click", experimentsView);
byId("refresh-experiments").addEventListener("click", () => experimentPath ? openExperiment(experimentPath) : loadExperiments());
byId("all-experiments").addEventListener("click", () => { delete byId("experiment-detail").dataset.tab; loadExperiments(); });
byId("edit-experiment").addEventListener("click", () => { if (experimentData) { fileView(); openFile(experimentData.file); } });
byId("back-experiment").addEventListener("click", () => { showView("experiments"); });
byId("rehearse-experiment").addEventListener("click", startRehearsal);
byId("run-experiment").addEventListener("click", () => askToRun());
byId("evaluate-experiment").addEventListener("click", () => askToRun(null, "only"));
byId("stop-experiment").addEventListener("click", stopExperiment);
pollExperiments();  // an experiment started before this page was opened (or reloaded) shows as running
for (const tab of ["plan", "runs", "summary"]) byId(`exp-${tab}-tab`).addEventListener("click", () => showExperimentTab(tab));
