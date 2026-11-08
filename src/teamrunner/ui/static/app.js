"use strict";

const token = new URLSearchParams(location.search).get("token") || "";
const byId = id => document.getElementById(id);
let current = null, dirty = false, setting = false, validationTimer, validating = 0;
let roles = {}, problems = [], info = null, modalAction = null, saving = false;
let context = "", generation = 0;

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
  if (!setting && current) { dirty = editor.getValue() !== current.text; updateState(); }
});
function updateState() {
  byId("save").disabled = !current || current.readonly || !dirty || saving;
  byId("rename").disabled = !current || current.readonly;
  byId("trash").disabled = !current || current.readonly;
  byId("save-state").textContent = current ? (current.readonly ? "Read-only run record" : dirty ? "● Unsaved changes" : "Saved") : "";
  byId("breadcrumb").textContent = current ? `${current.path}${dirty ? " ●" : ""}` : "Your workspace";
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
    notice(file.readonly ? "Run records are read-only." : "Save with Ctrl+S or ⌘S.");
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
      notice("Saved. Checking the team…"); updateState();
      if (byId("flow-tab").getAttribute("aria-pressed") === "true") await inspect("flow");
      clearTimeout(validationTimer); validationTimer = setTimeout(() => validate(false), 350);
    }
  } catch (error) { notice(error.message); }
  finally { saving = false; updateState(); }
}
async function validate(online = false, pathOverride = null) {
  const id = ++validating, path = pathOverride ?? teamContext();
  byId("validation-summary").textContent = online ? "Checking keys and models online…" : "Checking files…";
  byId(online ? "online" : "validate").disabled = true;
  try {
    const result = await api("/api/validate", {path, online});
    if (id !== validating) return;
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
    const warnings = problems.length - errors;
    byId("validation-summary").textContent = `${errors} error${errors === 1 ? "" : "s"} · ${warnings} warning${warnings === 1 ? "" : "s"}${dirty ? " · saved files only" : ""}`;
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
    if (result.online) {
      for (const p of result.online.problems) {
        const item = document.createElement("li"); item.className = "online-problem";
        item.textContent = `${p.what} · ${p.who.join(", ")}: ${p.wrong} — ${p.fix}`; list.append(item);
      }
      for (const warning of result.online.warnings) { const item = document.createElement("li"); item.textContent = warning; list.append(item); }
      byId("validation-summary").textContent += result.online.problems.length ? ` · ${result.online.problems.length} online problems` : " · online check passed";
    }
    markers(); updateRoles(); command(online ? "validate --online" : "validate");
    notice(errors ? "Fix the errors below before running the team." : dirty ? "Checked the saved files. Save to check your edits." : "Validation complete.");
    return result;
  } catch (error) {
    if (id === validating) { byId("validation-summary").textContent = error.message; notice(error.message); }
  }
  finally { byId("online").disabled = false; byId("validate").disabled = false; }
}
function highlightSelection() {
  document.querySelectorAll(".tree .file").forEach(button => {
    const selected = current && button.dataset.path === current.path;
    button.classList.toggle("selected", Boolean(selected));
    if (selected) button.setAttribute("aria-current", "true"); else button.removeAttribute("aria-current");
  });
}
function updateRoles() {
  document.querySelectorAll(".tree .file").forEach(button => {
    button.querySelector(".role")?.remove();
    if (roles[button.dataset.path]) { const badge = document.createElement("span"); badge.className = "badge role"; badge.textContent = roles[button.dataset.path]; button.append(badge); }
  });
}
async function loadFiles(path, list) {
  const result = await api("/api/files" + pathQuery(path));
  if (!list.isConnected) return;
  const signature = JSON.stringify(result.entries);
  if (list.listingSignature === signature) return;
  const expanded = new Set([...list.querySelectorAll('button[aria-expanded="true"]')].map(button => button.dataset.path));
  list.replaceChildren();
  for (const entry of result.entries) {
    const item = document.createElement("li"), button = document.createElement("button");
    const folder = entry.kind === "folder"; button.type = "button"; button.className = folder ? "folder" : "file";
    button.dataset.path = entry.path;
    const icon = document.createElement("span"); icon.className = "file-icon"; icon.setAttribute("aria-hidden", "true"); icon.textContent = folder ? "▸" : "·";
    const name = document.createElement("span"); name.className = "file-name"; name.textContent = entry.name; button.append(icon, name);
    if (entry.team || entry.name === "runs") {
      const badge = document.createElement("span"); badge.className = "badge"; badge.textContent = entry.team ? "team" : "records"; button.append(badge);
    }
    item.append(button); list.append(item);
    if (folder) {
      const nested = document.createElement("ul"); nested.hidden = true; button.setAttribute("aria-expanded", "false");
      button.addEventListener("click", async () => {
        button.disabled = true;
        try {
          if (nested.hidden) await loadFiles(entry.path, nested);
          nested.hidden = !nested.hidden; button.setAttribute("aria-expanded", String(!nested.hidden)); icon.textContent = nested.hidden ? "▸" : "▾";
          if (entry.team) { context = entry.path; notice(`Team selected: ${entry.name}. Choose a file to edit.`); document.dispatchEvent(new CustomEvent("teamrunner:team-select", {detail:entry.path})); }
          updateRoles(); highlightSelection();
        } catch (error) { notice(error.message); } finally { button.disabled = false; }
      }); item.append(nested);
      if (expanded.has(entry.path)) {
        nested.hidden = false; button.setAttribute("aria-expanded", "true"); icon.textContent = "▾";
        await loadFiles(entry.path, nested);
      }
    } else { button.addEventListener("click", () => openFile(entry.path)); }
  }
  list.listingSignature = signature;
  if (!result.entries.length) { const empty = document.createElement("li"); empty.className = "empty"; empty.textContent = "No files here yet. Start with New team."; list.append(empty); }
  updateRoles(); highlightSelection();
}
async function refresh() {
  byId("refresh").disabled = true;
  try {
    info = await api("/api/info"); document.title = `${info.name} · TeamRunner`;
    byId("folder-name").textContent = info.name; byId("folder-path").textContent = info.folder;
    if (!info.team) { byId("welcome-title").textContent = "A place for your teams."; byId("welcome-text").textContent = "Choose New team to start empty or copy a shipped example. Your files stay in this folder."; }
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
        current = disk; generation++;
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
    await refresh(); await validate(false); notice("File moved to the system trash.");
  } catch (error) { notice(error.message); }
});
byId("keys").addEventListener("click", async () => {
  try {
    const status = await api("/api/keys" + pathQuery(teamContext()));
    const required = status.keys.map(k => `${k.name}: ${k.present ? "set" : "missing"}`).join("; ");
    dialog("Set up API keys", `${required || "No keys required, or fix validation errors to identify them."} Keys are saved in TeamRunner’s existing key file, outside the package. Stored values are never displayed.`, "", async (_name, name, value) => {
      await api("/api/key", {name, value}); notice(`${name} saved. Use the online check when ready.`);
    }, [...new Set([...status.keys.map(key => key.name), ...status.allowed])].map(name => ({value:name,label:name})), true);
  } catch (error) { notice(error.message); }
});
byId("save").addEventListener("click", save);
byId("validate").addEventListener("click", () => validate(false));
byId("online").addEventListener("click", () => validate(true));
byId("edit-tab").addEventListener("click", showEdit);
byId("show-tab").addEventListener("click", () => inspect("show"));
byId("flow-tab").addEventListener("click", () => inspect("flow"));
byId("refresh").addEventListener("click", refresh);
const workspaceReady = refresh();
