// SPDX-License-Identifier: GPL-3.0-or-later
// Copyright 2026 Kumar Muthuraman
"use strict";

// The run timeline (one run) and the run wall (many runs). Both are drawn from the digest
// the server makes of a run's log (runviews.digest): one row per turn. Nothing here names a
// field, a member or a team, so a new team needs no change to this file.
//   A cell is one turn. Its colour is the value of the chosen output; grey means the turn
//   gave no value for it; a dashed cell said nothing. The wall's thumbnails use the same
//   colours, so a run looks the same in both places.
const SVG_NS = "http://www.w3.org/2000/svg";
function shape(tag, attrs = {}, text) {
  const item = document.createElementNS(SVG_NS, tag);
  for (const [key, value] of Object.entries(attrs)) item.setAttribute(key, value);
  if (text !== undefined) item.textContent = text;
  return item;
}

// One colour per distinct value, in the order the values were first seen for that team and
// output. The wall names the commonest first; a run opened afterwards keeps those colours.
const seenValues = new Map();
function outputColour(scope, field, value) {
  const key = scope + "\n" + field;
  if (!seenValues.has(key)) seenValues.set(key, []);
  const values = seenValues.get(key);
  let at = values.indexOf(value);
  if (at < 0) at = values.push(value) - 1;
  return at < 5 ? `var(--out${at + 1})` : "var(--out0)";
}
// The outputs these runs declared, most used first. One whose every value is long text
// cannot be drawn as a colour.
function outputFields(digests) {
  const found = new Map();
  const field = name => { if (!found.has(name)) found.set(name, {name, turns:0, values:new Map()}); return found.get(name); };
  for (const turns of digests) for (const turn of turns) {
    for (const [name, value] of Object.entries(turn.outputs)) { const f = field(name); f.turns++; f.values.set(value, (f.values.get(value) || 0) + 1); }
    for (const name of turn.long) field(name);
  }
  return [...found.values()].sort((a, b) => b.turns - a.turns);
}
const chosenOutput = new Map();  // team -> the output the reader last chose ("" for none)
function outputFor(scope, fields) {
  const kept = chosenOutput.get(scope);
  if (kept === "") return null;
  if (fields.some(f => f.name === kept && f.values.size)) return kept;
  return fields.find(f => f.values.size)?.name ?? null;
}
function choices(label, items, current, pick) {
  const box = node("span", undefined, "picture-choice");
  box.append(node("span", label, "bar-label"));
  for (const [key, text, off, why] of items) {
    const button = node("button", text); button.type = "button";
    button.setAttribute("aria-pressed", String(key === current)); button.disabled = !!off;
    if (why) button.title = why;
    button.addEventListener("click", () => pick(key)); box.append(button);
  }
  return box;
}
function valueKey(scope, field) {
  const key = node("span", undefined, "picture-key"), values = seenValues.get(scope + "\n" + field.name) || [];
  for (const value of values.filter(v => field.values.has(v))) {
    const item = node("span"), swatch = node("i"); swatch.style.background = outputColour(scope, field.name, value);
    item.append(swatch, `${value === "" ? "(empty)" : value} ×${field.values.get(value)}`); key.append(item);
  }
  return key;
}
const refused = turn => turn.tools.some(call => call.access && call.access !== "allowed" && call.access !== "retry");
const faulty = turns => turns.some(turn => refused(turn) || turn.route?.limit);
const lastValue = (turns, field) => { let value = null; for (const turn of turns) if (field in turn.outputs) value = turn.outputs[field]; return value; };
const membersOf = digests => { const names = []; for (const turns of digests) for (const turn of turns) if (!names.includes(turn.member)) names.push(turn.member); return names; };

