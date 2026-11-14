# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright 2026 Kumar Muthuraman
"""Single-instance demo gateway. Python standard library; no prompt/key logging.

Bind only to loopback behind Caddy. SQLite quotas survive restarts and count
attempts, including failed upstream requests, to bound retry-driven spending.
"""
from __future__ import annotations

import ipaddress
import json
import math
import os
import re
import sqlite3
import threading
import time
import urllib.error
import urllib.request
from email.utils import parsedate_to_datetime
from dataclasses import dataclass
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer


@dataclass
class Config:
    key: str
    model: str = "qwen/qwen3.7-flash"
    db: str = "/data/quotas.sqlite3"
    ip_minute: int = 30
    ip_day: int = 100
    global_day: int = 1000
    global_month: int = 10000
    max_output: int = 512
    max_prompt_bytes: int = 16000
    max_body: int = 65536
    concurrency: int = 4
    enabled: bool = True
    structured_output: str = "json_object"
    # The version check (D148, D149): its own host name, so it can move by DNS alone.
    version_domain: str = "version.teamrunner.ai"
    version_file: str = "/srv/version.json"
    version_log: str = "/logs/version-checks.jsonl"
    # The oldest TeamRunner the demo answers; "0" answers every version (D149).
    min_version_for_demo: str = "0"

    @classmethod
    def from_env(cls):
        c = cls(key=os.environ.get("OPENROUTER_API_KEY", ""),
                model=os.environ.get("DEMO_MODEL", "qwen/qwen3.7-flash"),
                db=os.environ.get("DEMO_QUOTA_DB", "/data/quotas.sqlite3"),
                structured_output=os.environ.get("DEMO_STRUCTURED_OUTPUT", "json_object"),
                enabled=os.environ.get("DEMO_ENABLED", "true").lower() == "true",
                version_domain=os.environ.get("VERSION_DOMAIN", "version.teamrunner.ai").lower(),
                version_file=os.environ.get("VERSION_FILE", "/srv/version.json"),
                version_log=os.environ.get("VERSION_LOG", "/logs/version-checks.jsonl"),
                min_version_for_demo=os.environ.get("MIN_VERSION_FOR_DEMO", "0").strip())
        if not VERSION.fullmatch(c.min_version_for_demo):
            raise ValueError("MIN_VERSION_FOR_DEMO must be a version such as 0.0.6, or 0 for none")
        if c.structured_output not in {"json_object", "json_schema"}:
            raise ValueError("DEMO_STRUCTURED_OUTPUT must be json_object or json_schema")
        for field in ("ip_minute", "ip_day", "global_day", "global_month", "max_output",
                      "max_prompt_bytes", "max_body", "concurrency"):
            value = int(os.environ.get("DEMO_" + field.upper(), getattr(c, field)))
            if value < 1:
                raise ValueError("DEMO_" + field.upper() + " must be positive")
            setattr(c, field, value)
        if not c.key or (c.enabled and c.key == "replace-on-server"):
            raise ValueError("set OPENROUTER_API_KEY on the server before starting")
        return c


VERSION = re.compile(r"\d+(\.\d+){0,5}")
# What TeamRunner sends: `TeamRunner/<version> (python <major.minor>; <platform>)`.
USER_AGENT = re.compile(r"TeamRunner/(\d+(?:\.\d+){0,5}(?:[a-z]+\d{0,4})?(?:\.dev\d{0,4})?) "
                        r"\(python (\d{1,2}\.\d{1,3}); ([a-z][a-z0-9_-]{0,31})\)")
INSTALL = re.compile(r"[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}")


def version_tuple(text: str) -> tuple[int, ...]:
    found = VERSION.match(text or "")
    parts = tuple(int(p) for p in found.group().split(".")) if found else ()
    return parts + (0,) * (6 - len(parts))


def too_old_for_demo(user_agent: str | None, c: Config) -> bool:
    if not any(version_tuple(c.min_version_for_demo)):
        return False  # no floor set
    found = USER_AGENT.fullmatch(user_agent or "")
    return not found or version_tuple(found.group(1)) < version_tuple(c.min_version_for_demo)


