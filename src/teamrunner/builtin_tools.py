"""The built-in tools (D105, D108), each with the guardrails it enforces.

TeamRunner enforces every guardrail, not the model (D77): a request that
breaks one is refused with the reason, and the asker is told. The general
guardrails (`calls_per_turn`, `calls_per_run`, `timeout`) are enforced by
the toolkeepers' desk (`desk.py`); the ones here belong to each tool.
"""

from __future__ import annotations

import ipaddress
import re
import socket
from dataclasses import dataclass, field
from html.parser import HTMLParser
from io import BytesIO
from pathlib import Path
from typing import Awaitable, Callable
from urllib.parse import urlparse

from . import __version__

# Where a guardrail is not written, these apply. Recorded in every tool_call.
DEFAULT_MAX_CHARS = 50_000
DEFAULT_MAX_RESULTS = 10
BRAVE_URL = "https://api.search.brave.com/res/v1/web/search"
BRAVE_KEY = "BRAVE_API_KEY"  # the search service's key (D99, D113)
# Brave's list price, $5 per 1,000 searches (brave.com/search/api, 2026-11-02); free credits
# are not deducted, so a run's cost is what it would cost anyone repeating it (D118).
BRAVE_PRICE_USD = 0.005
LIST_PRICES = {"web_search_brave": BRAVE_PRICE_USD}  # per call; the other tools cost nothing
MAX_DOWNLOAD = 25 * 1024 * 1024
USER_AGENT = f"TeamRunner/{__version__} (+https://github.com/teamrunner/teamrunner)"


class Refused(Exception):
    """A guardrail said no. The reason is what the asker is told."""


@dataclass
class Context:
    inputs_dir: Path  # the run's inputs/ (D108)
    work_dir: Path  # the run's work/ (D105)
    keys: dict[str, str] = field(default_factory=dict)  # the tools' keys, found before the run
    package_top: Path | None = None  # where `paths` are read from
    rehearsal: bool = False  # D115: requests are checked, but no tool runs
    prices: object = None  # prices.Prices: overrides from ~/.teamrunner/prices.yaml (D120)


@dataclass
class Answer:
    text: str
    service: str | None = None  # which search service answered (D105)
    cost_usd: float = 0.0  # what the request cost at list price (D118)
    logged: str | None = None  # what the run log keeps in place of `text`, when not all of it


@dataclass(frozen=True)
class Tool:
    name: str
    summary: str  # what the model is told the tool does
    arguments: dict  # JSON Schema of what it takes
    run: Callable[[dict, dict, Context], Awaitable[Answer]]


# --- reading text out of what came back ------------------------------------------

class _Text(HTMLParser):
    SKIP = {"script", "style", "noscript", "svg", "head"}
    BREAK = {"p", "div", "br", "li", "h1", "h2", "h3", "h4", "h5", "h6", "tr", "section", "article"}

    def __init__(self):
        super().__init__()
        self.parts: list[str] = []
        self.skipping = 0
        self.title: list[str] = []
        self.in_title = False

    def handle_starttag(self, tag, attrs):
        if tag == "title":
            self.in_title = True
        elif tag in self.SKIP:
            self.skipping += 1
        elif tag in self.BREAK:
            self.parts.append("\n")

    def handle_endtag(self, tag):
        if tag == "title":
            self.in_title = False
        elif tag in self.SKIP and self.skipping:
            self.skipping -= 1

    def handle_data(self, data):
        if self.in_title:
            self.title.append(data)
        elif not self.skipping:
            self.parts.append(data)


def html_text(html: str) -> str:
    parser = _Text()
    parser.feed(html)
    lines = [" ".join(line.split()) for line in "".join(parser.parts).splitlines()]
    title = " ".join("".join(parser.title).split())
    return "\n".join(line for line in [title, *lines] if line)


def pdf_text(data: bytes) -> str:
    from pypdf import PdfReader
    reader = PdfReader(BytesIO(data))
    return "\n\n".join((page.extract_text() or "").strip() for page in reader.pages).strip()


def is_pdf(data: bytes) -> bool:
    return data[:5] == b"%PDF-"


def cut(text: str, guardrails: dict) -> str:
    limit = guardrails.get("max_chars", DEFAULT_MAX_CHARS)
    if len(text) <= limit:
        return text
    return text[:limit] + f"\n\n[cut at {limit} characters of {len(text)}]"


def check_type(data: bytes, guardrails: dict) -> None:
    types = guardrails.get("types")
    if types and "pdf" in types and not is_pdf(data):
        raise Refused("not a PDF")


# --- read_page ---------------------------------------------------------------------

def _allowed_host(host: str, domains: list[str] | None) -> bool:
    if not domains:
        return True
    host = host.lower().rstrip(".")
    return any(host == d.lower() or host.endswith("." + d.lower()) for d in domains)


