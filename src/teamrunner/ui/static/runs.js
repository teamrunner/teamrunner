// SPDX-License-Identifier: GPL-3.0-or-later
// Copyright 2026 Kumar Muthuraman
"use strict";

let runSource = "", runData = null, runRows = [], runRequest = 0, detailRequest = 0, searchTimer;
const node = (tag, text, className) => {
  const item = document.createElement(tag);
  if (text !== undefined) item.textContent = typeof text === "string" ? text : JSON.stringify(text);
  if (className) item.className = className;
  return item;
};
const pretty = value => typeof value === "string" ? value : JSON.stringify(value, null, 2);
const cost = value => typeof value === "number" ? `$${value.toFixed(value < 1 ? 4 : 2)}` : "not recorded";
const runsNotice = text => { byId("runs-notice").textContent = text; };
const runNotice = text => { byId("run-notice").textContent = text; };

// Three tabs: Files (the team), Run (one run, live or saved) and Run history (every
// saved run). An experiment tab can join them later, opening its runs in Run.
const VIEWS = {files: ["files-workspace", "files-view"], run: ["run-workspace", "run-view"], runs: ["runs-workspace", "runs-view"]};
function showView(name) {
  for (const [view, [workspace, tab]] of Object.entries(VIEWS)) {
    byId(workspace).hidden = view !== name; byId(tab).setAttribute("aria-pressed", String(view === name));
  }
  document.title = `${info?.name || "TeamRunner"} · ${{files: "Files", run: "Run", runs: "Run history"}[name]}`;
}
function fileView() { showView("files"); editor.refresh(); }
// What the Run tab shows: the run in progress, or the saved run opened last.
function showRunPane(which) {
  byId("live-run").hidden = which !== "live"; byId("run-detail").hidden = which !== "saved";
  byId("run-empty").hidden = which !== "empty";
  byId("show-live").hidden = which === "live" || !liveJob || liveJob.done;
}
function runView() {
  showView("run");
  if (byId("live-run").hidden && byId("run-detail").hidden) showRunPane(liveJob && !liveJob.done ? "live" : runData ? "saved" : "empty");
}
async function runsView(sourceOverride = null) {
  const source = sourceOverride ?? teamContext();
  if (source !== runSource) { byId("runs-team").value = ""; byId("runs-status").value = ""; byId("runs-search").value = ""; }
  runSource = source;
  showView("runs");
  byId("run-comparison").hidden = true;
  byId("runs-list-panel").hidden = false;
  await loadRuns();
}
function options(id, values, label) {
  const select = byId(id), selected = select.value;
  select.replaceChildren(node("option", label)); select.firstChild.value = "";
  for (const value of values) { const option = node("option", value); option.value = value; select.append(option); }
  select.value = values.includes(selected) ? selected : "";
}
async function loadRuns(append = false) {
  byId("run-comparison").hidden = true;
  const request = ++runRequest;
  if (!append) runRows = [];
  byId("refresh-runs").disabled = true; runsNotice("Reading run folders…");
  const query = new URLSearchParams({path:runSource, team:byId("runs-team").value,
    status:byId("runs-status").value, search:byId("runs-search").value, limit:50, offset:runRows.length});
  try {
    const result = await api("/api/runs?" + query);
    if (request !== runRequest) return;
    runRows = append ? [...runRows, ...result.runs] : result.runs;
    options("runs-team", result.teams, "All teams"); options("runs-status", result.statuses, "All statuses");
    byId("runs-count").textContent = result.total ? `${runRows.length} of ${result.total} matching runs · newest first` : result.all_total ? "No runs match these filters." : "No saved runs yet.";
    const rows = byId("runs-rows"); rows.replaceChildren();
    for (const run of runRows) {
      const row = node("tr"), date = node("td"), open = node("button", run.when);
      open.type = "button"; open.addEventListener("click", () => openRun(run.path)); date.append(open); row.append(compareCheckbox(run),date);
      const team = node("td", run.team);
      if (run.rehearsal) team.append(node("span", "Rehearsal", "run-label"));
      if (run.backup) team.append(node("span", "Backup model", "run-label"));
      row.append(team, node("td", run.message), node("td", run.result));
      const status = node("td"); status.append(node("span", run.status, `run-status ${run.status}`));
      row.append(status, node("td", String(run.turns)), node("td", run.length), node("td", run.cost)); rows.append(row);
    }
    byId("more-runs").hidden = runRows.length >= result.total;
    byId("runs-table").hidden = !runRows.length;
    updateRunColumns();
    runsNotice(result.warnings.join("\n"));
    byId("runs-command").textContent = `teamrunner runs ${JSON.stringify(info ? join(info.folder, context) : context || ".")}`;
  } catch (error) {
    if (request === runRequest) { runsNotice(error.message); byId("runs-rows").replaceChildren(); byId("runs-count").textContent = ""; byId("more-runs").hidden = true; }
  } finally { if (request === runRequest) byId("refresh-runs").disabled = false; }
}
function runFileURL(path, data = runData) {
  return "/api/run-file?" + new URLSearchParams({run:data.summary.path, file:path, token});
}
function downloadLink(path, label = path) {
  const link = node("a", label); link.href = runFileURL(path); link.setAttribute("download", ""); return link;
}
function markdown(text, data = runData) {
  const result = node("div", undefined, "markdown");
  const fragment = DOMPurify.sanitize(marked.parse(String(text || ""), {async:false}), {
    ALLOWED_TAGS:["p","br","h1","h2","h3","h4","h5","h6","strong","em","del","a","ul","ol","li","blockquote","pre","code","hr","table","thead","tbody","tr","th","td"],
    ALLOWED_ATTR:["href","title","start","colspan","rowspan"], ALLOW_DATA_ATTR:false, RETURN_DOM_FRAGMENT:true
  });
  const available = new Set([...(data?.raw_files || []), ...(data?.artifacts || []).map(file => file.path)]);
  for (const link of fragment.querySelectorAll("a")) {
    const href = link.getAttribute("href") || "";
    if (/^https?:\/\//i.test(href)) { link.target = "_blank"; link.rel = "noopener noreferrer"; }
    else if (available.has(href.replace(/^\.\//, ""))) { link.href = runFileURL(href.replace(/^\.\//, ""),data); link.setAttribute("download", ""); }
    else { link.removeAttribute("href"); }
  }
  result.append(fragment); return result;
}
function jsonDetails(title, data, open = false) {
  const details = node("details"); details.open = open;
  details.append(node("summary", title), node("pre", pretty(data))); return details;
}
function showRunTab(name) {
  for (const view of ["conversation", "output", "record", "path"]) {
    byId("run-" + view).hidden = view !== name;
    byId(view + "-tab").setAttribute("aria-pressed", String(view === name));
  }
}
function speaker(event, data = runData) {
  const member = data.members.find(m => (event.instance_path && m.instance_path === event.instance_path) || m.name === event.from);
  return event.from || member?.name || event.instance_path?.split("/").pop() || "member";
}
function renderConversation(data = runData, container = byId("run-conversation"), live = false, state = null) {
  if (!state) {
    container.replaceChildren();
    state = {count:0, messages:0, bySequence:new Map(), empty:null};
  }
  const fresh = data.events.slice(state.count), bySequence = state.bySequence;
  for (const event of fresh) bySequence.set(event.seq, event);
  let messages = state.messages;
  for (const event of fresh) {
    if (event.event === "agent_message") {
      messages++;
      const card = node("article", undefined, "message-card");
      const heading = node("header"); heading.append(node("h4", `${speaker(event, data)} → ${typeof event.to === "string" ? event.to : pretty(event.to) || "not recorded"}`));
      const stage = event.stage ? `stage ${event.stage}` : "";
      const attempt = typeof event.attempt === "object" && event.attempt ? `attempt ${event.attempt.number}${event.attempt.of == null ? "" : " of " + event.attempt.of}` : event.attempt ? `attempt ${event.attempt}` : "";
      heading.append(node("span", [stage, attempt].filter(Boolean).join(" · "), "message-meta"));
      card.append(heading, markdown(event.content, data));
      if (event.output && Object.keys(event.output).length) card.append(jsonDetails("Declared outputs", event.output, true));
      const usage = event.usage ? `${event.usage.input ?? "?"} input / ${event.usage.output ?? "?"} output tokens` : "tokens not recorded";
      card.append(node("p", `${event.model_resolved || "model not recorded"} · ${usage} · ≈ ${cost(event.cost_usd)}`, "message-meta"));
      const read = node("details", undefined, "read-context"); read.append(node("summary", "What it could read"));
      // Long transcripts keep read-context DOM lazy until the reader opens it.
      read.addEventListener("toggle", () => {
        if (!read.open || read.dataset.loaded) return;
        read.dataset.loaded = "true";
        const member = data.members.find(m => m.name === event.from || (event.instance_path && m.instance_path === event.instance_path));
        if (member?.prompt) read.append(jsonDetails("Instructions recorded at run start", member.prompt));
        if (!Array.isArray(event.read)) read.append(node("p", "This log does not record which earlier messages were visible."));
        else if (!event.read.length) read.append(node("p", "No earlier messages were visible. The member still had its recorded instructions and inputs."));
        else for (const sequence of event.read) {
          const entry = bySequence.get(sequence);
          const title = entry ? `Entry ${sequence} · ${entry.from || entry.toolkeeper || entry.event}` : `Entry ${sequence} · missing from this log`;
          read.append(jsonDetails(title, entry ? entry.content ?? entry.result ?? entry : "This recorded entry is unavailable."));
          if (entry?.result_kept === false) read.append(node("p", "The full tool reply was not kept. This entry contains only the metadata recorded by the run."));
        }
      });
      card.append(read); container.append(card);
    } else if (event.event === "route_decision") {
      container.append(node("div", `⑂ ${event.decider} · ${event.field || "answer"} = ${pretty(event.option)} → ${pretty(event.to)}${event.bound_applied ? " · counted exit applied" : ""}`, "route-marker"));
    } else if (event.event === "tool_call") {
      const tool = node("details", undefined, "tool-card");
      tool.append(node("summary", `${event.from || "member"} → ${event.toolkeeper || "toolkeeper"} · ${event.tool || "tool"} · ${event.access || "access not recorded"}`));
      if (event.reason) tool.append(node("p", event.reason));
      if (event.error) tool.append(node("p", event.error, "event-note error"));
      if (event.result_kept === false) tool.append(node("p", "The full tool result was not kept; recorded metadata is shown below."));
      if (event.allowance) tool.append(jsonDetails("Access and limits", event.allowance));
      tool.append(jsonDetails("Arguments", event.arguments ?? {}, true), jsonDetails("Result", event.result ?? "No result recorded.", true));
      tool.append(node("p", `≈ ${cost(event.cost_usd)}`, "message-meta")); container.append(tool);
    } else if (["retry", "warning", "error"].includes(event.event)) {
      const box = node("div", undefined, "event-note " + event.event);
      box.append(node("strong", `${event.event}${event.instance_path ? " · " + speaker(event, data) : ""}`));
      box.append(node("p", event.message || event.reason || pretty(event)));
      if (event.reply) box.append(jsonDetails("Reply that did not fit", event.reply)); container.append(box);
    } else if (event.event === "run_finished") {
      container.append(node("p", `${event.status || "finished"}${event.reason ? " · " + event.reason : ""}`, "run-ending"));
    } else if (event.event === "check") {
      const failures = (event.problems || []).length;
      container.append(jsonDetails(failures ? `Pre-run check · ${failures} problems` : "Pre-run check", event));
    } else if (!["run_started"].includes(event.event)) {
      container.append(jsonDetails(event.event, event));
    }
  }
  if (messages) { state.empty?.remove(); state.empty = null; }
  else {
    if (!state.empty) { state.empty = node("p"); container.prepend(state.empty); }
    state.empty.textContent = live ? "Waiting for the first completed turn…" : "No member messages were recorded in this run.";
  }
  state.count = data.events.length; state.messages = messages;
  return state;
}
function renderRecord() {
  const container = byId("run-record"); container.replaceChildren();
  container.append(node("h4", "Members as recorded"), node("p", "Version numbers come from each member’s first appearance in the available run history. Nothing is written to the files.", "message-meta"));
  if (!runData.members.length) container.append(node("p", "This log has no recorded member inventory."));
  for (const member of runData.members) {
    const version = member.version ? `version ${member.version.number} · first run ${member.version.first_run_shown}` : "version not recorded";
    const card = node("section", undefined, "record-member");
    card.append(node("h4", `${member.name || "member"}, ${version}`));
    card.append(node("p", [member.kind, member.model].filter(Boolean).join(" · "), "message-meta"));
    card.append(jsonDetails("Recorded prompt and settings", member)); container.append(card);
  }
  container.append(node("h4", "Inputs, work and saved team files"));
  if (!runData.artifacts.length) container.append(node("p", "No files were recorded in inputs/, work/ or resolved/."));
  const files = node("ul", undefined, "artifact-list");
  for (const file of runData.artifacts) { const item = node("li"); item.append(downloadLink(file.path), node("span", ` · ${file.size.toLocaleString()} bytes`, "message-meta")); files.append(item); }
  container.append(files, node("p", runData.has_spec ? "A saved team is present in resolved/. Copy it into a new team to edit." : "This run does not contain a saved team spec. The original team files cannot be reconstructed from recorded prompts alone."));
  if (runData.has_spec) {
    const copy = node("button","Edit a copy of the saved team…");
    copy.addEventListener("click",()=>dialog("Copy saved team", "Create a new independent team inside this workspace. The historical run stays read-only.", "saved_team", async name => {
      const result = await api("/api/restore-team",{path:runData.summary.path,destination:name});
      await refresh(); await openFile(result.path);
    })); container.append(copy);
  }
  container.append(jsonDetails("record.json · inputs, fingerprint, cost and environment", runData.record, true));
}
async function openRun(path) {
  byId("run-comparison").hidden = true;
  const request = ++detailRequest; showView("run"); runNotice("Reading this run…");
  try {
    const result = await api("/api/run" + pathQuery(path)); if (request !== detailRequest) return;
    runData = result; const run = result.summary;
    showRunPane("saved");
    byId("run-title").textContent = `${run.team} · ${run.when}`;
    byId("run-bar-title").textContent = `${run.team} · ${run.when} · ${run.status}${run.rehearsal ? " · rehearsal" : ""}`;
    byId("run-meta").textContent = `${run.status}${run.rehearsal ? " · rehearsal" : ""}${run.backup ? " · backup model" : ""} · ${run.turns} turns · ${run.length} · ≈ ${run.cost}`;
    byId("raw-run-files").replaceChildren(...result.raw_files.map(file => downloadLink(file)));
    byId("run-warnings").textContent = result.warnings.join("\n");
    renderConversation(); renderRecord();
    byId("run-path").textContent = "Reading recorded path…";
    api("/api/run-path" + pathQuery(path)).then(data => {if (request === detailRequest) renderPath(byId("run-path"),data);}).catch(error => {if (request === detailRequest) byId("run-path").textContent=error.message;});
    byId("run-output").replaceChildren(result.output ? markdown(result.output) : node("p", "No output was recorded."));
    showRunTab("conversation"); runNotice("");
    byId("run-command").textContent = `teamrunner runs ${JSON.stringify(context || ".")} ${JSON.stringify(path.split("/").pop())}`;
  } catch (error) { if (request === detailRequest) runNotice(error.message); }
}
byId("files-view").addEventListener("click", fileView);
byId("runs-view").addEventListener("click", () => runsView());
byId("run-view").addEventListener("click", runView);
byId("refresh-runs").addEventListener("click", () => loadRuns());
byId("refresh-run").addEventListener("click", () => openRun(runData.summary.path));
byId("back-runs").addEventListener("click", () => runsView(runSource || null));
byId("show-live").addEventListener("click", () => showRunPane("live"));
for (const id of ["runs-team", "runs-status"]) byId(id).addEventListener("change", () => { loadRuns(); });
byId("runs-search").addEventListener("input", () => { clearTimeout(searchTimer); searchTimer = setTimeout(() => { loadRuns(); }, 250); });
byId("more-runs").addEventListener("click", () => { loadRuns(true); });
for (const name of ["conversation", "output", "record", "path"]) byId(name + "-tab").addEventListener("click", () => showRunTab(name));
byId("open-run-folder").addEventListener("click", async () => {
  try { await api("/api/open-run-folder", {path:runData.summary.path}); runNotice("Run folder opened on your computer."); }
  catch (error) { runNotice(error.message); }
});
document.addEventListener("teamrunner:file-open", fileView);
document.addEventListener("teamrunner:team-select", event => {
  if (!byId("runs-workspace").hidden) { runSource = event.detail; byId("runs-team").value = ""; byId("runs-status").value = ""; byId("run-comparison").hidden = true; byId("runs-list-panel").hidden = false; loadRuns(); }
});

// Explicit controls also make wide tables usable without a trackpad gesture.
const runTableWrap = byId("runs-table").parentElement;
function updateRunColumns() {
  const end = runTableWrap.scrollWidth - runTableWrap.clientWidth;
  byId("run-column-controls").hidden = !runRows.length || end < 2;
  byId("previous-columns").disabled = runTableWrap.scrollLeft <= 1;
  byId("next-columns").disabled = runTableWrap.scrollLeft >= end - 1;
}
for (const [id, direction] of [["previous-columns", -1], ["next-columns", 1]]) {
  byId(id).addEventListener("click", () => {
    runTableWrap.scrollBy({left:direction * Math.max(200, runTableWrap.clientWidth * 0.8), behavior:"smooth"});
  });
}
runTableWrap.addEventListener("scroll", updateRunColumns);
new ResizeObserver(updateRunColumns).observe(runTableWrap);
