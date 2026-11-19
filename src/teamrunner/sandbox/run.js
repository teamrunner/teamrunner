// SPDX-License-Identifier: GPL-3.0-or-later
// Copyright 2026 Kumar Muthuraman
// run_code's sandbox (D114): Python in WebAssembly (Pyodide), run by Deno.
//
// Talks to TeamRunner in lines on stdin and stdout. The first line in is the
// request, {code, files: {path: base64}, network}; the last line out is the
// answer, {stdout, stderr, error, files, python}. Each line out starts with
// MARK, so nothing else Deno or Pyodide might print is taken for one.
// The files under /work when the code ends are handed back; nothing else
// the code writes leaves the sandbox.
//
// The internet is switched off before the code runs, `network` or not. With
// `network: true` the code's fetch() becomes a request to TeamRunner, which
// fetches the page itself and refuses this computer and its local network
// (review 2026-11-16, S6): the code has no network of its own to get round it.

import { loadPyodide } from "npm:pyodide@314.0.7";

const MARK = "\x1eteamrunner ";
const encoder = new TextEncoder();
const out = Deno.stdout.writable.getWriter();
async function send(message) {
  await out.write(encoder.encode(MARK + JSON.stringify(message) + "\n"));
}

const reader = Deno.stdin.readable.pipeThrough(new TextDecoderStream()).getReader();
let buffered = "";
async function nextLine() {
  while (true) {
    const end = buffered.indexOf("\n");
    if (end >= 0) {
      const line = buffered.slice(0, end);
      buffered = buffered.slice(end + 1);
      return line;
    }
    const { value, done } = await reader.read();
    if (done) {
      const rest = buffered;
      buffered = "";
      return rest || null;
    }
    buffered += value;
  }
}

function toBase64(bytes) {
  let text = "";
  for (let i = 0; i < bytes.length; i += 0x8000) {
    text += String.fromCharCode(...bytes.subarray(i, i + 0x8000));
  }
  return btoa(text);
}

const fromBase64 = (data) => Uint8Array.from(atob(data), (c) => c.charCodeAt(0));

const request = JSON.parse(await nextLine());

// Replies to the code's fetches, by id, in whatever order TeamRunner sends them.
const waiting = new Map();
(async () => {
  let line;
  while ((line = await nextLine()) !== null) {
    if (!line) continue;
    const reply = JSON.parse(line);
    const done = waiting.get(reply.id);
    if (done) {
      waiting.delete(reply.id);
      done(reply);
    }
  }
})();

let fetches = 0;
async function fetchThroughTeamRunner(input, init) {
  const asked = new Request(input, init);
  const body = asked.body ? new Uint8Array(await asked.arrayBuffer()) : null;
  const id = ++fetches;
  const reply = new Promise((resolve) => waiting.set(id, resolve));
  await send({ fetch: { id, url: asked.url, method: asked.method, headers: [...asked.headers],
                        body: body ? toBase64(body) : null } });
  const got = await reply;
  if (got.error) throw new TypeError(got.error);
  const empty = [101, 204, 205, 304].includes(got.status);
  const response = new Response(empty ? null : fromBase64(got.body), {
    status: got.status, statusText: got.reason, headers: got.headers });
  Object.defineProperty(response, "url", { value: got.url });
  Object.defineProperty(response, "redirected", { value: got.redirected });
  return response;
}
const answer = { stdout: "", stderr: "", error: null, files: {}, python: null };
const LIMIT = 1_000_000; // characters of output kept

const pyodide = await loadPyodide();
answer.python = pyodide.runPython("import sys; sys.version.split()[0]");

const fs = pyodide.FS;
for (const [path, data] of Object.entries(request.files)) {
  fs.mkdirTree(path.slice(0, path.lastIndexOf("/")) || "/");
  fs.writeFile(path, fromBase64(data));
}
fs.mkdirTree("/work");
fs.chdir("/work");

// Packages the code imports load now, while the package site can still be reached.
try {
  await pyodide.loadPackagesFromImports(request.code, { messageCallback: () => {} });
} catch (e) {
  answer.stderr += `could not load a package: ${e.message}\n`;
}
// From here on the code runs. It gets no disk at all (its files live in Pyodide's own
// memory), so it cannot change the cached Pyodide that later runs load, and no
// environment, and no network: with `network: true`, its fetch() asks TeamRunner.
for (const name of ["read", "write", "env", "net"]) {
  await Deno.permissions.revoke({ name });
}
if (request.network) {
  globalThis.fetch = fetchThroughTeamRunner;
}
pyodide.setStdout({ batched: (s) => { if (answer.stdout.length < LIMIT) answer.stdout += s + "\n"; } });
pyodide.setStderr({ batched: (s) => { if (answer.stderr.length < LIMIT) answer.stderr += s + "\n"; } });

try {
  await pyodide.runPythonAsync(request.code);
} catch (e) {
  answer.error = String(e.message ?? e);
}

function collect(dir) {
  for (const name of fs.readdir(dir)) {
    if (name === "." || name === "..") continue;
    const path = `${dir}/${name}`;
    const stat = fs.stat(path);
    if (fs.isDir(stat.mode)) {
      collect(path);
    } else {
      answer.files[path.slice("/work/".length)] = toBase64(fs.readFile(path));
    }
  }
}
collect("/work");
await send({ answer });
await out.close();
Deno.exit(0); // the reader of stdin is still waiting for lines