def _public(host: str) -> bool:
    """A page on the internet, not this machine or its local network."""
    try:
        infos = socket.getaddrinfo(host, None)
    except OSError:
        return True  # let the request fail on its own, with its own message
    for info in infos:
        address = ipaddress.ip_address(info[4][0].split("%")[0])
        if getattr(address, "ipv4_mapped", None):
            address = address.ipv4_mapped
        if not address.is_global or address.is_multicast:
            return False
    return True


async def _refuse_local(request) -> None:
    """Checked before every request, redirects included: a public page must not lead
    read_page to this computer, its network, or a cloud machine's metadata address."""
    if not _public(request.url.host):
        raise Refused(f"the link led to {request.url.host}, which is on this computer or its local network")


async def read_page(args: dict, guardrails: dict, ctx: Context) -> Answer:
    import httpx
    url = str(args.get("url", "")).strip()
    parsed = urlparse(url)
    if parsed.scheme not in ("http", "https") or not parsed.hostname:
        raise Refused("not a web link (it must start with http:// or https://)")
    if not _allowed_host(parsed.hostname, guardrails.get("domains")):
        raise Refused(f"{parsed.hostname} is not one of the sites this toolkeeper reads")
    if not _public(parsed.hostname):
        raise Refused(f"{parsed.hostname} is on this computer or its local network")
    async with httpx.AsyncClient(follow_redirects=True, headers={"User-Agent": USER_AGENT},
                                 event_hooks={"request": [_refuse_local]}) as client:
        async with client.stream("GET", url) as response:
            final = response.url.host
            if not _allowed_host(final, guardrails.get("domains")):
                raise Refused(f"the link led to {final}, which is not one of the sites this toolkeeper reads")
            if response.status_code >= 400:
                return Answer(f"The site answered {response.status_code} ({response.reason_phrase}).")
            data = b""
            async for chunk in response.aiter_bytes():
                data += chunk
                if len(data) > MAX_DOWNLOAD:
                    raise Refused("the page is larger than 25 MB")
            kind = response.headers.get("content-type", "").split(";")[0].strip().lower()
    check_type(data, guardrails)
    if is_pdf(data):
        text = pdf_text(data)
    elif "html" in kind or data.lstrip()[:1] == b"<":
        text = html_text(data.decode(response.encoding or "utf-8", errors="replace"))
    elif kind.startswith("text/") or kind in ("application/json", "application/xml") or not kind:
        text = data.decode(response.encoding or "utf-8", errors="replace")
    else:
        raise Refused(f"this is a {kind} file, which read_page cannot read")
    return Answer(cut(text, guardrails))


# --- read_file ----------------------------------------------------------------------

async def read_file(args: dict, guardrails: dict, ctx: Context) -> Answer:
    asked = str(args.get("file", "")).strip()
    inputs = ctx.inputs_dir.resolve()
    candidates = [Path(asked), inputs / asked, inputs / Path(asked).name]
    path = next((c.resolve() for c in candidates
                 if c.resolve().is_relative_to(inputs) and c.resolve().is_file()), None)
    if path is None:
        given = sorted(p.name for p in inputs.iterdir()) if inputs.is_dir() else []
        raise Refused("read_file reads only the files this run was given"
                      + (": " + ", ".join(given) if given else "; this run was given none"))
    data = path.read_bytes()
    check_type(data, guardrails)
    text = pdf_text(data) if is_pdf(data) else data.decode("utf-8", errors="replace")
    return Answer(cut(text, guardrails))


# --- web_search_brave -------------------------------------------------------------------

def _plain(snippet: str) -> str:
    return " ".join(re.sub(r"<[^>]+>", "", snippet or "").split())


