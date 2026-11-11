// SPDX-License-Identifier: GPL-3.0-or-later
// Copyright 2026 Kumar Muthuraman
"use strict";
mermaid.initialize({startOnLoad:false,securityLevel:"strict",theme:"neutral",htmlLabels:false,flowchart:{useMaxWidth:false},maxTextSize:200000});
let diagramQueue=Promise.resolve(), diagramNumber=0;
function drawDiagram(container,source) {
  const request=String(++diagramNumber); container.dataset.diagram=request;
  container.replaceChildren(node("p","Drawing flow…"));
  diagramQueue=diagramQueue.then(async()=>{
    if(container.dataset.diagram!==request) return;
    if(!source || source.trim()==="flowchart TD") {container.replaceChildren(node("p","No flow steps were recorded.")); return;}
    try {
      const result=await mermaid.render("flow"+request,source);
      const svg=DOMPurify.sanitize(result.svg,{USE_PROFILES:{svg:true,svgFilters:true},FORBID_TAGS:["foreignObject","script","image","a","use"],RETURN_DOM_FRAGMENT:true});
      if(container.dataset.diagram===request) container.replaceChildren(svg);
    } catch(_) {if(container.dataset.diagram===request) container.replaceChildren(node("p","This diagram could not be drawn. The recorded arrow table remains available."));}
  }); return diagramQueue;
}
function renderPath(container,data) {
  container.replaceChildren(node("p",data.note,"message-meta"));
  const diagram=node("div",undefined,"flow-diagram"); container.append(diagram); drawDiagram(diagram,data.mermaid);
  const labels=new Map(data.graph.nodes.map(n=>[n.id,n.label])),table=node("table"),head=node("tr");
  for(const name of ["From","To","Route","Times"]) head.append(node("th",name)); table.append(head);
  for(const edge of data.graph.edges) {const row=node("tr"); for(const value of [labels.get(edge.source),labels.get(edge.target),(edge.labels||[]).join(" / "),String(edge.count)]) row.append(node("td",value)); table.append(row);}
  const details=node("details"); details.append(node("summary","Arrow counts"),table); container.append(details);
}
const comparedRuns=new Set();
function compareCheckbox(run) {
  const cell=node("td"),input=node("input"); input.type="checkbox"; input.setAttribute("aria-label",`Compare ${run.team}, ${run.when}`); input.checked=comparedRuns.has(run.path);
  input.addEventListener("change",()=>{
    if(input.checked&&comparedRuns.size>=2) {input.checked=false; runsNotice("Select two runs to compare. Clear a selection to choose another."); return;}
    if(input.checked) comparedRuns.add(run.path); else comparedRuns.delete(run.path);
    byId("compare-runs").disabled=comparedRuns.size!==2;
  }); cell.append(input); return cell;
}
byId("compare-runs").addEventListener("click",async()=>{
  try {
    const [left,right]=comparedRuns,data=await api("/api/compare?"+new URLSearchParams({left,right}));
    byId("runs-list-panel").hidden=true;
    const view=byId("run-comparison"); view.hidden=false; view.replaceChildren();
    const back=node("button","← Run history"); back.addEventListener("click",()=>{view.hidden=true; byId("runs-list-panel").hidden=false;}); view.append(back,node("h3","Compare runs"));
    const columns=node("div",undefined,"comparison-columns");
    for(const [run,path] of [[data.left,data.left_path],[data.right,data.right_path]]) {
      const column=node("section"),summary=run.summary;
      column.append(node("h4",`${summary.team} · ${summary.when}`),node("p",`${summary.status} · ${summary.length} · ≈ ${summary.cost}`),node("h4","Output"),markdown(run.output,run));
      const flow=node("section"); column.append(node("h4","Recorded path"),flow); columns.append(column); renderPath(flow,path);
    } view.append(columns,node("h3","Member versions"));
    for(const change of data.members) {
      const a=change.before,b=change.after,version=m=>m?`${m.name} · version ${m.version?.number??"unknown"}`:"absent",card=node("section",undefined,"record-member");
      card.append(node("h4",`${version(a)} → ${version(b)} · ${change.status}`));
      if(!change.exact_source) card.append(node("p","Exact member source was not recorded. Resolved prompts can include substituted inputs."),jsonDetails("Left recorded prompt",a?.prompt??"Not recorded"),jsonDetails("Right recorded prompt",b?.prompt??"Not recorded"));
      else if(change.diff) card.append(node("pre",change.diff,"source-diff")); view.append(card);
    } runsNotice("");
  } catch(error) {runsNotice(error.message);}
});
