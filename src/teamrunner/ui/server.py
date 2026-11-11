"""Authenticated, loopback-only HTTP server. No team files are served directly."""

from __future__ import annotations

import hmac
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
from pathlib import Path
import secrets
import subprocess
import sys
from threading import RLock
from urllib.parse import parse_qs, quote, urlsplit
import webbrowser

from . import api, diagrams, jobs, runviews, stream
from ..inputs import InputError
from ..new import NewError, examples

STATIC = Path(__file__).with_name("static")
ASSETS = {"/app.js": "text/javascript; charset=utf-8",
          "/app.css": "text/css; charset=utf-8",
          "/vendor/editor.js": "text/javascript; charset=utf-8",
          "/vendor/editor.css": "text/css; charset=utf-8",
          "/vendor/marked.js": "text/javascript; charset=utf-8",
          "/vendor/dompurify.js": "text/javascript; charset=utf-8",
          "/runs.js": "text/javascript; charset=utf-8"}
ASSETS["/vendor/mermaid.js"] = "text/javascript; charset=utf-8"
ASSETS["/diagrams.js"] = "text/javascript; charset=utf-8"
ASSETS["/run.js"] = "text/javascript; charset=utf-8"
ASSETS["/favicon.svg"] = "image/svg+xml"


class Server(ThreadingHTTPServer):
    daemon_threads = True

    def __init__(self, root: Path):
        self.root = root.expanduser().resolve(strict=True)
        if not self.root.is_dir():
            raise ValueError("Choose a folder to open.")
        self.token = secrets.token_urlsafe(32)
        self.files_lock = RLock()
        self.versions_cache = runviews.HistoryVersions()
        super().__init__(("127.0.0.1", 0), Handler)
        self.origin = f"http://127.0.0.1:{self.server_port}"
        self.url = f"{self.origin}/?token={self.token}"
        self.jobs = jobs.Jobs(self.root, self.files_lock)

    def server_close(self):
        if hasattr(self, "jobs"):
            self.jobs.close()
        super().server_close()