async def web_search_brave(args: dict, guardrails: dict, ctx: Context) -> Answer:
    """Brave Search (D113): titles, links and snippets."""
    import httpx
    query = " ".join(str(args.get("query", "")).split())
    if not query:
        raise Refused("the query was empty")
    domains = guardrails.get("domains")
    wanted = min(guardrails.get("max_results", DEFAULT_MAX_RESULTS), 20)  # Brave's most per page
    sent = query
    if domains:
        sent += " (" + " OR ".join(f"site:{d}" for d in domains) + ")"
    async with httpx.AsyncClient(headers={"User-Agent": USER_AGENT}) as client:
        response = await client.get(BRAVE_URL, params={"q": sent, "count": wanted},
                                    headers={"Accept": "application/json",
                                             "X-Subscription-Token": ctx.keys.get(BRAVE_KEY, "")})
    if response.status_code == 429:
        raise Refused("the search service's rate limit was reached")
    if response.status_code >= 400:
        raise RuntimeError(f"Brave Search answered {response.status_code}: {response.text[:200]}")
    results = (response.json().get("web") or {}).get("results") or []
    if domains:  # the site: operators are a request; the guardrail is enforced here
        results = [r for r in results if _allowed_host(urlparse(r.get("url", "")).hostname or "", domains)]
    results = results[:wanted]
    if not results:
        return Answer(f"No results for “{query}”.", service="brave", cost_usd=BRAVE_PRICE_USD)
    lines = [f"{i}. {_plain(r.get('title'))}\n   {r.get('url')}\n   {_plain(r.get('description'))}"
             for i, r in enumerate(results, 1)]
    # Brave's terms allow storing results only on plans with storage rights: by default the
    # member reads them and the log keeps the query and how many came back (D131).
    logged = None if guardrails.get("keep_results") else (
        f"{len(results)} result{'s' if len(results) != 1 else ''}, not kept "
        "(Brave's terms; `keep_results: true` keeps them)")
    return Answer("\n\n".join(lines), service="brave", cost_usd=BRAVE_PRICE_USD, logged=logged)


# --- run_code -------------------------------------------------------------------------

def sandbox_place(folder: Path, package_top: Path | None) -> str:
    """Where a `paths` folder appears inside the sandbox, the same on every machine (D126):
    a folder in the package at /package/<its path in the package>, any other at /outside/<its name>."""
    folder = folder.resolve()
    if package_top is not None and folder.is_relative_to(package_top.resolve()):
        return "/".join(["/package", *folder.relative_to(package_top.resolve()).parts])
    return f"/outside/{folder.name}"


async def run_code(args: dict, guardrails: dict, ctx: Context) -> Answer:
    """Python in the WebAssembly sandbox (D114)."""
    from importlib.metadata import version
    from . import sandbox
    code = str(args.get("code", ""))
    if not code.strip():
        raise Refused("there was no code")
    folders = []
    for written in guardrails.get("paths") or []:
        folder = Path(written).expanduser()
        if not folder.is_absolute() and ctx.package_top is not None:
            folder = ctx.package_top / folder
        if not folder.is_dir():
            raise Refused(f"the folder {written} that this toolkeeper may read does not exist")
        folders.append((folder, sandbox_place(folder, ctx.package_top)))
    try:
        ran = await sandbox.run(code, ctx.work_dir, folders, bool(guardrails.get("network", False)))
    except sandbox.SandboxError as e:
        raise RuntimeError(f"the sandbox could not run: {e}") from e
    parts = []
    if ran.stdout.strip():
        parts.append("Output:\n" + ran.stdout.rstrip())
    if ran.stderr.strip():
        parts.append("Warnings:\n" + ran.stderr.rstrip())
    if ran.error:
        parts.append("Error:\n" + ran.error)
    if ran.written:
        parts.append("Files in the work folder: " + ", ".join(ran.written))
    text = "\n\n".join(parts) or "The code ran and printed nothing."
    return Answer(cut(text, guardrails),
                  service=f"pyodide {sandbox.PYODIDE} (Python {ran.python}), deno {version('deno')}")


# --- read_email and save_draft: a mailbox folder of .eml files --------------------------

EMAIL_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,99}")
DEFAULT_MAILBOX = "mailbox"


def mailbox_folder(guardrails: dict, ctx: Context) -> Path:
    """The mailbox this toolkeeper reads: a folder of .eml files, written relative to the package."""
    written = str(guardrails.get("mailbox", DEFAULT_MAILBOX))
    folder = Path(written).expanduser()
    if not folder.is_absolute() and ctx.package_top is not None:
        folder = ctx.package_top / folder
    if not folder.is_dir():
        raise Refused(f"the mailbox {written} that this toolkeeper reads does not exist")
    return folder


def find_email(asked: str, guardrails: dict, ctx: Context):
    """One email in the mailbox, by its id (its file name without .eml)."""
    from email import policy
    from email.parser import BytesParser
    asked = asked.strip().removesuffix(".eml")
    if not EMAIL_ID.fullmatch(asked):
        raise Refused("an email is asked for by its id, such as m001")
    path = mailbox_folder(guardrails, ctx) / f"{asked}.eml"
    if not path.is_file():
        raise Refused(f"there is no email {asked} in the mailbox")
    return asked, BytesParser(policy=policy.default).parsebytes(path.read_bytes())


def email_text(message) -> str:
    """The headers a reader needs, the body as plain text, and the names of any attachments."""
    lines = [f"{h}: {message[h]}" for h in ("From", "To", "Cc", "Date", "Subject") if message[h]]
    part = message.get_body(preferencelist=("plain", "html"))
    body = "" if part is None else part.get_content()
    if part is not None and part.get_content_type() == "text/html":
        body = html_text(body)
    attached = [p.get_filename() or p.get_content_type() for p in message.iter_attachments()]
    if attached:
        lines.append("Attachments: " + ", ".join(attached))
    return "\n".join(lines) + "\n\n" + body.strip()