def version_check_line(user_agent: str | None, install: str | None, now: float | None = None) -> dict | None:
    """One log line from the two headers, or None if either is malformed. Anyone can call
    this address, so only fields that match TeamRunner's own form are kept. Never the IP."""
    found = USER_AGENT.fullmatch(user_agent or "")
    install = (install or "").strip()
    if not found or not INSTALL.fullmatch(install):
        return None
    when = datetime.fromtimestamp(time.time() if now is None else now, timezone.utc)
    return {"time": when.strftime("%Y-%m-%dT%H:%M:%SZ"), "version": found.group(1),
            "python": found.group(2), "platform": found.group(3), "install": install}


def client_ip(peer: str, forwarded: str | None) -> str:
    address = ipaddress.ip_address(peer)
    # Caddy is the only network entry point and overwrites this header. Never
    # trust a forwarded header received from a public client directly.
    if address.is_loopback and forwarded:
        address = ipaddress.ip_address(forwarded.strip())
    if isinstance(address, ipaddress.IPv6Address) and address.ipv4_mapped:
        address = address.ipv4_mapped
    # Rotating IPv6 privacy addresses should not bypass an allowance.
    if address.version == 6:
        return str(ipaddress.ip_network(f"{address}/64", strict=False))
    return str(address)


class Quotas:
    def __init__(self, config: Config):
        self.config = config
        with sqlite3.connect(config.db) as db:
            db.execute("PRAGMA journal_mode=WAL")
            db.execute("CREATE TABLE IF NOT EXISTS quota (scope TEXT, subject TEXT, window TEXT, "
                       "used INTEGER NOT NULL, PRIMARY KEY (scope, subject, window))")

    def take(self, ip: str, now: float | None = None) -> bool:
        c = self.config
        now = time.time() if now is None else now
        date = datetime.fromtimestamp(now, timezone.utc)
        day, month = date.strftime("%Y-%m-%d"), date.strftime("%Y-%m")
        limits = [("minute", ip, str(int(now // 60)), c.ip_minute),
                  ("day", ip, day, c.ip_day), ("global_day", "*", day, c.global_day),
                  ("month", "*", month, c.global_month)]
        with sqlite3.connect(c.db, timeout=5) as db:
            db.execute("BEGIN IMMEDIATE")
            # Expire old IP records; keep only current UTC windows.
            db.execute("DELETE FROM quota WHERE (scope='minute' AND CAST(window AS INTEGER)<?) OR "
                       "(scope IN ('day','global_day') AND window<?) OR "
                       "(scope='month' AND window<?)", (int(now // 60), day, month))
            for scope, subject, window, limit in limits:
                row = db.execute("SELECT used FROM quota WHERE scope=? AND subject=? AND window=?",
                                 (scope, subject, window)).fetchone()
                if row and row[0] >= limit:
                    return False
            for scope, subject, window, _ in limits:
                db.execute("INSERT INTO quota VALUES (?, ?, ?, 1) ON CONFLICT(scope,subject,window) "
                           "DO UPDATE SET used=used+1", (scope, subject, window))
        return True


def payload(data: dict, c: Config) -> dict:
    allowed = {"model", "messages", "temperature", "top_p", "max_tokens", "seed", "response_format"}
    if not isinstance(data, dict) or set(data) - allowed:
        raise ValueError("the demo accepts text messages and basic generation settings only; no tools or streaming")
    if data.get("model") not in {"demoLLM", "teamrunner/demoLLM"}:
        raise ValueError("the only public model is demoLLM")
    messages = data.get("messages")
    if not isinstance(messages, list) or not 1 <= len(messages) <= 64:
        raise ValueError("send between 1 and 64 messages")
    for m in messages:
        if (not isinstance(m, dict) or set(m) != {"role", "content"}
                or m["role"] not in {"system", "user", "assistant"} or not isinstance(m["content"], str)):
            raise ValueError("the demo supports system, user and assistant text messages only")
    if len(json.dumps(messages, ensure_ascii=False).encode()) > c.max_prompt_bytes:
        raise ValueError("this conversation is too long for the free demo; use your own model for larger teams")
    for name, low, high in (("temperature", 0, 2), ("top_p", 0, 1)):
        if name in data:
            value = data[name]
            if isinstance(value, bool) or not isinstance(value, (float, int)) or not math.isfinite(value) or not low <= value <= high:
                raise ValueError(f"{name} must be between {low} and {high}")
    for name in ("seed", "max_tokens"):
        if name in data and (isinstance(data[name], bool) or not isinstance(data[name], int)):
            raise ValueError(f"{name} must be an integer")
    if data.get("max_tokens", 1) < 1:
        raise ValueError("max_tokens must be positive")
    result = {"model": c.model, "messages": messages,
              "max_tokens": min(data.get("max_tokens", c.max_output), c.max_output),
              "reasoning": {"enabled": False}, "provider": {"require_parameters": True}}
    for name in ("temperature", "top_p", "seed"):
        if name in data:
            result[name] = data[name]
    if "response_format" in data:
        fmt = data["response_format"]
        if (not isinstance(fmt, dict) or fmt.get("type") != "json_schema"
                or not isinstance(fmt.get("json_schema"), dict)
                or not isinstance(fmt["json_schema"].get("schema"), dict)):
            raise ValueError("response_format must contain a JSON schema")
        # Schemas stay bounded by the request-body limit; no user-controlled URLs/plugins.
        result["response_format"] = {"type": "json_schema", "json_schema": {
            "name": "turn", "strict": True, "schema": fmt["json_schema"]["schema"]}}
        if c.structured_output == "json_object":
            # Qwen3.7 Flash's OpenRouter endpoint accepts JSON mode, but does not
            # advertise strict json_schema. TeamRunner still validates every field
            # and retries invalid replies in its normal turn handling.
            result["response_format"] = {"type": "json_object"}
            result["messages"] = [*messages, {"role": "user", "content":
                "Return only a JSON object, without Markdown fences, matching this JSON Schema: "
                + json.dumps(fmt["json_schema"]["schema"])}]
            if len(json.dumps(result["messages"], ensure_ascii=False).encode()) > c.max_prompt_bytes:
                raise ValueError("the conversation and answer schema are too long for the free demo")
    return result


def upstream(body: dict, c: Config) -> dict:
    req = urllib.request.Request("https://openrouter.ai/api/v1/chat/completions",
        data=json.dumps(body).encode(), headers={"Authorization": "Bearer " + c.key,
        "Content-Type": "application/json", "X-Title": "TeamRunner Demo"}, method="POST")
    with urllib.request.urlopen(req, timeout=20) as response:
        return json.load(response)


class AllowanceReached(Exception):
    pass


class Gateway(ThreadingHTTPServer):
    daemon_threads = True

    def __init__(self, address, config: Config, call=upstream):
        self.config, self.call = config, call
        self.pause = time.sleep
        self.quotas, self.slots = Quotas(config), threading.BoundedSemaphore(config.concurrency)
        self.log_lock = threading.Lock()
        super().__init__(address, Handler)

    def completion(self, body: dict, ip: str) -> dict:
        # Absorb short upstream rate limits without marking successful turns as
        # client failures. Each extra attempt consumes the same persistent caps.
        # Three 20-second attempts plus at most 10 seconds of waiting fit under
        # Caddy's 70-second and the client's 75-second timeouts.
        deadline = time.monotonic() + 68
        for attempt in range(3):
            if attempt and not self.quotas.take(ip):
                raise AllowanceReached
            try:
                return self.call(body, self.config)
            except urllib.error.HTTPError as e:
                if e.code != 429 or attempt == 2:
                    raise
                delay = 1.0 * (2 ** attempt)
                retry = e.headers.get("Retry-After") if e.headers else None
                if retry:
                    try:
                        delay = float(retry)
                    except ValueError:
                        try:
                            delay = parsedate_to_datetime(retry).timestamp() - time.time()
                        except (ValueError, TypeError, OverflowError):
                            pass
                if not math.isfinite(delay) or delay > 5 or time.monotonic() + max(delay, 0) + 20 > deadline:
                    raise  # Never ignore a provider asking for a longer pause.
                e.close()
                print("OpenRouter rate limit; retrying within gateway", flush=True)
                self.pause(max(delay, 0))


class Handler(BaseHTTPRequestHandler):
    def setup(self):
        super().setup()
        self.connection.settimeout(15)

    def log_message(self, *args):
        pass  # No IPs, prompts, response bodies or secrets in access logs.

    def send_json(self, status, data, retry=None):
        raw = json.dumps(data).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(raw)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("Connection", "close")
        if retry:
            self.send_header("Retry-After", str(retry))
        self.end_headers()
        self.wfile.write(raw)

    def error(self, status, message, retry=None):
        self.send_json(status, {"error": {"message": message}}, retry)

    def version_file(self):
        c = self.server.config
        try:
            with open(c.version_file, "rb") as f:
                raw = f.read(65536)
            json.loads(raw)
        except (OSError, ValueError):
            return self.error(503, "the version file is unavailable")
        line = version_check_line(self.headers.get("User-Agent"), self.headers.get("X-TeamRunner-Install"))
        if line:
            try:
                with self.server.log_lock, open(c.version_log, "a", encoding="utf-8") as log:
                    log.write(json.dumps(line) + "\n")
            except OSError:
                print("version log could not be written", flush=True)
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(raw)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("Connection", "close")
        self.end_headers()
        self.wfile.write(raw)

    def on_version_host(self) -> bool:
        host = (self.headers.get("Host") or "").strip().lower()
        if host.startswith("["):
            host = host.split("]")[0] + "]"
        else:
            host = host.split(":")[0]
        return host == self.server.config.version_domain

    def do_GET(self):
        if self.on_version_host():
            if self.path == "/version.json":
                return self.version_file()
            return self.error(404, "not found")
        if self.path == "/healthz":
            return self.send_json(200, {"status": "ok", "enabled": self.server.config.enabled})
        if self.path == "/v1/models":
            if not self.server.config.enabled:
                return self.error(503, "the demo is temporarily paused")
            return self.send_json(200, {"object": "list", "data": [{"id": "demoLLM", "object": "model"}]})
        self.error(404, "not found")

    def do_POST(self):
        c = self.server.config
        if self.path != "/v1/chat/completions" or self.on_version_host():
            return self.error(404, "not found")
        if not c.enabled:
            return self.error(503, "the demo is temporarily paused")
        if too_old_for_demo(self.headers.get("User-Agent"), c):
            return self.error(426, f"the demo needs TeamRunner {c.min_version_for_demo} or later; "
                                   "update with: pip install -U teamrunner")
        if self.headers.get("Transfer-Encoding") or len(self.headers.get_all("Content-Length", [])) != 1:
            return self.error(400, "send a single Content-Length header")
        try:
            size = int(self.headers.get("Content-Length", "0"))
        except ValueError:
            return self.error(400, "invalid content length")
        if not 0 < size <= c.max_body:
            return self.error(413, "this request is too large for the free demo")
        if not self.server.slots.acquire(blocking=False):
            return self.error(503, "the demo is busy; try again shortly", 10)
        try:
            try:
                raw = self.rfile.read(size)
                if len(raw) != size:
                    return self.error(400, "incomplete request")
                body = payload(json.loads(raw), c)
                ip = client_ip(self.client_address[0], self.headers.get("X-Forwarded-For"))
            except (ValueError, TypeError, UnicodeError, RecursionError) as e:
                return self.error(400, str(e)[:200])
            if not self.server.quotas.take(ip):
                return self.error(429, "the demo allowance has been reached; try later or use your own model", 60)
            try:
                data = self.server.completion(body, ip)
                choice = data["choices"][0]
                text = choice["message"]["content"]
                if not isinstance(text, str) or not text.strip() or choice.get("finish_reason") != "stop":
                    return self.error(502, "the model did not finish its answer; try a shorter prompt")
                usage = data.get("usage") or {}
                public = {"id": data.get("id"), "object": "chat.completion",
                    "model": data.get("model") or c.model,
                    "choices": [{"index": 0, "message": {"role": "assistant", "content": text},
                                 "finish_reason": "stop"}],
                    "usage": {k: usage.get(k, 0) for k in ("prompt_tokens", "completion_tokens", "total_tokens")}}
            except AllowanceReached:
                return self.error(429, "the demo allowance has been reached; try later or use your own model", 60)
            except urllib.error.HTTPError as e:
                print(f"OpenRouter HTTP {e.code}", flush=True)
                if e.code == 429:
                    return self.error(503, "the demo model is temporarily busy", 15)
                return self.error(502, "the model provider is unavailable; try again later", 15)
            except (urllib.error.URLError, TimeoutError, ValueError, KeyError, IndexError, TypeError):
                return self.error(502, "the model provider could not complete this request")
            self.send_json(200, public)
        except sqlite3.Error:
            self.error(503, "the demo allowance service is unavailable")  # fail closed
        finally:
            self.server.slots.release()


if __name__ == "__main__":
    config = Config.from_env()
    server = Gateway(("127.0.0.1", 8080), config)
    print(f"TeamRunner demo listening on 127.0.0.1:8080; model={config.model}", flush=True)
    server.serve_forever()