class Handler(BaseHTTPRequestHandler):
    server: Server

    def log_message(self, format, *args):
        pass  # Request URLs contain the access token; never log them.

    def send(self, status: int, data, content_type="application/json; charset=utf-8"):
        body = json.dumps(data, ensure_ascii=False).encode() if isinstance(data, dict) else data
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("Referrer-Policy", "no-referrer")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Content-Security-Policy", "default-src 'none'; script-src 'self'; "
                         "style-src 'self' 'unsafe-inline'; connect-src 'self'; img-src 'self'; "
                         "base-uri 'none'; frame-ancestors 'none'; form-action 'none'")
        self.send_header("Connection", "close")
        self.end_headers()
        self.wfile.write(body)

    def authorized(self, mutation=False):
        # Host checking also prevents a DNS-rebinding page from reaching this server.
        if self.headers.get("Host") != urlsplit(self.server.origin).netloc:
            self.send(403, {"error": "Open the address printed by teamrunner ui."})
            return False
        if (self.headers.get("Origin", self.server.origin) != self.server.origin
                or self.headers.get("Sec-Fetch-Site") == "cross-site"):
            self.send(403, {"error": "Open the address printed by teamrunner ui."})
            return False
        request = urlsplit(self.path)
        query = parse_qs(request.query)
        token = self.headers.get("X-TeamRunner-Token", "" if mutation else query.get("token", [""])[0])
        if not hmac.compare_digest(token.encode(), self.server.token.encode()):
            self.send(403, {"error": "Open the full address printed by teamrunner ui, including its token."})
            return False
        return True

    def do_GET(self):
        if not self.authorized():
            return
        request = urlsplit(self.path)
        query = parse_qs(request.query)
        if request.path in ("/api/live-events", "/api/job"):
            try:
                job_id = query.get("id", [None])[0]
                if request.path == "/api/live-events":
                    after = max(0, int(self.headers.get("Last-Event-ID", query.get("after", ["0"])[0])))
                    stream.events(self, job_id, after)
                else:
                    if job_id:
                        self.server.jobs.get(job_id)
                    self.send(200, self.server.jobs.status(job_id))
            except FileNotFoundError as error:
                self.send(404, {"error": str(error)})
            except ValueError:
                self.send(400, {"error": "Choose a run in this server session and a valid event number."})
            return
        if request.path not in ("/api/show", "/api/keys", "/api/explain", "/api/diagram", "/api/run-setup"):
            self.read_request()
            return
        with self.server.files_lock:
            self.read_request()

    def download(self, path: Path):
        with path.open("rb") as stream:
            self.send_response(200)
            self.send_header("Content-Type", "application/octet-stream")
            self.send_header("Content-Length", str(path.stat().st_size))
            self.send_header("Content-Disposition", "attachment; filename*=UTF-8''" + quote(path.name))
            self.send_header("Cache-Control", "no-store")
            self.send_header("X-Content-Type-Options", "nosniff")
            self.send_header("Referrer-Policy", "no-referrer")
            self.send_header("Content-Security-Policy", "default-src 'none'; sandbox")
            self.end_headers()
            while chunk := stream.read(65536):
                self.wfile.write(chunk)

    def read_request(self):
        request = urlsplit(self.path)
        query = parse_qs(request.query)
        try:
            if request.path == "/":
                page = (STATIC / "index.html").read_text(encoding="utf-8")
                self.send(200, page.replace("__TOKEN__", self.server.token).encode(),
                          "text/html; charset=utf-8")
            elif request.path in {"/legal.txt", "/license.txt"}:
                # Bundled in wheels; use the canonical root files in a source checkout.
                filename = {"/legal.txt": "LEGAL.md", "/license.txt": "LICENSE"}[request.path]
                document = (Path(__file__).parents[1] / "LEGAL.md"
                            if request.path == "/legal.txt" else STATIC / "license.txt")
                if not document.is_file():
                    document = Path(__file__).parents[3] / filename
                self.send(200, document.read_bytes(), "text/plain; charset=utf-8")
            elif request.path in ASSETS:
                self.send(200, (STATIC / request.path[1:]).read_bytes(), ASSETS[request.path])
            elif request.path == "/api/info":
                self.send(200, {"name": self.server.root.name, "folder": str(self.server.root),
                                "team": (self.server.root / "team.yaml").is_file(),
                                "examples": examples(), "feedback": feedback()})
            elif request.path == "/api/team-contents":
                self.send(200, api.team_contents(self.server.root, query.get("path", [""])[0]))
            elif request.path == "/api/files":
                self.send(200, api.files(self.server.root, query.get("path", [""])[0]))
            elif request.path == "/api/file":
                self.send(200, api.read_file(self.server.root, query.get("path", [""])[0]))
            elif request.path == "/api/show":
                self.send(200, api.show_member(self.server.root, query.get("path", [""])[0]))
            elif request.path == "/api/keys":
                self.send(200, api.key_status(self.server.root, query.get("path", [""])[0]))
            elif request.path == "/api/explain":
                self.send(200, api.explain_team(self.server.root, query.get("path", [""])[0]))
            elif request.path == "/api/diagram":
                self.send(200, diagrams.author(self.server.root, query.get("path", [""])[0]))
            elif request.path == "/api/compare":
                self.send(200, diagrams.compare(self.server.root, query.get("left", [""])[0], query.get("right", [""])[0], self.server.versions_cache))
            elif request.path == "/api/run-path":
                self.send(200, diagrams.path_view(runviews.detail(self.server.root, query.get("path", [""])[0], self.server.versions_cache)))
            elif request.path == "/api/runs":
                self.send(200, runviews.listing(self.server.root, query.get("path", [""])[0],
                          team=query.get("team", [""])[0], status=query.get("status", [""])[0],
                          search=query.get("search", [""])[0],
                          limit=max(1, min(200, int(query.get("limit", ["50"])[0]))),
                          offset=max(0, int(query.get("offset", ["0"])[0]))))
            elif request.path == "/api/run":
                self.send(200, runviews.detail(self.server.root, query.get("path", [""])[0], self.server.versions_cache))
            elif request.path == "/api/run-file":
                self.download(runviews.attachment(self.server.root, query.get("run", [""])[0],
                                                 query.get("file", [""])[0]))
            elif request.path == "/api/run-setup":
                self.send(200, self.server.jobs.setup(query.get("path", [""])[0]))
            else:
                self.send(404, {"error": "That page could not be found."})
        except api.OutsideRoot as error:
            self.send(403, {"error": str(error)})
        except (FileNotFoundError, NotADirectoryError):
            self.send(404, {"error": "That folder could not be found."})
        except UnicodeError:
            self.send(400, {"error": "This file isn’t plain text. Choose a UTF-8 text file."})
        except ValueError as error:
            self.send(400, {"error": str(error)})
        except (OSError, RuntimeError):
            self.send(400, {"error": "That file or folder could not be opened."})

    def do_POST(self):
        if not self.authorized(mutation=True):
            return
        try:
            length = int(self.headers.get("Content-Length", "0"))
            if not 0 < length <= 12 * 1024 * 1024:
                self.send(413, {"error": "The request is too large or empty."})
                return
            if self.headers.get_content_type() != "application/json":
                raise ValueError("Send a JSON request.")
            data = json.loads(self.rfile.read(length))
            if not isinstance(data, dict):
                raise ValueError("Send a JSON object.")
            route = urlsplit(self.path).path
            root = self.server.root
            if route == "/api/stop":
                self.send(200, self.server.jobs.stop(data["id"]))
                return
            with self.server.files_lock:
                if route == "/api/start":
                    result = self.server.jobs.start(data)
                elif route == "/api/save":
                    result = api.save_file(root, data["path"], data["text"], data["revision"])
                elif route == "/api/validate":
                    result = api.validate_team(root, data.get("path", ""), data.get("offline") is True,
                                               fresh=data.get("remembered") is not True)
                elif route == "/api/member":
                    result = api.create_member(root, data["path"])
                elif route == "/api/chair":
                    result = api.create_chair(root, data.get("path", ""))
                elif route == "/api/rename":
                    result = api.rename_file(root, data["path"], data["destination"], data["revision"])
                elif route == "/api/trash":
                    result = api.trash_file(root, data["path"], data["revision"])
                elif route == "/api/trash-team":
                    if self.server.jobs.status()["active"] is not None:
                        raise api.Conflict("A run is in progress. Stop it or wait for it to finish "
                                           "before moving a team to the trash.")
                    result = api.trash_team(root, data["path"])
                elif route == "/api/restore-team":
                    result = diagrams.restore(root, data["path"], data["destination"])
                elif route == "/api/new":
                    result = api.new_team(root, data["path"], data.get("example", ""))
                elif route == "/api/key":
                    result = api.set_key(data["name"], data["value"])
                elif route == "/api/open-run-folder":
                    result = runviews.open_folder(root, data["path"])
                else:
                    self.send(404, {"error": "That action could not be found."})
                    return
            self.send(200, result)
        except api.OutsideRoot as error:
            self.send(403, {"error": str(error)})
        except jobs.InvalidTeam as error:
            self.send(422, {"error": str(error), "validation": error.report})
        except FileNotFoundError as error:
            self.send(404, {"error": str(error)})
        except (api.Conflict, FileExistsError) as error:
            self.send(409, {"error": str(error) or "That filename already exists."})
        except UnicodeError:
            self.send(400, {"error": "This file isn’t plain text. Choose a UTF-8 text file."})
        except (ValueError, NewError, InputError) as error:
            self.send(400, {"error": str(error)})
        except (TypeError, KeyError, AttributeError, OSError, RuntimeError, subprocess.SubprocessError):
            # Do not echo request values: the request may be a key submission.
            self.send(400, {"error": "The action could not be completed. Check the folder, filename and values."})


