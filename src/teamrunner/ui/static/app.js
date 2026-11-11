// SPDX-License-Identifier: GPL-3.0-or-later
// Copyright 2026 Kumar Muthuraman
"use strict";

const token = new URLSearchParams(location.search).get("token") || "";
const byId = id => document.getElementById(id);
byId("legal-notices").addEventListener("click", () => byId("legal-dialog").showModal());
let current = null, dirty = false, setting = false, validationTimer, validating = 0;
let roles = {}, problems = [], info = null, modalAction = null, saving = false;
let context = "", generation = 0;
// What the last check found. Saving and the Validate button make the same check,
// the one `teamrunner validate` makes: files, then keys and models (D140). Saving
// may use a provider's model list from the last few minutes; the button asks again.
// An edit makes the result stale: nothing on screen may claim a check of other text.
let fileCheck = null, onlineCheck = null, stale = false, checkedAt = "";
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

async function api(route, data) {
  const response = await fetch(route, {method: data === undefined ? "GET" : "POST",
    headers: {"X-TeamRunner-Token": token, ...(data === undefined ? {} : {"Content-Type": "application/json"})},
    ...(data === undefined ? {} : {body: JSON.stringify(data)})});
  const result = await response.json();
  if (!response.ok) throw new Error(result.error || "The action could not be completed.");
  return result;
}
const notice = text => { byId("notice").textContent = text; };
const pathQuery = path => `?path=${encodeURIComponent(path)}`;
const parent = path => path.includes("/") ? path.slice(0, path.lastIndexOf("/")) : "";
const join = (folder, name) => folder ? `${folder}/${name}` : name;
const inRuns = path => path.split("/").some(part => part.toLowerCase() === "runs");
const teamContext = () => current ? current.path : context;
const command = (action, path = context || ".") => {
  byId("command").textContent = `teamrunner ${action} ${JSON.stringify(path || ".")}`;
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
  byId("new-chair").title = record ? "Run records are read-only. Select a team’s file to add a chair."
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
  byId("new-team").title = record ? "Run records are read-only. Select a team’s file to create a team." : "Create a new team folder";
  byId("new-member").title = record ? "Run records are read-only. Select a team’s file to add a member." : "Add a member to the selected team";
  byId("trash-team").disabled = !context || inRuns(context);
  byId("trash-team").title = context ? `Move the team ${context} to the trash` : info?.team ? "This team is the folder you opened; it can’t be moved to the trash from here." : "Select a team to move it to the trash";
  byId("save-state").textContent = current ? (current.readonly ? "Read-only run record" : dirty ? "● Unsaved" : "Saved") : "";
  byId("breadcrumb").textContent = current ? current.path : "";
  byId("show-tab").hidden = !current || current.readonly || !isMember(current.path);
  byId("team-name").textContent = context ? context.split("/").pop() : info?.team ? info.name : "—";
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
async function openFile(path, line) {
  if (current && current.path === path) { document.dispatchEvent(new Event("teamrunner:file-open")); if (line) jump(line); return; }
  if (!mayLeave()) return;
  try {
    const file = await api("/api/file" + pathQuery(path));
    document.dispatchEvent(new Event("teamrunner:file-open"));
    current = file; dirty = false; generation++;
    byId("editing").hidden = false; document.querySelector(".welcome").hidden = true;
    byId("disk-warning").hidden = true;
    setting = true; editor.setValue(file.text); editor.clearHistory(); setting = false;
    editor.setOption("readOnly", file.readonly);
    const yaml = /\.ya?ml$/i.test(path);
    editor.setOption("mode", yaml ? "yaml" : path.endsWith("chair.md") ?
      {name: "yaml-frontmatter", base: "teamrunner-flow"} : {name: "yaml-frontmatter", base: "markdown"});
    showEdit(); updateState(); markers(); highlightSelection(); editor.focus();
    notice("");
    if (line) jump(line);
    if (!file.readonly) await validate(false);
  } catch (error) { notice(error.message); }
}
function jump(line) { showEdit(); editor.setCursor(Math.max(0, line - 1), 0); editor.scrollIntoView({line: line - 1, ch: 0}, 70); editor.focus(); }
function showEdit() {
  byId("flow-diagram").hidden = true;
  editor.getWrapperElement().hidden = false; byId("resolved").hidden = true;
  for (const id of ["edit-tab", "show-tab", "flow-tab"]) byId(id).setAttribute("aria-pressed", String(id === "edit-tab"));
  editor.refresh();
}
async function inspect(kind) {
  if (!current) return;
  if (dirty) { notice("Save this file first to see the version on disk."); return; }
  const id = ++generation;
  try {
    const result = await api(`/api/${kind === "show" ? "show" : "diagram"}` + pathQuery(current.path));
    if (id !== generation) return;
    editor.getWrapperElement().hidden = true; byId("resolved").hidden = false;
    byId("resolved").textContent = result.text;
    byId("flow-diagram").hidden = kind !== "flow";
    if (kind === "flow") drawDiagram(byId("flow-diagram"), result.mermaid);
    for (const name of ["edit-tab", "show-tab", "flow-tab"]) byId(name).setAttribute("aria-pressed", String(name === (kind === "show" ? "show-tab" : "flow-tab")));
    command(kind === "show" ? "show" : "explain --diagram", kind === "show" ? current.path : context);
  } catch (error) { notice(error.message); }
}
async function save() {
  if (!current || current.readonly || !dirty || saving) return;
  saving = true; updateState();
  const file = current, text = editor.getValue();
  try {
    const result = await api("/api/save", {path: file.path, revision: file.revision, text});
    if (current === file) {
      current = result; dirty = editor.getValue() !== result.text; byId("disk-warning").hidden = true;
      notice(""); onlineCheck = null; updateState();
      if (byId("flow-tab").getAttribute("aria-pressed") === "true") await inspect("flow");
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
    context = result.team === "." ? "" : result.team;
    if (current && result.ids_added.includes(current.path)) {
      if (dirty) { byId("disk-warning").hidden = false; }
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
  for (const entry of result.entries) {
    const item = document.createElement("li"), button = document.createElement("button");
    const folder = entry.kind === "folder"; button.type = "button"; button.className = folder ? "folder" : "file";
    button.dataset.path = entry.path;
    button.dataset.inTeam = String(inTeam);
    const caret = document.createElement("span"); caret.className = "caret"; caret.setAttribute("aria-hidden", "true"); caret.textContent = folder ? "▸" : "";
    let glyph = icon(folder ? "closed" : "file");
    const name = document.createElement("span"); name.className = "file-name"; name.textContent = entry.name + (folder ? "/" : ""); button.append(caret, glyph, name);
    if (entry.team) button.classList.add("team-folder");
    if (inRuns(entry.path)) button.classList.add("record");
    if (entry.team || entry.name === "runs") {
      const badge = document.createElement("span"); badge.className = "badge" + (entry.team ? " kind-team" : ""); badge.textContent = entry.team ? "team" : "records"; button.append(badge);
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
          if (entry.team) { context = entry.path; updateState(); document.dispatchEvent(new CustomEvent("teamrunner:team-select", {detail:entry.path})); }
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
  if (!result.entries.length) { const empty = document.createElement("li"); empty.className = "empty"; empty.textContent = inRuns(path) ? "Nothing recorded here." : path ? "This folder is empty." : "No files here yet. Start with + Team."; list.append(empty); }
  updateRoles(); highlightSelection();
}
async function refresh() {
  byId("refresh").disabled = true;
  try {
    info = await api("/api/info"); document.title = `${info.name} · TeamRunner`;
    byId("folder-name").textContent = info.name + "/"; byId("folder-path").textContent = info.folder; byId("tree-root").title = info.folder;
    if (!info.team) { byId("welcome-title").textContent = "A place for your teams."; byId("welcome-text").textContent = "Choose + Team to start empty or copy a shipped example. Your files stay in this folder."; }
    await loadFiles("", byId("files")); byId("tree-status").textContent = "";
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
    await branch("", byId("files"));
    if (focused && (document.activeElement === focusedElement || document.activeElement === document.body)) [...byId("files").querySelectorAll("button")].find(button => button.dataset.path === focused)?.focus({preventScroll:true});
  } catch (error) { byId("tree-status").textContent = error.message; }
  finally { treeRefreshing = false; }
}
async function pollDisk() {
  if (saving || document.hidden || pollingDisk) return;
  refreshTree();
  if (!current) return;
  pollingDisk = true;
  const file = current;
  try {
    const disk = await api("/api/file" + pathQuery(file.path));
    if (current === file && disk.revision !== file.revision) {
      if (dirty) byId("disk-warning").hidden = false;
      else {
        const cursor = editor.getCursor();
        current = disk; generation++; onlineCheck = null;
        setting = true; editor.setValue(disk.text); editor.clearHistory(); setting = false;
        editor.setCursor(cursor); updateState(); byId("disk-warning").hidden = true;
        notice("Reloaded changes from disk.");
        if (byId("flow-tab").getAttribute("aria-pressed") === "true") await inspect("flow");
        else if (byId("show-tab").getAttribute("aria-pressed") === "true") await inspect("show");
      }
    }
  } catch (error) { if (current === file) { byId("disk-warning").hidden = false; notice(error.message); } }
  finally { pollingDisk = false; }
}
setInterval(pollDisk, 3000);
window.addEventListener("focus", pollDisk);
byId("reload-file").addEventListener("click", async () => {
  if (!current || !mayLeave()) return;
  const path = current.path; current = null; dirty = false; await openFile(path);
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
  dialog("New team", "Create a folder inside this workspace. Use letters, digits and underscores.", "my_team", async (name, example) => {
    const result = await api("/api/new", {path: name, example}); dirty = false; await refresh(); await openFile(result.path);
  }, [{value:"",label:"Empty team"}, ...(info?.examples || []).map(name => ({value:name,label:name}))]);
});
byId("new-member").addEventListener("click", () => {
  if (!mayLeave()) return;
  const folder = current ? parent(current.path) : context;
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
// The button checks what is on screen, asking the providers afresh: unsaved edits are saved first.
byId("validate").addEventListener("click", async () => {
  clearTimeout(validationTimer);
  if (dirty && current && !current.readonly) { await save(); clearTimeout(validationTimer); if (dirty) return; }
  await validate(true);
});
byId("edit-tab").addEventListener("click", showEdit);
byId("show-tab").addEventListener("click", () => inspect("show"));
byId("flow-tab").addEventListener("click", () => inspect("flow"));
byId("refresh").addEventListener("click", refresh);
window.addEventListener("resize", () => editor.refresh());
const workspaceReady = refresh();
