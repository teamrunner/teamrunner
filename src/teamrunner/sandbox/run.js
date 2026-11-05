// run_code's sandbox (D114): Python in WebAssembly (Pyodide), run by Deno.
//
// Reads one JSON request on stdin: {code, files: {path: base64}, network}.
// Writes one JSON answer on stdout: {stdout, stderr, error, files, python}.
// The files under /work when the code ends are handed back; nothing else
// the code writes leaves the sandbox. Unless `network` is true, the
// internet is switched off before the code runs.

import { loadPyodide } from "npm:pyodide@314.0.7";

const request = JSON.parse(await new Response(Deno.stdin.readable).text());
const answer = { stdout: "", stderr: "", error: null, files: {}, python: null };
const LIMIT = 1_000_000; // characters of output kept

const pyodide = await loadPyodide();
answer.python = pyodide.runPython("import sys; sys.version.split()[0]");

const fs = pyodide.FS;
for (const [path, data] of Object.entries(request.files)) {
  fs.mkdirTree(path.slice(0, path.lastIndexOf("/")) || "/");
  fs.writeFile(path, Uint8Array.from(atob(data), (c) => c.charCodeAt(0)));
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
// environment; the internet only if the toolkeeper allows it.
for (const name of ["read", "write", "env"]) {
  await Deno.permissions.revoke({ name });
}
if (!request.network) {
  await Deno.permissions.revoke({ name: "net" });
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
      const bytes = fs.readFile(path);
      let text = "";
      for (let i = 0; i < bytes.length; i += 0x8000) {
        text += String.fromCharCode(...bytes.subarray(i, i + 0x8000));
      }
      answer.files[path.slice("/work/".length)] = btoa(text);
    }
  }
}
collect("/work");
await Deno.stdout.write(new TextEncoder().encode(JSON.stringify(answer)));