async def read_email(args: dict, guardrails: dict, ctx: Context) -> Answer:
    _, message = find_email(str(args.get("id", "")), guardrails, ctx)
    return Answer(cut(email_text(message), guardrails), service="mailbox folder")


async def save_draft(args: dict, guardrails: dict, ctx: Context) -> Answer:
    """A reply saved to the run's work/outbox/, never sent. By default it goes to the
    email's sender and no one else (`reply_only`)."""
    from email.message import EmailMessage
    from email.utils import getaddresses
    asked, original = find_email(str(args.get("reply_to", "")), guardrails, ctx)
    body = str(args.get("body", "")).strip()
    if not body:
        raise Refused("the draft was empty")
    limit = guardrails.get("max_chars", DEFAULT_MAX_CHARS)
    if len(body) > limit:
        raise Refused(f"the draft is {len(body)} characters; this outbox takes at most {limit}")
    sender = original.get("Reply-To") or original.get("From") or ""
    sender_address = next((a for _, a in getaddresses([sender]) if a), "")
    wanted = str(args.get("to") or "").strip()
    wanted_addresses = [a for _, a in getaddresses([wanted]) if a]
    if wanted and guardrails.get("reply_only", True):
        if [a.lower() for a in wanted_addresses] != [sender_address.lower()]:
            raise Refused("this outbox only answers the sender of the email "
                          f"({sender_address}); it does not write to other addresses")
    to = wanted if wanted and not guardrails.get("reply_only", True) else sender
    if not to:
        raise Refused(f"email {asked} has no sender to answer")
    outbox = ctx.work_dir / "outbox"
    path = outbox / f"{asked}.eml"
    if path.exists():
        raise Refused(f"a draft answering {asked} is already in the outbox")
    draft = EmailMessage()
    draft["To"] = to
    if original["To"]:
        draft["From"] = original["To"]
    subject = str(original.get("Subject") or "")
    draft["Subject"] = subject if subject.lower().startswith("re:") else f"Re: {subject}".strip()
    if original["Message-ID"]:
        draft["In-Reply-To"] = original["Message-ID"]
    draft["X-TeamRunner"] = "draft, not sent"
    draft.set_content(body + "\n", cte="8bit")  # readable as it is, in any text editor
    outbox.mkdir(parents=True, exist_ok=True)
    path.write_bytes(bytes(draft))
    return Answer(f"Saved a draft to {to} in the outbox as outbox/{asked}.eml. It has not been sent.",
                  service="outbox folder")


TOOLS: dict[str, Tool] = {
    "web_search_brave": Tool("web_search_brave", "Searches the web: a query in, titles, links and snippets out.",
                       {"type": "object", "properties": {"query": {"type": "string"}},
                        "required": ["query"], "additionalProperties": False}, web_search_brave),
    "read_page": Tool("read_page", "Reads a web page or an online PDF: a link in, its text out.",
                      {"type": "object", "properties": {"url": {"type": "string"}},
                       "required": ["url"], "additionalProperties": False}, read_page),
    "read_file": Tool("read_file", "Reads a file this run was given: its name in, its text out.",
                      {"type": "object", "properties": {"file": {"type": "string"}},
                       "required": ["file"], "additionalProperties": False}, read_file),
    "run_code": Tool("run_code", "Runs Python code in a sandbox: the code in, what it prints and its "
                     "errors out. Files it writes in the current folder are kept for the rest of the run.",
                     {"type": "object", "properties": {"code": {"type": "string"}},
                      "required": ["code"], "additionalProperties": False}, run_code),
    "read_email": Tool("read_email", "Reads one email from the mailbox: its id in, its sender, "
                       "recipients, date, subject and text out.",
                       {"type": "object", "properties": {"id": {"type": "string"}},
                        "required": ["id"], "additionalProperties": False}, read_email),
    "save_draft": Tool("save_draft", "Saves a reply to an email in the outbox, where a person reads it "
                       "before anything is sent: the id of the email it answers and the reply's text in. "
                       "Nothing is sent.",
                       {"type": "object", "properties": {
                           "reply_to": {"type": "string", "description": "the id of the email this answers"},
                           "body": {"type": "string", "description": "the reply's text"},
                           "to": {"type": "string", "description": "who it goes to; leave out to answer the sender"}},
                        "required": ["reply_to", "body"], "additionalProperties": False}, save_draft),
}
NOT_BUILT: dict[str, str] = {}  # tool -> why this version cannot run it yet
TOOL_KEYS = {"web_search_brave": BRAVE_KEY}