// ---- The run wall ----
function runThumb(turns, members, scope, field, fault, narrow) {
  const cw = narrow ? 7 : 9, ch = 6, width = Math.max(turns.length, 2) * cw, height = Math.max(members.length, 1) * ch;
  const svg = shape("svg", {width, height, viewBox:`0 0 ${width} ${height}`, "aria-hidden":"true"});
  svg.append(shape("rect", {x:0, y:0, width, height, class:"picture-ground"}));
  turns.forEach((turn, at) => {
    const y = members.indexOf(turn.member) * ch, has = field != null && field in turn.outputs;
    if (has) svg.append(shape("rect", {x:at * cw, y, width:cw - 1, height:ch - 1, fill:outputColour(scope, field, turn.outputs[field])}));
    else if (!turn.words) svg.append(shape("rect", {x:at * cw + .5, y:y + .5, width:cw - 2, height:ch - 2, class:"picture-silent"}));
    else svg.append(shape("rect", {x:at * cw, y, width:cw - 1, height:ch - 1, class:"picture-plain"}));
  });
  if (fault) svg.append(shape("path", {d:`M${width - 7} 0H${width}V7Z`, class:"picture-fault"}));
  return svg;
}
const wall = {order:"time"};
// `rows` are the runs as listed (runviews.listing with wall=1), or an experiment's runs with
// `group` (the condition) and `title` set; `open` opens one; `first` names the order given.
function showRunWall(host, rows, open, first = "newest first") {
  host.replaceChildren();
  const groups = new Map(), redraw = () => showRunWall(host, rows, open, first);
  // One team on the wall: its choices are shown once, above every group.
  const shared = new Set(rows.map(run => run.team)).size === 1;
  const controlsFor = (scope, fields, field) => {
    const controls = node("div", undefined, "picture-controls");
    if (fields.length) controls.append(choices("OUTPUT SHOWN", [...fields.map(f => [f.name, f.name, !f.values.size, f.values.size ? "" : "Long text is not drawn as a colour"]), ["", "none"]],
      field ?? "", key => { chosenOutput.set(scope, key); redraw(); }));
    controls.append(choices("ORDER", [["time", first], ["shape", "by shape"], ["value", "by output", !field]], wall.order, key => { wall.order = key; redraw(); }));
    return controls;
  };
  if (shared && rows.length) {
    const scope = rows[0].team, fields = outputFields(rows.map(run => run.digest)), field = outputFor(scope, fields), info = fields.find(f => f.name === field);
    if (info) for (const [value] of [...info.values].sort((a, b) => b[1] - a[1])) outputColour(scope, field, value);
    host.append(controlsFor(scope, fields, field));
  }
  for (const run of rows) {
    const name = run.group ?? run.team + (run.experiment?.condition ? ` · ${run.experiment.condition}` : "");
    if (!groups.has(name)) groups.set(name, {name, team:run.team, runs:[]});
    groups.get(name).runs.push(run);
  }
  const narrow = rows.some(run => run.digest.length > 12), labelled = rows.length <= 24;
  for (const group of groups.values()) {
    const scope = group.team, digests = group.runs.map(run => run.digest);
    const fields = outputFields(digests), field = outputFor(scope, fields), info = fields.find(f => f.name === field);
    // The commonest values take the first colours.
    if (info) for (const [value] of [...info.values].sort((a, b) => b[1] - a[1])) outputColour(scope, field, value);
    const members = membersOf(digests), box = node("section", undefined, "picture-group");
    const head = node("div", undefined, "picture-head");
    head.append(node("strong", group.name), node("span", `${group.runs.length} run${group.runs.length === 1 ? "" : "s"}`, "message-meta"));
    box.append(head);
    if (!shared) box.append(controlsFor(scope, fields, field));
    const lengths = new Map();
    for (const turns of digests) lengths.set(turns.length, (lengths.get(turns.length) || 0) + 1);
    const flagged = group.runs.filter(run => run.status !== "completed" || faulty(run.digest)).length;
    box.append(node("p", "Length: " + [...lengths].sort((a, b) => a[0] - b[0]).map(([n, count]) => `${n} turn${n === 1 ? "" : "s"} ×${count}`).join(", ")
      + (flagged ? ` · red corner: ${flagged}` : ""), "message-meta"));
    if (info) {
      const ends = new Map();
      for (const turns of digests) { const value = lastValue(turns, field); if (value != null) ends.set(value, (ends.get(value) || 0) + 1); }
      const line = node("p", undefined, "message-meta picture-tally");
      line.append(`Last ${field} in each run: `, valueKey(scope, {name:field, values:ends}));
      const none = group.runs.length - [...ends.values()].reduce((sum, n) => sum + n, 0);
      if (none) line.append(` none ×${none}`);
      box.append(line);
    }
    let runs = group.runs.slice();
    const path = run => run.digest.map(turn => turn.member).join(" ");
    if (wall.order === "shape") runs.sort((a, b) => a.digest.length - b.digest.length || path(a).localeCompare(path(b)));
    if (wall.order === "value" && field) {
      const rank = run => { const value = lastValue(run.digest, field), at = (seenValues.get(scope + "\n" + field) || []).indexOf(value); return value == null || at < 0 ? 999 : at; };
      runs.sort((a, b) => rank(a) - rank(b) || a.digest.length - b.digest.length);
    }
    const thumbs = node("div", undefined, "picture-thumbs");
    for (const run of runs) {
      const button = node("button", undefined, "picture-thumb"); button.type = "button";
      const slot = run.experiment ? `replicate ${run.experiment.replicate} · ` : "";
      button.title = run.title ?? `${slot}${run.when} · ${run.message || "no message"} · ${run.digest.length} turns · ${run.length} · ${run.status}${run.rehearsal ? " · rehearsal" : ""}`;
      button.setAttribute("aria-label", `Open this run: ${button.title}`);
      button.append(runThumb(run.digest, members, scope, field, run.status !== "completed" || faulty(run.digest), narrow));
      if (labelled) button.append(node("span", run.experiment ? `r${String(run.experiment.replicate).padStart(2, "0")}` : run.when));
      button.addEventListener("click", () => open(run.path)); thumbs.append(button);
    }
    box.append(thumbs); host.append(box);
  }
  if (!rows.length) return;
  host.append(node("p", "One small picture per run, as wide as the run is long: rows are members, columns are turns. A coloured cell is a turn that gave the chosen output; a grey one did not. A red corner marks a run that did not complete, had a tool call refused, or hit a loop's limit.", "message-meta picture-note"));
}

