"use strict";

let runSetup = null, rerunSource = null, liveJob = null, eventSource = null;
let liveConversation = null, liveTotals = {known:0, unknown:false, turns:0};
let liveData = {events:[], members:[], artifacts:[], raw_files:[]}, seenEvents = new Set(), liveRender = null;

function quoteArg(value) {
  const text = String(value);
  return runSetup?.windows ? "'" + text.replace(/'/g, "''") + "'" : "'" + text.replace(/'/g, "'\"'\"'") + "'";
}
function updateRunForm() {
  const mode = byId("input-mode").value, rehearsal = byId("run-rehearse").checked;
  for (const id of ["run-message", "run-message-label"]) byId(id).hidden = Boolean(rerunSource) || mode !== "message";
  for (const id of ["run-file", "run-file-label"]) byId(id).hidden = Boolean(rerunSource) || !["file", "task"].includes(mode);
  byId("input-mode").disabled = Boolean(rerunSource);
  byId("input-mode").hidden = Boolean(rerunSource);
  byId("input-mode-label").hidden = Boolean(rerunSource);
  byId("rerun-inputs").hidden = !rerunSource;
  byId("start-run").textContent = rehearsal ? "Rehearse team" : "Run with models";
  byId("run-cost-note").textContent = rehearsal ? "Free: placeholder words and random answers to test the flow. No model or tool is called." : "This run calls the models and tools named by your team, using your keys. Provider charges may apply.";
  let shown = runSetup?.command_base || "teamrunner run";
  if (rerunSource) {
    const inputs = Object.values(runData.record.inputs || {});
    if (inputs.some(item => !item.file)) shown += " --task-file recorded-inputs.yaml";
    for (const item of inputs.filter(item => item.file)) shown += " --file " + quoteArg(item.file.original.replace(/\\/g, "/").split("/").pop());
  }
  else if (mode === "message") shown += " --message " + quoteArg(byId("run-message").value);
  else if (["file", "task"].includes(mode)) shown += (mode === "file" ? " --file " : " --task-file ") + quoteArg(byId("run-file").files[0]?.name || "your-input-file");
  if (rehearsal) shown += " --rehearse";
  byId("run-form-command").textContent = shown;
}
async function showRunForm(rerun = false) {
  if (dirty) { fileView(); notice("Save your changes before starting a run."); return; }
  let path = byId("runs-workspace").hidden ? teamContext() : runSource;
  rerunSource = rerun && runData ? runData.summary.path : null;
  if (rerunSource) {
    const parts = rerunSource.split("/"), at = parts.lastIndexOf("runs");
    if (at >= 0 && typeof runData.record.entry === "string") path = [...parts.slice(0, at), ...runData.record.entry.split("/").slice(1)].join("/");
  }
  try {
    runSetup = await api("/api/run-setup" + pathQuery(path));
    byId("run-form-title").textContent = `${rerunSource ? "Re-run" : "Run"} ${runSetup.name}`;
    byId("run-form-help").textContent = rerunSource ? "Use the recorded inputs with the current saved team files. This does not restore historical member versions. For the equivalent CLI command, put these inputs in recorded-inputs.yaml." : "The team is validated before starting. Only one run can be in progress.";
    byId("input-mode").value = "defaults"; byId("run-message").value = ""; byId("run-file").value = "";
    byId("run-form-error").textContent = "";
    byId("run-rehearse").checked = rerunSource ? Boolean(runData.record.rehearsal) : runSetup.default_rehearse;
    byId("rerun-inputs").textContent = rerunSource ? JSON.stringify({inputs:Object.fromEntries(Object.entries(runData.record.inputs || {}).filter(([,item]) => !item.file).map(([name,item]) => [name,item.value]))}, null, 2) : "";
    updateRunForm(); byId("run-dialog").showModal();
  } catch (error) { if (byId("runs-workspace").hidden) notice(error.message); else runsNotice(error.message); }
}
function upload(file) {
  if (!file) return Promise.reject(new Error("Choose an input file."));
  if (file.size > 8 * 1024 * 1024) return Promise.reject(new Error("Choose a file no larger than 8 MB."));
  return new Promise((resolve, reject) => {
    const reader = new FileReader();
    reader.onload = () => resolve({name:file.name, base64:String(reader.result).split(",")[1]});
    reader.onerror = () => reject(new Error("The file could not be read."));
    reader.readAsDataURL(file);
  });
}
byId("run-form").addEventListener("submit", async event => {
  event.preventDefault(); byId("start-run").disabled = true; byId("run-form-error").textContent = "";
  try {
    const report = await validate(false, runSetup.team);
    if (!report) throw new Error("The team could not be validated.");
    if (!report.ok) {
      byId("run-dialog").close(); fileView(); notice("Fix the validation errors below before running.");
      byId("validation").scrollIntoView({block:"nearest"}); return;
    }
    const data = {path:runSetup.team, rehearse:byId("run-rehearse").checked, mode:byId("input-mode").value};
    if (rerunSource) data.rerun = rerunSource;
    else if (data.mode === "message") data.message = byId("run-message").value;
    else if (["file", "task"].includes(data.mode)) data.upload = await upload(byId("run-file").files[0]);
    const job = await api("/api/start", data);
    byId("run-dialog").close(); runsView(runSetup.team); connectJob(job);
  } catch (error) { byId("run-form-error").textContent = error.message; }
  finally { byId("start-run").disabled = false; }
});
function updateLiveStatus(state) {
  liveJob = state;
  byId("live-title").textContent = `${state.rehearse ? "Rehearsal" : "Run"} · ${state.name || runSetup?.name || state.team || "."}`;
  const who = state.speaking ? ` · current turn: ${state.speaking.member}${state.speaking.stage ? " · " + state.speaking.stage : ""}` : "";
  byId("live-status").textContent = `${state.state}${who}${state.reason ? " · " + state.reason : ""}`;
  byId("stop-run").disabled = state.done || state.state === "stopping";
  byId("stop-run").textContent = state.state === "stopping" ? "Stopping…" : "Stop run";
  byId("browse-finished").hidden = !state.done || !state.folder;
  byId("live-command").textContent = state.command;
  const problems = state.check?.problems || [];
  byId("live-check").hidden = !problems.length;
  byId("live-check").textContent = problems.map(p => `${p.what} · ${p.who.join(", ")}: ${p.wrong}\n${p.fix}`).join("\n\n");
}
function renderLive() {
  if (liveRender !== null) cancelAnimationFrame(liveRender);
  liveRender = null;
  const target = byId("live-events"), nearEnd = target.scrollHeight - target.scrollTop - target.clientHeight < 100;
  const previousCount = liveConversation?.count || 0;
  liveConversation = renderConversation(liveData, target, !liveJob?.done, liveConversation);
  if (nearEnd) target.scrollTop = target.scrollHeight;
  for (const event of liveData.events.slice(previousCount)) {
    if (["agent_message", "retry", "tool_call"].includes(event.event)) {
      if (typeof event.cost_usd === "number") liveTotals.known += event.cost_usd;
      else if (event.event !== "tool_call") liveTotals.unknown = true;
    }
    if (event.event === "agent_message") liveTotals.turns++;
  }
  const {known, unknown, turns} = liveTotals;
  byId("live-cost").textContent = `${turns} recorded turns · ${unknown ? "known cost" : "recorded cost"} ≈ ${cost(known)}${unknown ? " · some costs not reported" : ""}`;
}
function connectJob(job) {
  if (eventSource) eventSource.close();
  if (liveRender !== null) cancelAnimationFrame(liveRender);
  liveConversation = null; liveTotals = {known:0, unknown:false, turns:0};
  liveJob = job; liveData = {events:[], members:[], artifacts:[], raw_files:[]}; seenEvents = new Set();
  byId("live-run").hidden = false; byId("live-connection").textContent = "Connecting to the run log…";
  updateLiveStatus(job); renderLive();
  eventSource = new EventSource("/api/live-events?" + new URLSearchParams({id:job.id, token}));
  eventSource.addEventListener("status", event => { updateLiveStatus(JSON.parse(event.data)); byId("live-connection").textContent = "Live log connected."; });
  eventSource.addEventListener("log", event => {
    const value = JSON.parse(event.data); if (seenEvents.has(value.seq)) return;
    seenEvents.add(value.seq); liveData.events.push(value);
    if (value.event === "run_started") liveData.members = Array.isArray(value.members) ? value.members : [];
    if (!liveRender) liveRender = requestAnimationFrame(renderLive);
  });
  eventSource.addEventListener("log-warning", event => { byId("live-connection").textContent = JSON.parse(event.data).message; });
  eventSource.addEventListener("done", event => {
    updateLiveStatus(JSON.parse(event.data)); renderLive(); eventSource.close();
    byId("live-connection").textContent = "The run finished. Its files stay in the run folder.";
    loadRuns(); refreshTree();
  });
  eventSource.onerror = () => { if (!liveJob.done) byId("live-connection").textContent = "Connection interrupted; reconnecting. The run continues on your computer."; };
}
byId("stop-run").addEventListener("click", async () => {
  if (!liveJob) return;
  try { updateLiveStatus(await api("/api/stop", {id:liveJob.id})); }
  catch (error) { byId("live-connection").textContent = error.message; }
});
byId("browse-finished").addEventListener("click", () => { byId("live-run").hidden = true; openRun(liveJob.folder); });
byId("run-team").addEventListener("click", () => showRunForm());
byId("run-from-history").addEventListener("click", () => showRunForm());
byId("rerun").addEventListener("click", () => showRunForm(true));
byId("cancel-run-form").addEventListener("click", () => byId("run-dialog").close());
for (const id of ["input-mode", "run-rehearse", "run-file"]) byId(id).addEventListener("change", updateRunForm);
byId("run-message").addEventListener("input", updateRunForm);
workspaceReady.then(() => api("/api/job")).then(job => { if (job.active !== null) { runsView(job.team); connectJob(job); } }).catch(() => {});
