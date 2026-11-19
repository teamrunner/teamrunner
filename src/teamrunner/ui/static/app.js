// SPDX-License-Identifier: GPL-3.0-or-later
// Copyright 2026 Kumar Muthuraman
"use strict";

const token = new URLSearchParams(location.search).get("token") || "";
const byId = id => document.getElementById(id);
byId("legal-notices").addEventListener("click", () => byId("legal-dialog").showModal());
let current = null, dirty = false, setting = false, validationTimer, validating = 0;
let roles = {}, problems = [], info = null, modalAction = null, saving = false;
let context = "", generation = 0;
// The experiment the open file belongs to (its folder holds experiment.md), else "".
// Then the bar is the experiment's: Validate checks it as --plan does, and Run… opens it.
let experimentContext = "";
// What the last check found. Saving and the Validate button make the same check,
// the one `teamrunner validate` makes: files, then keys and models (D140). Saving
// may use a provider's model list from the last few minutes; the button asks again.
// An edit makes the result stale: nothing on screen may claim a check of other text.
let fileCheck = null, onlineCheck = null, stale = false, checkedAt = "";
// Two things to work on: teams, or experiments. In a workspace each has its own folder,
// and the sidebar shows only that one; the other is a greyed line that switches to it.
let mode = "teams";
const treeRoot = () => info?.workspace ? mode : "";
const rootMissing = () => treeRoot() === "experiments" && !info?.experiments;
const otherMode = () => mode === "teams" ? "experiments" : "teams";
function setMode(next) {
  if (next === mode) return;
  mode = next;
  for (const name of ["teams", "experiments"]) byId(`${name}-mode`).setAttribute("aria-pressed", String(name === mode));
  for (const id of ["flow-view"]) byId(id).hidden = mode !== "teams";
  for (const id of ["plan-view", "summary-view"]) byId(id).hidden = mode !== "experiments";
  byId("runs-view").title = mode === "teams" ? "The selected team’s runs; open one to read it (teamrunner runs)" : "The selected experiment’s runs, by condition and replicate";
  byId("back-runs").hidden = mode !== "teams";  // a team's history; an experiment's runs are in its grid
  byId("files").listingSignature = null; byId("files").replaceChildren();
  showRoot(); updateState();
  if (info) loadTree();
}
function showRoot() {
  if (!info) return;
  // teamrunner/ holds teams/ and experiments/, always in that order: the one in use
  // folds open with its files, the other is a greyed line that switches to it.
  byId("folder-name").textContent = info.name + "/";
  byId("fold-root").hidden = !treeRoot();
  byId("mode-name").textContent = treeRoot() + "/";
  byId("tree-root").parentElement.classList.toggle("in-workspace", Boolean(treeRoot()));
  const other = byId("other-root");
  if (mode === "teams") byId("files").after(other); else byId("fold-root").before(other);
  other.hidden = !info.workspace;
  other.textContent = `${otherMode()}/`;
  other.title = `Open ${otherMode() === "teams" ? "Teams" : "Experiments"}`;
}
async function loadTree() {
  if (rootMissing()) {
    const empty = document.createElement("li"); empty.className = "empty";
    empty.textContent = "No experiments yet. Start with + Experiment.";
    byId("files").replaceChildren(empty); byId("files").listingSignature = null; return;
  }
  await loadFiles(treeRoot(), byId("files"));
}
const ICONS = {
  folder: '<path d="M1.5 4.5v8h11l2-5.5h-11l-2 5.5M1.5 4.5v-1.5h4l1.5 1.5h5v2"/>',
  closed: '<path d="M1.5 12.5v-9.5h4l1.5 1.5h7.5v8z"/>',
  file: '<path d="M3.5 1.5h6l3 3v10h-9zM9.5 1.5v3h3"/>'
};
function icon(name) {
  const svg = document.createElementNS("http://www.w3.org/2000/svg", "svg");
  svg.setAttribute("viewBox", "0 0 16 16"); svg.setAttribute("class", "ic"); svg.setAttribute("aria-hidden", "true");
  svg.innerHTML = ICONS[name]; return svg;
}

// When TeamRunner stops (Ctrl+C, or its terminal closed), nothing the page asks can work: it
// says so once, keeps the editor's text to copy, and stops asking (review 2026-11-16, U2).
const STOPPED = "TeamRunner has stopped running on your computer.";
let serverGone = false;
function lostServer() {
  if (serverGone) return;
  serverGone = true;
  byId("server-lost").hidden = false;
  for (const button of document.querySelectorAll("button")) if (!byId("server-lost").contains(button)) button.disabled = true;
  byId("copy-unsaved").hidden = !current;
}
async function api(route, data) {
  if (serverGone) throw new Error(STOPPED);
  let response;
  try {
    response = await fetch(route, {method: data === undefined ? "GET" : "POST",
      headers: {"X-TeamRunner-Token": token, ...(data === undefined ? {} : {"Content-Type": "application/json"})},
      ...(data === undefined ? {} : {body: JSON.stringify(data)})});
  } catch (error) { lostServer(); throw new Error(STOPPED); }
  const result = await response.json();
  if (!response.ok) throw Object.assign(new Error(result.error || "The action could not be completed."), {status: response.status, result});
  return result;
}
const notice = text => { byId("notice").textContent = text; };
const pathQuery = path => `?path=${encodeURIComponent(path)}`;
const parent = path => path.includes("/") ? path.slice(0, path.lastIndexOf("/")) : "";
const join = (folder, name) => folder ? `${folder}/${name}` : name;
const inRuns = path => path.split("/").some(part => part.toLowerCase() === "runs");
// The team every button acts on is the one chosen last: a team (or experiment) folder, or a
// file in one. The bar names the same team (review 2026-11-16, U1).
let chose = "file";
const teamContext = () => chose === "folder" ? (experimentContext || context) : current ? current.path : context;
const command = (action, path = context || ".") => {
  byId("command").textContent = experimentContext ? `teamrunner experiment ${JSON.stringify(experimentContext)} --plan`
    : `teamrunner ${action} ${JSON.stringify(path || ".")}`;
};