// ---- The run timeline ----
const BOTTOM = {words:["words written", v => Math.round(v).toLocaleString()], tokens_read:["tokens read", v => Math.round(v).toLocaleString()],
  cost:["cost", v => "≈ $" + v.toFixed(4)], ms:["time", v => (v / 1000).toFixed(1) + "s"]};
const timeline = {bottom:"words", turn:null, run:""};
// Long text shown short, with a button for the rest.
function expandable(text, limit) {
  const box = node("span");
  if (text.length <= limit) { box.textContent = text; return box; }
  const body = node("span", text.slice(0, limit) + "…"), toggle = node("button", "Show all", "picture-more");
  toggle.type = "button"; toggle.setAttribute("aria-expanded", "false");
  toggle.addEventListener("click", () => {
    const open = toggle.getAttribute("aria-expanded") !== "true";
    toggle.setAttribute("aria-expanded", String(open));
    body.textContent = open ? text : text.slice(0, limit) + "…"; toggle.textContent = open ? "Show less" : "Show all";
  });
  box.append(body, " ", toggle); return box;
}
// `data` is one run as /api/run gives it; `goTo(seq)` shows that message in the conversation.
function showRunTimeline(host, data, goTo) {
  const turns = data.digest || [], scope = data.summary.team;
  if (timeline.run !== data.summary.path) { timeline.run = data.summary.path; timeline.turn = null; }
  host.replaceChildren(); host.hidden = !turns.length;
  if (!turns.length) return;
  const redraw = () => showRunTimeline(host, data, goTo);
  const fields = outputFields([turns]), field = outputFor(scope, fields), info = fields.find(f => f.name === field);
  const members = membersOf([turns]), priced = turns.some(turn => turn.cost > 0);
  if (timeline.bottom === "cost" && !priced) timeline.bottom = "words";
  const controls = node("div", undefined, "picture-controls");
  if (fields.length) controls.append(choices("OUTPUT SHOWN", [...fields.map(f => [f.name, f.name, !f.values.size, f.values.size ? "" : "Long text is not drawn as a colour; choose a turn to read it"]), ["", "none"]],
    field ?? "", key => { chosenOutput.set(scope, key); redraw(); }));
  controls.append(choices("BOTTOM ROW", Object.entries(BOTTOM).map(([key, [name]]) => [key, name, key === "cost" && !priced, key === "cost" && !priced ? "No cost was recorded for this run" : ""]),
    timeline.bottom, key => { timeline.bottom = key; redraw(); }));
  host.append(controls);
  if (info) { const key = node("p", undefined, "message-meta picture-tally"); key.append(`${field}: `, valueKey(scope, info)); host.append(key); }

  const C = 26, G = 3, L = 104, T = 30, n = turns.length, rows = members.length;
  const width = L + n * (C + G) + 8, height = T + rows * (C + G) + 36;
  const svg = shape("svg", {width, height, viewBox:`0 0 ${width} ${height}`, role:"group", "aria-label":"Run timeline: members down, turns across"});
  const chosen = timeline.turn == null ? null : turns[timeline.turn];
  const readable = at => chosen && Array.isArray(chosen.read) && at < timeline.turn && chosen.read.includes(turns[at].seq);
  const unread = at => chosen && Array.isArray(chosen.read) && at < timeline.turn && !chosen.read.includes(turns[at].seq);
  members.forEach((member, row) => {
    const y = T + row * (C + G) + C / 2;
    svg.append(shape("text", {x:L - 10, y:y + 4, "text-anchor":"end", class:"picture-label"}, member.length > 13 ? member.slice(0, 12) + "…" : member));
    svg.append(shape("line", {x1:L, x2:width - 8, y1:y, y2:y, class:"picture-rule"}));
  });
  // Column groups: consecutive turns in the same stage or block, and round.
  const tag = turn => turn.group + (turn.round ? (turn.group ? " " : "round ") + turn.round : "");
  let start = 0;
  turns.forEach((turn, at) => {
    if (at + 1 < n && tag(turns[at + 1]) === tag(turn)) return;
    const x0 = L + start * (C + G), x1 = L + at * (C + G) + C, text = tag(turn), room = Math.floor((x1 - x0 + G) / 6.4);
    if (text) {
      svg.append(shape("line", {x1:x0, x2:x1, y1:T - 7, y2:T - 7, class:"picture-span"}));
      const label = shape("text", {x:x0, y:T - 12, class:"picture-small"}, text.length > room ? text.slice(0, Math.max(1, room - 1)) + "…" : text);
      label.append(shape("title", {}, text)); svg.append(label);
    }
    start = at + 1;
  });
  const key = timeline.bottom, top = Math.max(...turns.map(turn => turn[key] || 0), 1e-9);
  turns.forEach((turn, at) => {
    const x = L + at * (C + G), y = T + members.indexOf(turn.member) * (C + G), has = field != null && field in turn.outputs;
    const cell = shape("g", {tabindex:"0", role:"button", class:"picture-cell" + (unread(at) ? " unread" : ""),
      "aria-pressed":String(at === timeline.turn), "aria-label":`Turn ${at + 1}, ${turn.member}${has ? `, ${field} ${turn.outputs[field]}` : ""}`});
    cell.append(shape("rect", {x, y, width:C, height:C, rx:3, class:"picture-back"}));
    if (has) cell.append(shape("rect", {x, y, width:C, height:C, rx:3, fill:outputColour(scope, field, turn.outputs[field])}));
    else if (!turn.words) cell.append(shape("rect", {x:x + 1, y:y + 1, width:C - 2, height:C - 2, rx:3, class:"picture-silent"}));
    else cell.append(shape("rect", {x, y, width:C, height:C, rx:3, class:"picture-plain"}));
    turn.tools.forEach((call, i) => {
      const cx = x + 5 + i * 7, cy = y + 5;
      if (cx > x + C - 4) return;
      if (call.access === "retry") cell.append(shape("path", {d:`M${cx} ${cy - 3}l3 3l-3 3l-3-3Z`, class:"picture-mark"}));
      else cell.append(shape("circle", {cx, cy, r:2.6, class:call.access && call.access !== "allowed" ? "picture-mark refused" : "picture-mark"}));
    });
    if (turn.route) cell.append(shape("path", {d:`M${x + C - 9} ${y + 2}h7v7Z`, class:"picture-route" + (turn.route.limit ? " limit" : "")}));
    if (at === timeline.turn) cell.append(shape("rect", {x:x - 1.5, y:y - 1.5, width:C + 3, height:C + 3, rx:4, class:"picture-chosen"}));
    else if (readable(at)) cell.append(shape("rect", {x:x - .5, y:y - .5, width:C + 1, height:C + 1, rx:3, class:"picture-read"}));
    const pick = () => { timeline.turn = at === timeline.turn ? null : at; redraw(); host.querySelector(`.picture-cell[aria-pressed="true"]`)?.focus(); };
    cell.addEventListener("click", pick);
    cell.addEventListener("keydown", event => { if (event.key === "Enter" || event.key === " ") { event.preventDefault(); pick(); } });
    svg.append(cell);
    const bar = Math.max(1.5, 20 * (turn[key] || 0) / top), base = T + rows * (C + G) + 6;
    svg.append(shape("rect", {x:x + 4, y:base + 20 - bar, width:C - 8, height:bar, class:"picture-bar"}));
  });
  svg.append(shape("text", {x:L - 10, y:T + rows * (C + G) + 16, "text-anchor":"end", class:"picture-small"}, BOTTOM[key][0]));
  svg.append(shape("text", {x:L - 10, y:T + rows * (C + G) + 29, "text-anchor":"end", class:"picture-small"}, "max " + BOTTOM[key][1](Math.max(...turns.map(turn => turn[key] || 0)))));
  const scroll = node("div", undefined, "picture-scroll"); scroll.append(svg); host.append(scroll);

  const legend = node("p", undefined, "message-meta picture-legend");
  const item = (drawing, text) => { const part = node("span"), icon = shape("svg", {width:16, height:14, viewBox:"0 0 16 14", "aria-hidden":"true"}); icon.append(drawing); part.append(icon, text); legend.append(part); };
  if (field) item(shape("rect", {x:1, y:1, width:12, height:12, rx:2, class:"picture-plain"}), `no ${field} output`);
  item(shape("rect", {x:2, y:2, width:11, height:11, rx:2, class:"picture-silent"}), "said nothing");
  if (turns.some(turn => turn.tools.some(call => call.access !== "retry"))) item(shape("circle", {cx:7, cy:7, r:3, class:"picture-mark"}), "tool call");
  if (turns.some(refused)) item(shape("circle", {cx:7, cy:7, r:3, class:"picture-mark refused"}), "refused");
  if (turns.some(turn => turn.tools.some(call => call.access === "retry"))) item(shape("path", {d:"M7 3l4 4l-4 4l-4-4Z", class:"picture-mark"}), "retry");
  if (turns.some(turn => turn.route)) item(shape("path", {d:"M4 3h8v8Z", class:"picture-route"}), "route decision");
  if (turns.some(turn => turn.route?.limit)) item(shape("path", {d:"M4 3h8v8Z", class:"picture-route limit"}), "loop limit reached");
  host.append(legend);

  const detail = node("div", undefined, "picture-detail"); host.append(detail);
  if (!chosen) { detail.append(node("p", "Choose a turn to see what it could read, its outputs and what it cost.", "message-meta")); return; }
  const event = data.events.find(e => e && e.event === "agent_message" && e.seq === chosen.seq) || {};
  const head = node("p", undefined, "picture-turn");
  head.append(node("strong", `Turn ${timeline.turn + 1} · ${chosen.member}${tag(chosen) ? " · " + tag(chosen) : ""}`));
  const jump = node("button", "Show in the conversation", "picture-more"); jump.type = "button";
  jump.addEventListener("click", () => goTo(chosen.seq)); head.append(" ", jump); detail.append(head);
  const before = timeline.turn, could = Array.isArray(chosen.read) ? `could read ${turns.filter((_, at) => readable(at)).length} of ${before} earlier turns` : "what it could read was not recorded";
  detail.append(node("p", `${chosen.words.toLocaleString()} words written · ${chosen.tokens_read.toLocaleString()} tokens read · ${(chosen.ms / 1000).toFixed(1)}s${chosen.cost ? ` · ≈ $${chosen.cost.toFixed(4)}` : ""} · ${could}`, "message-meta"));
  const lines = node("dl", undefined, "picture-lines");
  const line = (name, body) => { const term = node("dt", name); lines.append(term, typeof body === "string" ? node("dd", body) : (() => { const dd = node("dd"); dd.append(body); return dd; })()); };
  for (const [name, value] of Object.entries(event.output && typeof event.output === "object" ? event.output : {})) line(`output (${name})`, expandable(pretty(value), 160));
  if (chosen.tools.length) line("tools", chosen.tools.map(call => call.access === "retry" ? "retry" : `${call.tool || "tool"}: ${call.access || "access not recorded"}`).join(" · "));
  if (chosen.route) line("route", `${chosen.route.field || "answer"} = ${pretty(chosen.route.option)} → ${pretty(chosen.route.to)}${chosen.route.limit ? " · loop limit reached" : ""}`);
  line("message", typeof event.content === "string" && event.content ? expandable(event.content, 280) : "(no message text)");
  detail.append(lines);
}