ISSUES = "https://github.com/teamrunner/teamrunner/issues/new"
HELLO = "hello@teamrunner.ai"


def feedback() -> dict:
    """Where comments go (GitHub Issues or email), with the details a bug report asks for.

    Nothing is sent from here: these are links the person opens and reads before posting.
    """
    import platform
    from .. import __version__
    version = f"teamrunner {__version__}"
    system = f"{platform.system()} {platform.release()}, Python {platform.python_version()}"
    return {"version": version, "system": system, "email": HELLO,
            "bug": f"{ISSUES}?template=bug_report.yml&version={quote(version)}&system={quote(system)}",
            "idea": f"{ISSUES}?template=question.yml&version={quote(version)}",
            "mail": f"mailto:{HELLO}?subject={quote('TeamRunner feedback')}"
                    f"&body={quote(chr(10) * 2 + version + chr(10) + system)}"}


def serve(folder: Path) -> int:
    try:
        server = Server(folder)
    except (OSError, ValueError, RuntimeError) as error:
        print(f"Could not open {folder}: {error}", file=sys.stderr)
        return 1
    with server:
        print(f"TeamRunner · {server.root}\n{server.url}\nPress Ctrl+C to stop.", flush=True)
        try:
            if not webbrowser.open(server.url):
                print("Open the address above in your browser.", flush=True)
        except webbrowser.Error:
            print("Open the address above in your browser.", flush=True)
        try:
            server.serve_forever()
        except KeyboardInterrupt:
            print("\nTeamRunner's local page stopped.")
    return 0