CodeMirror.defineMode("teamrunner-flow", () => ({token(stream) {
  if (stream.match(/^\s*#.*/)) return "comment";
  if (stream.match(/--[^>]*-->|->|→|=|\(\*\d+\)/)) return "operator";
  if (stream.match(/\b(done|stop)\b/)) return "keyword";
  stream.next(); return null;
}}));
const editor = CodeMirror.fromTextArea(byId("editor"), {
  lineNumbers: true, lineWrapping: true, indentUnit: 2,
  gutters: ["CodeMirror-linenumbers", "problems-gutter"],
  extraKeys: {"Cmd-S": () => save(), "Ctrl-S": () => save()},
  screenReaderLabel: "File contents"
});
editor.on("change", () => {
  if (!setting && current) {
    dirty = editor.getValue() !== current.text;
    if (dirty && !stale && fileCheck) { stale = true; onlineCheck = null; showChecks(); }
    updateState();
  }
});
function isMember(path) {
  const name = path.split("/").pop();
  return ["worker", "decider", "toolkeeper"].includes(roles[path]) ||
    (!(path in roles) && /\.md$/i.test(name) && !["chair.md", "manager.md", "readme.md"].includes(name.toLowerCase()));
}
function showChecks() {
  const mark = byId("validate-mark"), set = (state, text) => { mark.textContent = text; mark.dataset.state = state; };
  const errors = problems.filter(p => p.level === "error").length, warnings = problems.length - errors;
  const fresh = fileCheck && !stale, online = fresh && onlineCheck ? onlineCheck.problems.length : 0;
  let summary = "", said = "not checked since the last change";
  if (!fileCheck) set("", "");
  else if (stale) { set("", ""); summary = "Edited since the last check"; }
  else if (errors) { set("error", `✕ ${errors}`); summary = `${errors} error${errors === 1 ? "" : "s"} in the files`; said = summary; }
  else if (fileCheck.experiment) { set("ok", "✓"); summary = `Experiment valid · ${fileCheck.runs} run${fileCheck.runs === 1 ? "" : "s"} planned`; said = "experiment valid"; }
  else if (!onlineCheck) { set("", ""); summary = "Press Validate to check files, keys and models"; }
  else if (online) { set("error", `✕ ${online}`); summary = `Files valid · ${online} problem${online === 1 ? "" : "s"} with keys or models`; said = summary; }
  else { set("ok", "✓"); summary = "Files, keys and models valid"; said = "files, keys and models valid"; }
  // The time shows that a second press did check again, even when nothing changed.
  if (fresh && onlineCheck && !errors && checkedAt) summary += ` · checked ${checkedAt}`;
  if (fileCheck && !stale && !errors && warnings) summary += `${summary ? " · " : ""}${warnings} warning${warnings === 1 ? "" : "s"}`;
  byId("validate").setAttribute("aria-label", `Validate, ${said}`);
  byId("validation-summary").textContent = summary;
  if (!fileCheck) { problems = []; byId("problems").replaceChildren(); markers(); }
  byId("validation").hidden = !byId("problems").children.length;
  byId("problems-title").textContent = stale ? "To fix (found before your latest edits)" : "To fix";
}
// The chair is not a member, so it has its own button: one chair.md per team, in the team's own folder.
const hasTeam = () => Boolean(context || info?.team);
function chairState() {
  const record = Boolean(current?.readonly) || inRuns(context);
  const exists = [...byId("files").querySelectorAll("button.file")].some(button => button.dataset.path === join(context, "chair.md"));
  byId("new-chair").disabled = record || !hasTeam() || exists;
  byId("new-chair").title = current?.generated ? "Built from experiment.md: read-only. Edit experiment.md or its variants/ instead."
    : record ? "Run records are read-only. Select a team’s file to add a chair."
    : !hasTeam() ? "Select a team to add its chair"
    : exists ? "This team already has a chair.md" : "Add chair.md, the team’s flow, to the selected team";
}
function updateState() {
  byId("save").disabled = !current || current.readonly || !dirty || saving;
  byId("rename").disabled = !current || current.readonly;
  byId("trash").disabled = !current || current.readonly;
  // Run records are read-only: nothing is created beside them, and a team is not trashed from inside them.
  const record = Boolean(current?.readonly);
  byId("new-team").disabled = byId("new-member").disabled = record;
  chairState();
  for (const id of ["new-team", "new-member", "new-chair"]) byId(id).hidden = mode === "experiments";
  for (const id of ["new-experiment", "new-variant", "new-metric", "new-task"]) byId(id).hidden = mode !== "experiments";
  const exp = chosenExperiment();
  for (const [id, what] of [["new-variant", "a copy of one of the team’s files, in variants/, to change for a condition"],
    ["new-metric", "a measure in metrics/: an answer key, an evaluator, or a member’s typed answer"],
    ["new-task", "the same inputs for every condition, in tasks/"]]) {
    byId(id).disabled = !exp;
    byId(id).title = exp ? `Add to ${exp.split("/").pop()}: ${what}` : "Choose an experiment in the sidebar first";
  }
  byId("new-experiment").disabled = !info?.teams?.length;
  byId("new-experiment").title = info?.teams?.length ? "Start a new experiment on one of your teams" : "Make a team first, in Teams: an experiment runs versions of a team";
  const built = current?.generated ? "Built from experiment.md: read-only. Edit experiment.md or its variants/ instead." : "";
  byId("new-team").title = built || (record ? "Run records are read-only. Select a team’s file to create a team." : "Create a new team folder");
  byId("new-member").title = built || (record ? "Run records are read-only. Select a team’s file to add a member." : "Add a member to the selected team");
  byId("trash-team").disabled = !context || inRuns(context) || Boolean(current?.generated);
  byId("trash-team").title = context ? `Move the team ${context} to the trash` : info?.team ? "This team is the folder you opened; it can’t be moved to the trash from here." : "Select a team to move it to the trash";
  byId("save-state").textContent = current ? (current.generated ? "Built from experiment.md · read-only" : current.readonly ? "Read-only run record" : dirty ? "● Unsaved" : "Saved") : "";
  byId("breadcrumb").textContent = current ? current.path : "";
  const docs = current && docsPage(current.path);
  byId("file-docs").hidden = !docs;
  if (docs) { byId("file-docs").href = `${DOCS}${docs[0]}.html`; byId("file-docs").title = `How ${docs[1]} work: a first example, then every setting (teamrunner.ai)`; }
  // "As it will run" and the flow diagram are a team's views; an experiment has its own tab.
  byId("show-tab").hidden = !current || current.readonly || !isMember(current.path) || Boolean(experimentContext);
  if (experimentContext && byId("edit-tab").getAttribute("aria-pressed") !== "true") showEdit();
  byId("team-label").textContent = experimentContext ? "EXPERIMENT" : "TEAM";
  byId("team-name").textContent = experimentContext ? (fileCheck?.experiment === experimentContext && fileCheck.name || experimentContext.split("/").pop())
    : context ? context.split("/").pop() : info?.team ? info.name : "—";
  byId("run-team").textContent = experimentContext ? "Open in Experiments" : "Run…";
  byId("run-team").title = experimentContext ? "This experiment’s plan, runs and summary" : "";
  byId("validate").title = experimentContext
    ? "Check this experiment as teamrunner experiment --plan does: its settings, every condition’s team, each change and each metric. Saving does this too. Free; needs no keys."
    : "Check this team’s files, then its keys and models (teamrunner validate). Saving does this too; use the button to check again without an edit. Free; asks each provider which models it serves.";
}
// Each kind of file has its page on teamrunner.ai: a first example, then every detail.
const DOCS = "https://teamrunner.ai/docs/";
function docsPage(path) {
  const parts = path.split("/"), name = parts[parts.length - 1].toLowerCase(), folder = (parts[parts.length - 2] || "").toLowerCase();
  if (inRuns(path) || current?.generated) return null;  // a record or a built condition: read-only, not something to write
  if (name === "team.yaml") return ["team_yaml", "team.yaml"];
  if (name === "experiment.md") return ["experiment", "experiment.md"];
  if (name === "chair.md" || name === "manager.md") return ["chair", "chair.md"];
  if (folder === "variants") return ["variant", "variants"];
  if (folder === "metrics") return /\.ya?ml$/.test(name) ? ["answer_key", "answer keys"] : name.endsWith(".md") ? ["evaluator", "evaluators"] : null;
  if (folder === "tasks" && /\.ya?ml$/.test(name)) return ["task", "tasks"];
  if (folder === "models" && /\.ya?ml$/.test(name)) return ["model_profile", "model profiles"];
  if (roles[path] === "toolkeeper") return ["toolkeeper", "toolkeepers"];
  if (isMember(path)) return ["member", "members"];
  return null;
}
function mayLeave() { return !dirty || window.confirm("Discard the unsaved changes in this file?"); }
window.addEventListener("beforeunload", event => { if (dirty) { event.preventDefault(); event.returnValue = ""; } });
window.addEventListener("keydown", event => {
  if ((event.metaKey || event.ctrlKey) && event.key.toLowerCase() === "s") { event.preventDefault(); save(); }
});

function markers() {
  editor.clearGutter("problems-gutter");
  for (const p of problems.filter(p => current && p.file === current.path && p.line)) {
    const mark = document.createElement("button"); mark.type = "button";
    mark.textContent = p.level === "error" ? "●" : "▲"; mark.className = `gutter-${p.level}`;
    mark.title = p.message + (p.fix ? `\n${p.fix}` : "");
    mark.setAttribute("aria-label", `${p.level} on line ${p.line}: ${p.message}`);
    mark.addEventListener("click", () => notice(`${p.message}${p.fix ? " — " + p.fix : ""}`));
    editor.setGutterMarker(p.line - 1, "problems-gutter", mark);
  }
}
let opening = 0;
async function openFile(path, line, again = false) {
  if (!again && current && current.path === path) { chose = "file"; document.dispatchEvent(new Event("teamrunner:file-open")); if (line) jump(line); return; }
  if (!again && !mayLeave()) return;
  const request = ++opening;  // two quick clicks: the file clicked last wins (U6)
  try {
    const file = await api("/api/file" + pathQuery(path));
    if (request !== opening) return;
    if (info?.workspace) { const top = path.split("/")[0]; if (top === "teams" || top === "experiments") setMode(top); }
    document.dispatchEvent(new Event("teamrunner:file-open"));
    current = file; dirty = false; generation++; chose = "file";
    byId("editing").hidden = false; document.querySelector(".welcome").hidden = true;
    byId("disk-warning").hidden = true;
    setting = true; editor.setValue(file.text); editor.clearHistory(); setting = false;
    editor.setOption("readOnly", file.readonly);
    const yaml = /\.ya?ml$/i.test(path);
    editor.setOption("mode", yaml ? "yaml" : path.endsWith("chair.md") ?
      {name: "yaml-frontmatter", base: "teamrunner-flow"} : {name: "yaml-frontmatter", base: "markdown"});
    showEdit(); updateState(); markers(); highlightSelection(); editor.focus();
    notice(file.generated ? `This is condition ${file.generated.condition}, built by TeamRunner from ${file.generated.experiment}/experiment.md and built again before every run, so it is read-only. To change it, edit experiment.md or its variants/.` : "");
    if (line) jump(line);
    if (!file.readonly) await validate(false);
  } catch (error) { notice(error.message); }
}
function jump(line) { showEdit(); editor.setCursor(Math.max(0, line - 1), 0); editor.scrollIntoView({line: line - 1, ch: 0}, 70); editor.focus(); }
function showEdit() {
  editor.getWrapperElement().hidden = false; byId("resolved").hidden = true;
  for (const id of ["edit-tab", "show-tab"]) byId(id).setAttribute("aria-pressed", String(id === "edit-tab"));
  editor.refresh();
}
async function inspect() {
  if (!current) return;
  if (dirty) { notice("Save this file first to see the version on disk."); return; }
  const id = ++generation;
  try {
    const result = await api("/api/show" + pathQuery(current.path));
    if (id !== generation) return;
    editor.getWrapperElement().hidden = true; byId("resolved").hidden = false;
    byId("resolved").textContent = result.text;
    for (const name of ["edit-tab", "show-tab"]) byId(name).setAttribute("aria-pressed", String(name === "show-tab"));
    command("show", current.path);
  } catch (error) { notice(error.message); }
}
// The Flow diagram view: the selected team's flow, whatever file is open (a team's view, not a file's).
let flowRequest = 0;
async function showFlow() {
  const team = context || (info?.team ? "." : ""), request = ++flowRequest;
  byId("flow-team").textContent = team ? (team === "." ? info.name : team.split("/").pop()) : "—";
  byId("flow-command").textContent = `teamrunner explain ${JSON.stringify(team || "<team>")} --diagram`;
  if (!team) { byId("flow-diagram").replaceChildren(); byId("flow-text").hidden = true; byId("flow-notice").textContent = "Choose a team in the sidebar to see its flow."; return; }
  byId("flow-notice").textContent = "Drawing the flow…";
  try {
    const result = await api("/api/diagram" + pathQuery(team === "." ? "team.yaml" : `${team}/team.yaml`));
    if (request !== flowRequest) return;
    byId("flow-text").textContent = result.text; byId("flow-text").hidden = !result.text;
    byId("flow-diagram").replaceChildren();
    if (result.mermaid) drawDiagram(byId("flow-diagram"), result.mermaid);
    byId("flow-notice").textContent = dirty ? "Drawn from the saved files; save to see your edits." : "";
  } catch (error) { if (request === flowRequest) byId("flow-notice").textContent = error.message; }
}
const flowShown = () => !byId("flow-workspace").hidden;
async function save() {
  if (!current || current.readonly || !dirty || saving) return;
  saving = true; updateState();
  const file = current, text = editor.getValue();
  try {
    const result = await api("/api/save", {path: file.path, revision: file.revision, text});
    if (current === file) {
      current = result; dirty = editor.getValue() !== result.text; byId("disk-warning").hidden = true;
      notice(""); onlineCheck = null; updateState();
      if (flowShown()) await showFlow();
      clearTimeout(validationTimer); validationTimer = setTimeout(() => validate(true, null, true), 350);
    }
  } catch (error) { notice(error.message); }
  finally { saving = false; updateState(); }
}
async function validate(full = false, pathOverride = null, remembered = false) {
  const id = ++validating, path = pathOverride ?? teamContext();
  if (full) byId("validation-summary").textContent = "Checking files, keys and models…";
  byId("validate").disabled = true;
  const checked = editor.getValue();
  try {
    const result = await api("/api/validate", {path, offline: !full, remembered});
    if (id !== validating) return;
    const edited = dirty || editor.getValue() !== checked;  // typed while the check ran
    experimentContext = result.experiment || "";
    context = result.experiment ? "" : result.team === "." ? "" : result.team;
    if (current && result.ids_added.includes(current.path)) {
      if (dirty) { if (!await takeIdLine(current)) byId("disk-warning").hidden = false; }
      else {
        const path = current.path, updated = await api("/api/file" + pathQuery(path));
        if (current && current.path === path && !dirty) {
          current = updated; const cursor = editor.getCursor();
          setting = true; editor.setValue(updated.text); setting = false; editor.setCursor(cursor); updateState();
        }
      }
    }
    roles = {...roles, ...result.roles}; problems = result.problems;
    const errors = problems.filter(p => p.level === "error").length;
    // A check of the saved files says nothing about edits not yet saved. A files-only
    // check keeps an earlier full check of the same team: nothing has changed since.
    const sameTeam = fileCheck && fileCheck.team === result.team;
    fileCheck = result; stale = edited || dirty;
    onlineCheck = stale ? null : result.online ? result.online : full || !sameTeam ? null : onlineCheck;
    if (result.online) checkedAt = new Date().toLocaleTimeString();
    const list = byId("problems"); list.replaceChildren();
    for (const p of problems) {
      const item = document.createElement("li"), button = document.createElement("button");
      button.type = "button"; button.className = `problem ${p.level}`;
      const where = document.createElement("strong"); where.textContent = `${p.file}${p.line ? ":" + p.line : ""} · ${p.level}`;
      const message = document.createElement("span"); message.textContent = p.message;
      button.append(where, message);
      if (p.fix) { const fix = document.createElement("span"); fix.className = "fix"; fix.textContent = p.fix; button.append(fix); }
      button.addEventListener("click", () => openFile(p.file, p.line)); item.append(button); list.append(item);
    }
    if (onlineCheck) {
      for (const p of onlineCheck.problems) {
        const item = document.createElement("li"); item.className = "online-problem";
        item.textContent = `${p.what} · ${p.who.join(", ")}: ${p.wrong} — ${p.fix}`; list.append(item);
      }
      for (const warning of onlineCheck.warnings) { const item = document.createElement("li"); item.textContent = warning; list.append(item); }
    }
    markers(); updateRoles(); updateState(); showChecks(); command(full ? "validate" : "validate --offline");
    notice(dirty ? "Checked the saved files only. Save to check your edits." : "");
    return result;
  } catch (error) {
    if (id === validating) { fileCheck = onlineCheck = null; stale = false; showChecks(); notice(error.message); }
  }
  finally { byId("validate").disabled = false; }
}
// TeamRunner added an `id:` line to the file while it was being edited. That one line is the
// only change on disk, so it goes into the text being edited too, and the edits are kept and
// can be saved (review 2026-11-16, U6). False if the change on disk is anything else.
async function takeIdLine(file) {
  const updated = await api("/api/file" + pathQuery(file.path));
  if (current !== file) return true;
  const before = file.text.split("\n"), after = updated.text.split("\n");
  if (after.length !== before.length + 1) return false;
  let at = 0; while (at < before.length && before[at] === after[at]) at++;
  if (!/^id:\s/.test(after[at]) || after.slice(at + 1).join("\n") !== before.slice(at).join("\n")) return false;
  const lines = editor.getValue().split("\n"), anchor = at ? before[at - 1] : null;
  const place = anchor === null ? 0 : lines.indexOf(anchor) + 1;
  if (anchor !== null && place === 0) return false;
  const cursor = editor.getCursor();
  setting = true; editor.replaceRange(after[at] + "\n", {line: place, ch: 0}); setting = false;
  if (cursor.line >= place) editor.setCursor({line: cursor.line + 1, ch: cursor.ch});
  current = updated; dirty = editor.getValue() !== current.text; updateState();
  notice("TeamRunner added this file’s id line while you were editing; it is in your text now.");
  return true;
}
function highlightSelection() {
  document.querySelectorAll(".tree .file").forEach(button => {
    const selected = current && button.dataset.path === current.path;
    button.classList.toggle("selected", Boolean(selected));
    if (selected) button.setAttribute("aria-current", "true"); else button.removeAttribute("aria-current");
  });
}
// The four kinds of file a team is made of. Until a team has been checked, a
// Markdown file beside team.yaml is taken to be an agent; validation corrects it.
const KINDS = {worker: "agent", decider: "agent", toolkeeper: "toolkeeper", chair: "chair"};
function kindOf(button) {
  const path = button.dataset.path, name = path.split("/").pop().toLowerCase();
  if (name === "team.yaml") return "team";
  if (roles[path]) return KINDS[roles[path]];
  if (button.dataset.inTeam !== "true" || !name.endsWith(".md") || name === "readme.md") return "";
  return ["chair.md", "manager.md"].includes(name) ? "chair" : "agent";
}
function updateRoles() {
  document.querySelectorAll(".tree .file").forEach(button => {
    button.querySelector(".role")?.remove();
    const kind = kindOf(button);
    if (kind) {
      const badge = document.createElement("span"); badge.className = `badge role kind-${kind}`; badge.textContent = kind;
      if (roles[button.dataset.path] === "decider") badge.title = "This agent makes a decision the flow branches on.";
      button.append(badge);
    }
    button.dataset.kind = kind;
  });
}
async function loadFiles(path, list) {
  const result = await api("/api/files" + pathQuery(path));
  if (!list.isConnected) return;
  const signature = JSON.stringify(result.entries);
  if (list.listingSignature === signature) return;
  const expanded = new Set([...list.querySelectorAll('button[aria-expanded="true"]')].map(button => button.dataset.path));
  list.replaceChildren();
  const inTeam = result.entries.some(entry => entry.name === "team.yaml");
  // A team's runs are read in Runs, an experiment's in its Runs; their raw folders stay out of the way.
  for (const entry of result.entries.filter(e => !(e.kind === "folder" && e.name.toLowerCase() === "runs"))) {
    const item = document.createElement("li"), button = document.createElement("button");
    const folder = entry.kind === "folder"; button.type = "button"; button.className = folder ? "folder" : "file";
    button.dataset.path = entry.path;
    button.dataset.inTeam = String(inTeam);
    const caret = document.createElement("span"); caret.className = "caret"; caret.setAttribute("aria-hidden", "true"); caret.textContent = folder ? "▸" : "";
    let glyph = icon(folder ? "closed" : "file");
    const name = document.createElement("span"); name.className = "file-name"; name.textContent = entry.name + (folder ? "/" : ""); button.append(caret, glyph, name);
    if (entry.team) button.classList.add("team-folder");
    if (inRuns(entry.path)) button.classList.add("record");
    if (entry.team || entry.experiment || entry.name === "runs") {
      const badge = document.createElement("span"); badge.className = "badge" + (entry.team ? " kind-team" : entry.experiment ? " kind-experiment" : "");
      badge.textContent = entry.team ? "team" : entry.experiment ? "experiment" : "records"; button.append(badge);
    }
    const open = shown => { caret.textContent = shown ? "▾" : "▸"; const next = icon(shown ? "folder" : "closed"); glyph.replaceWith(next); glyph = next; };
    item.append(button); list.append(item);
    if (folder) {
      const nested = document.createElement("ul"); nested.hidden = true; button.setAttribute("aria-expanded", "false");
      button.addEventListener("click", async () => {
        button.disabled = true;
        try {
          if (nested.hidden) await loadFiles(entry.path, nested);
          nested.hidden = !nested.hidden; button.setAttribute("aria-expanded", String(!nested.hidden)); open(!nested.hidden);
          if (entry.experiment) { chose = "folder"; experimentContext = entry.path; context = ""; updateState(); document.dispatchEvent(new CustomEvent("teamrunner:experiment-select", {detail:entry.path})); validate(false, entry.path); }
          if (entry.team) { chose = "folder"; context = entry.path; experimentContext = ""; updateState(); document.dispatchEvent(new CustomEvent("teamrunner:team-select", {detail:entry.path})); validate(false, entry.path); }
          updateRoles(); highlightSelection();
        } catch (error) { notice(error.message); } finally { button.disabled = false; }
      }); item.append(nested);
      if (expanded.has(entry.path)) {
        nested.hidden = false; button.setAttribute("aria-expanded", "true"); open(true);
        await loadFiles(entry.path, nested);
      }
    } else { button.addEventListener("click", () => openFile(entry.path)); }
  }
  list.listingSignature = signature; chairState();
  if (!result.entries.length) { const empty = document.createElement("li"); empty.className = "empty"; empty.textContent = inRuns(path) ? "Nothing recorded here." : path !== treeRoot() ? "This folder is empty." : mode === "experiments" ? "No experiments yet. Start with + Experiment." : "No teams yet. Start with + Team."; list.append(empty); }
  updateRoles(); highlightSelection();
}
async function refresh() {
  byId("refresh").disabled = true;
  try {
    info = await api("/api/info"); document.title = `${info.name} · TeamRunner`;
    showRoot(); byId("folder-path").textContent = info.folder; byId("tree-root").title = info.folder; updateState();
    if (!info.team) { byId("welcome-title").textContent = "A place for your teams."; byId("welcome-text").textContent = "Choose + Team to start empty, copy a shipped example, or describe the team and have a model write it. Your files stay in this folder."; }
    await loadTree(); byId("tree-status").textContent = "";
    if (!current && info.team) await openFile("team.yaml");
  } catch (error) { notice(error.message); } finally { byId("refresh").disabled = false; }
}
let treeRefreshing = false, pollingDisk = false;
async function refreshTree() {
  if (treeRefreshing || document.hidden) return;
  treeRefreshing = true;
  const focusedElement = document.activeElement;
  const focused = byId("files").contains(focusedElement) ? focusedElement.dataset.path : null;
  async function branch(path, list) {
    await loadFiles(path, list);
    if (path.split("/").some(part => part.toLowerCase() === "runs")) return;
    for (const item of list.children) {
      const button = item.firstElementChild, nested = button?.nextElementSibling;
      if (nested && !nested.hidden) await branch(button.dataset.path, nested);
    }
  }
  try {
    if (rootMissing()) return;
    await branch(treeRoot(), byId("files"));
    if (focused && (document.activeElement === focusedElement || document.activeElement === document.body)) [...byId("files").querySelectorAll("button")].find(button => button.dataset.path === focused)?.focus({preventScroll:true});
  } catch (error) { byId("tree-status").textContent = error.message; }
  finally { treeRefreshing = false; }
}
async function pollDisk() {
  if (saving || document.hidden || pollingDisk || serverGone) return;
  refreshTree();
  if (!current) return;
  pollingDisk = true;
  const file = current;
  try {
    // Only the fingerprint, every few seconds; the text only when it has changed (U8).
    const seen = await api("/api/file" + pathQuery(file.path) + "&only=revision");
    const disk = current === file && seen.revision !== file.revision ? await api("/api/file" + pathQuery(file.path)) : null;
    if (disk && current === file && disk.revision !== file.revision) {
      if (dirty) byId("disk-warning").hidden = false;
      else {
        const cursor = editor.getCursor();
        current = disk; generation++; onlineCheck = null;
        setting = true; editor.setValue(disk.text); editor.clearHistory(); setting = false;
        editor.setCursor(cursor); updateState(); byId("disk-warning").hidden = true;
        notice("Reloaded changes from disk.");
        if (flowShown()) await showFlow();
        if (byId("show-tab").getAttribute("aria-pressed") === "true") await inspect();
      }
    }
  } catch (error) { if (current === file && !serverGone) { byId("disk-warning").hidden = false; notice(error.message); } }
  finally { pollingDisk = false; }
}
setInterval(pollDisk, 3000);
window.addEventListener("focus", pollDisk);
// Reload replaces the text only once the file has been read: if it cannot be, the editor keeps
// its text and its file (U2).
byId("reload-file").addEventListener("click", async () => {
  if (!current || !mayLeave()) return;
  await openFile(current.path, undefined, true);
});

function dialog(title, help, name, action, choices = null, key = false) {
  modalAction = action; byId("form-title").textContent = title; byId("form-help").textContent = help;
  byId("form-name").value = name; byId("form-name").hidden = key; byId("name-label").hidden = key;
  byId("form-name").required = !key; byId("form-error").textContent = "";
  byId("form-choice").hidden = !choices; byId("choice-label").hidden = !choices;
  byId("choice-label").textContent = key ? "Provider or tool key" : "Start from";
  byId("form-choice").replaceChildren();
  if (choices) for (const choice of choices) { const option = document.createElement("option"); option.value = choice.value; option.textContent = choice.label; byId("form-choice").append(option); }
  byId("form-value").hidden = !key; byId("value-label").hidden = !key; byId("form-value").required = key; byId("form-value").value = "";
  byId("form-submit").textContent = key ? "Save key" : "Continue"; byId("form-dialog").showModal();
  if (!key) { byId("form-name").focus(); byId("form-name").select(); }
}
byId("form-cancel").addEventListener("click", () => byId("form-dialog").close());
byId("form-dialog").addEventListener("close", () => { byId("form-value").value = ""; });
byId("action-form").addEventListener("submit", async event => {
  event.preventDefault(); byId("form-submit").disabled = true;
  try { await modalAction(byId("form-name").value.trim(), byId("form-choice").value, byId("form-value").value); byId("form-dialog").close(); }
  catch (error) { byId("form-error").textContent = error.message; }
  finally { byId("form-value").value = ""; byId("form-submit").disabled = false; }
});
byId("new-team").addEventListener("click", () => {
  if (!mayLeave()) return;
  dialog("New team", "Create a folder inside this workspace. Use letters, digits and underscores. “Describe it” has a model write the team from your description.", "my_team", async (name, example) => {
    if (example === DESCRIBE) { setTimeout(() => openDescribe(name)); return; }
    const result = await api("/api/new", {path: name, example}); dirty = false; await refresh(); await openFile(result.path);
  }, [{value:"",label:"Empty team"}, {value:DESCRIBE,label:"Describe it — a model writes the team"}, ...(info?.examples || []).map(name => ({value:name,label:name}))]);
});

// + Team → Describe it (D162): the same writer as `teamrunner new --describe`.
const DESCRIBE = ":describe", DESCRIPTION_LIMIT = 50000;
let describePoll = null, describedFile = null;
const describeBytes = () => new TextEncoder().encode(byId("describe-text").value).length;
function describeSize() {
  const bytes = describeBytes();
  byId("describe-size").textContent = bytes ? `${(bytes / 1000).toFixed(1)} KB of ${DESCRIPTION_LIMIT / 1000} KB` : "a .txt or .md file; you can edit it here before sending";
}
function describeShow(state) {
  const writing = state.state === "writing";
  for (const id of ["describe-name", "describe-text", "describe-load", "describe-model", "describe-submit"]) byId(id).disabled = writing;
  byId("describe-error").textContent = state.state === "failed" ? state.error || "" : "";
  byId("describe-problems").replaceChildren(...(state.problems || []).map(problem => {
    const item = document.createElement("li");
    item.textContent = [problem.file && `${problem.file}${problem.line ? `:${problem.line}` : ""}`, problem.message, problem.fix && `fix: ${problem.fix}`].filter(Boolean).join(" — ");
    return item;
  }));
  byId("describe-problems").hidden = !byId("describe-problems").children.length;
  byId("describe-explain").hidden = state.state !== "done";
  byId("describe-explain").textContent = state.explain || "";
  const cost = state.cost == null ? "cost unknown" : `$${state.cost.toFixed(4)}`;
  const tries = n => `${n} tr${n === 1 ? "y" : "ies"}`;
  byId("describe-status").textContent =
    writing ? `${state.model} is writing ${state.path}${state.round ? ` — try ${state.round} of ${state.of}` : ""}…`
    : state.state === "done" ? `✓ ${state.path} written by ${state.model} in ${tries(state.round)} · ${cost}${state.notes ? `. Its choices: ${state.notes}` : ""}`
    : state.state === "failed" && state.cost !== undefined ? `${cost} spent.${state.kept ? ` What it wrote is in ${state.path}.` : " Nothing was written."}` : "";
  describedFile = (state.state === "done" || state.kept) ? state.file : null;
  byId("describe-open").hidden = !describedFile;
  byId("describe-open").textContent = state.state === "done" ? "Open team" : "Open what it wrote";
  byId("describe-submit").hidden = state.state === "done";
}
async function describeWatch() {
  clearTimeout(describePoll);
  const state = await api("/api/describe-status");
  if (byId("describe-dialog").open) describeShow(state);
  if (state.state === "writing") { describePoll = setTimeout(describeWatch, 1000); return; }
  await refresh();
  if (!byId("describe-dialog").open && state.state === "done") notice(`${state.path} is written. Open its team.yaml to see it.`);
  if (!byId("describe-dialog").open && state.state === "failed") notice(`The model could not write ${state.path}: ${state.error}`);
}
async function openDescribe(name) {
  byId("describe-name").value = name; byId("describe-text").value = ""; byId("describe-file").value = "";
  describeShow({state: "idle"}); describeSize();
  byId("describe-dialog").showModal();
  const [models, state] = await Promise.all([api("/api/describe-models"), api("/api/describe-status")]);
  byId("describe-model").replaceChildren(...models.models.map(model => { const option = document.createElement("option"); option.value = option.textContent = model; return option; }));
  if (!models.models.length) { byId("describe-error").textContent = "No model key is set. Add an OpenAI, Anthropic or Gemini key with Keys first."; byId("describe-submit").disabled = true; }
  if (state.state === "writing") { describeShow(state); describeWatch(); }
  else byId("describe-text").focus();
}
byId("describe-load").addEventListener("click", () => byId("describe-file").click());
byId("describe-file").addEventListener("change", async () => {
  const file = byId("describe-file").files[0]; byId("describe-file").value = "";
  if (!file) return;
  if (file.size > DESCRIPTION_LIMIT) { byId("describe-error").textContent = `${file.name} is longer than ${DESCRIPTION_LIMIT / 1000} KB; shorten it.`; return; }
  byId("describe-text").value = await file.text(); byId("describe-error").textContent = ""; describeSize();
});
byId("describe-text").addEventListener("input", describeSize);
byId("describe-cancel").addEventListener("click", () => byId("describe-dialog").close());
byId("describe-open").addEventListener("click", async () => {
  const file = describedFile; byId("describe-dialog").close();
  if (file) { dirty = false; await refresh(); await openFile(file); }
});
byId("describe-form").addEventListener("submit", async event => {
  event.preventDefault();
  if (describeBytes() > DESCRIPTION_LIMIT) { byId("describe-error").textContent = `The description is longer than ${DESCRIPTION_LIMIT / 1000} KB; shorten it.`; return; }
  try {
    describeShow(await api("/api/describe", {path: byId("describe-name").value.trim(), description: byId("describe-text").value, model: byId("describe-model").value}));
    describeWatch();
  } catch (error) { byId("describe-error").textContent = error.message; }
});
byId("new-experiment").addEventListener("click", () => {
  if (!mayLeave()) return;
  const team = context || info.teams[0];
  dialog("New experiment", "An experiment runs versions of one team side by side and compares them. This makes experiments/<name>/experiment.md, with two copies of the team to start from and notes on what to change. Use letters, digits, _ and -.", "my_study", async (name, chosen) => {
    const result = await api("/api/new-experiment", {name, team: chosen});
    dirty = false; await refresh(); await openFile(result.path);
    notice(`${result.folder} is ready. Write what differs under variant's change:, then Validate, then Rehearse · free in Overview.`);
  }, info.teams.map(path => ({value: path, label: path.replace(/^teams\//, "")})));
  byId("choice-label").textContent = "Team to experiment on";
  byId("form-choice").value = info.teams.includes(team) ? team : info.teams[0];
  byId("form-submit").textContent = "Create";
});
// + Variant, + Metric, + Task: one file in the chosen experiment's own folder, then the line
// that uses it, shown to copy: experiment.md is the person's to edit.
function chosenExperiment() {
  if (experimentContext) return experimentContext;
  try { return mode === "experiments" ? experimentPath : ""; } catch { return ""; }  // experiments.js not read yet
}
function showLine(result) {
  byId("line-hint-where").textContent = `To use it, add this line ${result.where.replace(/`/g, "")}:`;
  byId("line-hint-code").textContent = result.line.trim();
  byId("line-hint-copy").textContent = "Copy";
  byId("line-hint").dataset.experiment = result.experiment || "";
  byId("line-hint").hidden = false;
}
async function addPart(route, data, made) {
  const result = await api(route, data);
  dirty = false; await refresh();
  if (result.path) await openFile(result.path);
  showLine({...result, experiment: data.experiment}); notice(made(result));
}
async function partsOf(exp) {
  try { return await api("/api/experiment-parts" + pathQuery(exp)); }
  catch (error) { notice(error.message); return null; }
}
byId("new-variant").addEventListener("click", async () => {
  const exp = chosenExperiment(); if (!exp || !mayLeave()) return;
  const found = await partsOf(exp); if (!found) return;
  if (!found.files.length) { notice("Fix experiment.md first: its conditions name no team to copy from."); return; }
  const many = found.teams.length > 1, choices = [];
  for (const f of found.files) {
    const where = many ? `${f.team}/` : "";
    if (f.what === "flow") choices.push({value: `${f.team}/${f.file}`, label: `${where}chair.md — the flow`});
    else {
      choices.push({value: `${f.team}/${f.file}#prompt`, label: `${where}${f.file} — its prompt only`});
      choices.push({value: `${f.team}/${f.file}`, label: `${where}${f.file} — the whole file (prompt and settings)`});
    }
  }
  dialog("New variant", "A variant is your version of one of the team’s files, used by a condition in place of the original. This copies the file into variants/ for you to edit; the team itself is never changed.", "my_variant",
    (name, source) => addPart("/api/variant", {experiment: exp, name, source}, r => `${r.path} is a copy to edit. Change it, then add the line below to a condition.`), choices);
  byId("choice-label").textContent = "Start from"; byId("form-submit").textContent = "Create";
});
byId("new-metric").addEventListener("click", async () => {
  const exp = chosenExperiment(); if (!exp || !mayLeave()) return;
  const found = await partsOf(exp); if (!found) return;
  const choices = [{value: "evaluator", label: "Evaluator — a model grades each run with your rubric (.md)"},
    {value: "answer_key", label: "Answer key — scored against a known answer, no model (.yaml)"},
    ...found.measurable.map(o => ({value: `output:${o}`, label: `A member’s typed answer: ${o} (no file)`}))];
  dialog("New metric", "What to measure in every run. Cost and time are always measured. A typed answer needs no file, only a line in experiment.md; the name is then not used.", "my_metric",
    (name, kind) => addPart("/api/metric", {experiment: exp, name, kind}, r => r.path ? `${r.path} is ready to fill in. Then add the line below to experiment.md.` : "Add the line below to experiment.md."), choices);
  byId("choice-label").textContent = "Kind"; byId("form-submit").textContent = "Create";
});
byId("new-task").addEventListener("click", () => {
  const exp = chosenExperiment(); if (!exp || !mayLeave()) return;
  dialog("New task", "A task gives every condition the same inputs, in place of the team’s defaults, so editing the team later cannot change the study. It starts with the inputs the team’s prompts take, filled with the team’s defaults.", "my_task",
    name => addPart("/api/task", {experiment: exp, name}, r => `${r.path} is ready: change the inputs to the case you want, then add the line below to experiment.md.`));
  byId("form-submit").textContent = "Create";
});
byId("line-hint-copy").addEventListener("click", async () => {
  try { await navigator.clipboard.writeText(byId("line-hint-code").textContent); byId("line-hint-copy").textContent = "Copied"; }
  catch { byId("line-hint-copy").textContent = "Select and copy it"; }
});
byId("line-hint-open").addEventListener("click", () => { const exp = byId("line-hint").dataset.experiment; if (exp) openFile(`${exp}/experiment.md`); });
byId("line-hint-close").addEventListener("click", () => { byId("line-hint").hidden = true; });
byId("other-root").addEventListener("click", () => byId(`${otherMode()}-mode`).click());
byId("new-member").addEventListener("click", () => {
  if (!mayLeave()) return;
  const folder = chose === "folder" || !current ? context : parent(current.path);
  dialog("New member", "Create a Markdown file in the selected team. Use letters, digits and underscores; for example, researcher.md.", "researcher.md", async name => {
    if (!/^[A-Za-z0-9_]+\.md$/.test(name) || !/[A-Za-z]/.test(name.slice(0, -3))) throw new Error("Use letters, digits and underscores, including at least one letter, followed by .md.");
    const result = await api("/api/member", {path: join(folder, name)}); dirty = false; await refresh(); await openFile(result.path);
  });
});
byId("new-chair").addEventListener("click", async () => {
  if (!hasTeam() || !mayLeave()) return;
  try {
    const result = await api("/api/chair", {path: context}); dirty = false; await refresh(); await openFile(result.path);
  } catch (error) { notice(error.message); }
});
byId("rename").addEventListener("click", () => {
  if (!current || !mayLeave()) return;
  const file = current;
  dialog("Rename file", "Renaming a member’s file renames the member. Validation will point out flow references that need updating.", file.path.split("/").pop(), async name => {
    const result = await api("/api/rename", {path:file.path, destination:join(parent(file.path),name), revision:file.revision}); dirty = false; current = null; await refresh(); await openFile(result.path);
  });
});
byId("trash").addEventListener("click", async () => {
  if (!current || !mayLeave() || !confirm(`Move ${current.path} to the system trash?`)) return;
  try {
    const path = current.path; await api("/api/trash", {path, revision:current.revision});
    current = null; dirty = false; generation++; byId("editing").hidden = true; document.querySelector(".welcome").hidden = false; updateState();
    await refresh(); await validate(false); notice("File moved to the trash.");
  } catch (error) { notice(error.message); }
});
byId("trash-team").addEventListener("click", async () => {
  if (!context || !mayLeave()) return;
  const team = context, count = (n, word) => `${n} ${word}${n === 1 ? "" : "s"}`;
  try {
    const holds = await api("/api/team-contents" + pathQuery(team));
    const records = holds.runs ? ` and ${count(holds.runs, "recorded run")}. The run records go with it` : "";
    if (!confirm(`Move the team ${team} to the system trash?\n\nThis takes the whole folder: ${count(holds.files, "file")}${records}. You can put it back from the trash.`)) return;
    await api("/api/trash-team", {path: team});
    if (current && current.path.startsWith(team + "/")) {
      current = null; dirty = false; generation++; byId("editing").hidden = true; document.querySelector(".welcome").hidden = false;
    }
    context = ""; fileCheck = onlineCheck = null; stale = false; showChecks(); updateState();
    document.dispatchEvent(new CustomEvent("teamrunner:team-select", {detail:""}));
    await refresh(); if (current && !current.readonly) await validate(false);  // back to the open file's team
    notice("Team moved to the trash.");
  } catch (error) { notice(error.message); }
});
byId("feedback").addEventListener("click", () => {
  const to = info?.feedback; if (!to) return;
  byId("feedback-bug").href = to.bug; byId("feedback-idea").href = to.idea;
  byId("feedback-mail").href = to.mail; byId("feedback-mail").textContent = to.email;
  byId("feedback-details").textContent = `${to.version} · ${to.system}`;
  byId("feedback-dialog").showModal();
});
byId("feedback-close").addEventListener("click", () => byId("feedback-dialog").close());
byId("keys").addEventListener("click", async () => {
  try {
    const status = await api("/api/keys" + pathQuery(teamContext()));
    const required = status.keys.map(k => `${k.name}: ${k.present ? "set" : "missing"}`).join("; ");
    dialog("Set up API keys", `${required || "No keys required, or fix validation errors to identify them."} Keys are saved in TeamRunner’s existing key file, outside the package. Stored values are never displayed.`, "", async (_name, name, value) => {
      await api("/api/key", {name, value}); onlineCheck = null; showChecks(); notice(`${name} saved. Press Validate to check it.`);
    }, [...new Set([...status.keys.map(key => key.name), ...status.allowed])].map(name => ({value:name,label:name})), true);
  } catch (error) { notice(error.message); }
});
byId("save").addEventListener("click", save);
byId("copy-unsaved").addEventListener("click", async () => {
  try { await navigator.clipboard.writeText(editor.getValue()); byId("copy-unsaved").textContent = "Copied"; }
  catch (error) { editor.focus(); editor.execCommand("selectAll"); byId("copy-unsaved").textContent = "Selected: press Ctrl+C (⌘C on a Mac)"; }
});
// The button checks what is on screen, asking the providers afresh: unsaved edits are saved first.
byId("validate").addEventListener("click", async () => {
  clearTimeout(validationTimer);
  if (dirty && current && !current.readonly) { await save(); clearTimeout(validationTimer); if (dirty) return; }
  await validate(true);
});
byId("edit-tab").addEventListener("click", showEdit);
byId("show-tab").addEventListener("click", () => inspect());
byId("refresh-flow").addEventListener("click", showFlow);
byId("refresh").addEventListener("click", refresh);
byId("fold-root").addEventListener("click", () => {
  const list = byId("files"), shown = list.hidden;
  list.hidden = !shown; byId("fold-root").setAttribute("aria-expanded", String(shown));
  byId("fold-root").querySelector(".caret").textContent = shown ? "▾" : "▸";
});
window.addEventListener("resize", () => editor.refresh());
const workspaceReady = refresh();
