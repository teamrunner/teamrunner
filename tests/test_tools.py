import asyncio
import io
import json

import pytest

from teamrunner import builtin_tools
from teamrunner.build import load_team
from teamrunner.builtin_tools import Answer, Context, Refused, Tool, html_text, read_file, read_page
from teamrunner.console import Console
from teamrunner.model import Reply, ToolCall
from teamrunner.run import Options, run_team

from test_run import OPENAI, home  # noqa: F401  (the key-file fixture)
from test_turns import NAMED, FakeModel, fits
from test_validate import debate


def asks(toolkeeper, text="", **arguments):
    """A reply that calls a toolkeeper."""
    return Reply(text, "gpt-5", {"input": 10, "output": 5}, 0.001, 20,
                 [ToolCall(f"call_{toolkeeper}_{len(arguments)}", toolkeeper, json.dumps(arguments))])


@pytest.fixture
def pages(monkeypatch):
    """read_page answers from a dict, without the network."""
    seen = []

    async def fake(args, guardrails, ctx):
        seen.append((args, guardrails))
        if "slow" in args["url"]:
            await asyncio.sleep(5)
        return Answer(f"text of {args['url']}")

    monkeypatch.setitem(builtin_tools.TOOLS, "read_page",
                        Tool("read_page", "Reads a page.", builtin_tools.TOOLS["read_page"].arguments, fake))
    return seen


LIBRARY = """\
---
kind: toolkeeper
tools:
  read_page: {calls_per_turn: 2, timeout: 1}
access:
  judge: {calls_per_turn: 3, calls_per_run: 4}
  for: standard
---
I read web pages for the team.
"""


def go(folder, answers, quiet=False):
    model, stream = FakeModel(answers), io.StringIO()
    outcome = run_team(folder, Options(quiet=quiet), model_factory=lambda k, s: model,
                       listers=OPENAI, model_data=lambda m: {}, waits=(0, 0, 0),
                       console=Console(False, stream))
    events = [json.loads(line) for line in (outcome.folder / "events.jsonl").read_text(encoding="utf-8").splitlines()] \
        if outcome.folder else []
    return outcome, model, events, stream.getvalue()


def calls(events):
    return [e for e in events if e["event"] == "tool_call"]


def test_a_side_trip_inside_a_turn(tmp_path, pages):
    team = debate(tmp_path, **{**NAMED, "library.md": LIBRARY, "chair.md": "for -> against -> judge\n"})
    outcome, model, events, printed = go(team, {
        "for": [asks("library", url="https://a.org/x"), "Pro, citing a.org."],
        "judge": [fits("Pro.", winner="for", reasoning="")]})
    assert outcome.status == "completed", outcome.reason
    [call] = calls(events)
    assert (call["from"], call["toolkeeper"], call["tool"], call["mandatory"]) == ("for", "library", "read_page", False)
    assert call["access"] == "allowed" and call["result"] == "text of https://a.org/x"
    assert call["allowance"] == {"used": 1, "of": 2}
    assert call["instance_path"] == "library" and call["component_id"]
    # the reply came back inside for's turn, and only there
    second = model.calls[1][3]
    assert second[-1] == {"role": "tool", "tool_call_id": "call_library_1", "content": "text of https://a.org/x"}
    assert "a.org" not in model.calls[2][3][1]["content"].replace("Pro, citing a.org.", "")
    # who may use a toolkeeper sees it as a tool, described by its sign; others do not
    tools = {c[0]: c[5] for c in model.calls}
    assert tools["for"][0]["function"]["name"] == "library"
    assert tools["for"][0]["function"]["description"].startswith("I read web pages for the team.")
    assert tools["against"] is None
    assert "└ library   read “https://a.org/x”   ✓ 1 of 2" in printed
    assert printed.index("└ library") > printed.index("for ")


def test_limits_are_counted_per_member(tmp_path, pages):
    team = debate(tmp_path, **{**NAMED, "library.md": LIBRARY, "chair.md": "for -> judge -> judge:again\n"})
    judge_asks = [asks("library", url=f"https://a.org/{i}") for i in range(4)]
    outcome, model, events, _ = go(team, {
        "for": [asks("library", url="https://a.org/p")] * 3 + ["Done."],
        "judge": judge_asks + [fits("Pro.", winner="for", reasoning=""),
                               asks("library", url="https://a.org/5"), asks("library", url="https://a.org/6"),
                               fits("Pro.", winner="for", reasoning="")]})
    assert outcome.status == "completed", outcome.reason
    speaker = [c for c in calls(events) if c["from"] == "for"]
    assert [c["access"] for c in speaker] == ["allowed", "allowed", "refused"]
    assert speaker[2]["reason"] == "limit reached: 2 calls a turn"
    judge = [c for c in calls(events) if c["from"] == "judge"]
    assert [c["access"] for c in judge] == ["allowed"] * 3 + ["refused", "allowed", "refused"]
    assert judge[4]["allowance"]["run"] == {"used": 4, "of": 4}
    assert judge[5]["reason"] == "limit reached: 4 calls a run"
    told = [m for m in model.calls[3][3] if m["role"] == "tool"]  # for's fourth call
    assert told[-1]["content"].startswith("Refused: limit reached")


def test_a_slow_tool_is_cut_off(tmp_path, pages):
    team = debate(tmp_path, **{**NAMED, "library.md": LIBRARY, "chair.md": "for -> against -> judge\n"})
    outcome, _, events, _ = go(team, {"for": [asks("library", url="https://slow.org"), "Fine."],
                                      "judge": [fits("Pro.", winner="for", reasoning="")]})
    [call] = calls(events)
    assert call["access"] == "refused" and call["reason"] == "it took longer than 1 seconds"


STEP = "for -> against -> judge:research -> library -> judge:verdict\n"


def test_a_toolkeeper_step_replies_to_whoever_asked(tmp_path, pages):
    team = debate(tmp_path, **{**NAMED, "library.md": LIBRARY, "chair.md": STEP})
    outcome, model, events, printed = go(team, {
        "judge": ["I will check first.",  # forgot to ask: asked again (D85)
                  asks("library", "Checking the claims.", url="https://claims.org"),
                  fits("Con.", winner="against", reasoning="the claim was wrong")]})
    assert outcome.status == "completed", outcome.reason
    research = model.calls[2][3][1]["content"]
    assert research.endswith("It is your turn. Stage: research. End your turn with a request to library.")
    assert model.calls[2][4] is None  # no typed outputs in a turn that ends with a request
    retry = next(e for e in events if e["event"] == "retry")
    assert retry["reason"] == "your turn must end with a request to library"

    [call] = calls(events)
    assert call["mandatory"] and call["from"] == "judge" and call["allowance"] == {"used": 1, "of": 3, "run": {"used": 1, "of": 4}}
    verdict = model.calls[-1][3][1]["content"]
    assert "library (to judge):\ntext of https://claims.org" in verdict
    said = [e for e in events if e["event"] == "agent_message"]
    assert said[2]["content"] == "Checking the claims." and said[2]["asks"] == "library"
    assert said[3]["read"][-1] == call["seq"]
    assert "└ judge → library   read “https://claims.org”   ✓ 1 of 3" in printed


def test_a_search_key_is_checked_before_the_run(tmp_path):
    web = LIBRARY.replace("read_page: {calls_per_turn: 2, timeout: 1}", "web_search_brave: {}")
    team = debate(tmp_path, **{**NAMED, "web.md": web, "chair.md": "for -> against -> judge\n"})
    outcome, model, _, printed = go(team, {})
    assert outcome.exit_code == 1 and model.calls == []
    assert "web holds `web_search_brave`, which needs BRAVE_API_KEY" in printed


@pytest.fixture
def brave(monkeypatch):
    """Brave's API, answered here: what was asked, and a reply to give."""
    import httpx
    asked, reply = [], {"status": 200, "json": {"web": {"results": [
        {"title": "Remote <strong>work</strong> study", "url": "https://stanford.edu/a",
         "description": "Output rose <strong>13%</strong>."},
        {"title": "A blog", "url": "https://blog.example.com/b", "description": "Opinions."}]}}}

    def handle(request):
        asked.append(request)
        return httpx.Response(reply["status"], json=reply["json"])

    real = httpx.AsyncClient
    monkeypatch.setattr(httpx, "AsyncClient", lambda **kw: real(transport=httpx.MockTransport(handle), **kw))
    return asked, reply


def test_web_search_asks_brave(brave):
    asked, _ = brave
    from teamrunner.builtin_tools import web_search_brave
    ctx = Context(None, None, {"BRAVE_API_KEY": "brv-test"})
    answer = asyncio.run(web_search_brave({"query": "remote  work productivity"}, {"max_results": 5}, ctx))
    assert answer.service == "brave"
    assert answer.text == ("1. Remote work study\n   https://stanford.edu/a\n   Output rose 13%.\n\n"
                           "2. A blog\n   https://blog.example.com/b\n   Opinions.")
    request = asked[0]
    assert request.headers["X-Subscription-Token"] == "brv-test"
    assert request.url.params["q"] == "remote work productivity" and request.url.params["count"] == "5"


def test_web_search_keeps_to_its_sites(brave):
    asked, _ = brave
    from teamrunner.builtin_tools import web_search_brave
    answer = asyncio.run(web_search_brave({"query": "remote work"}, {"domains": ["stanford.edu", "nber.org"]},
                                    Context(None, None, {})))
    assert asked[0].url.params["q"] == "remote work (site:stanford.edu OR site:nber.org)"
    assert "blog.example.com" not in answer.text and answer.text.startswith("1. Remote work study")


def test_web_search_rate_limit_is_a_refusal(brave):
    _, reply = brave
    reply["status"] = 429
    from teamrunner.builtin_tools import web_search_brave
    with pytest.raises(Refused, match="rate limit"):
        asyncio.run(web_search_brave({"query": "x"}, {}, Context(None, None, {})))


def test_a_decider_cannot_also_ask_a_toolkeeper_step(tmp_path):
    from test_flow import CHECKER
    team = debate(tmp_path, **{**NAMED, "library.md": LIBRARY.replace("for: standard", "fact_checker: standard"),
                               "fact_checker.md": CHECKER,
                               "chair.md": "for -> fact_checker -> library -> against -> judge\n"
                                           "fact_checker --pass--> library\nfact_checker --fail--> for\n"
                                           "fact_checker --*3--> stop\n"})
    _, report = load_team(team)
    assert any("decides here, so its turn cannot also end with a request" in p.message
               for p in report.problems.errors)


# --- the tools themselves, without the network --------------------------------------

@pytest.mark.parametrize("url, guardrails, reason", [
    ("ftp://a.org/x", {}, "not a web link"),
    ("https://evil.com/x", {"domains": ["api.crossref.org"]}, "evil.com is not one of the sites"),
    ("http://localhost:8000/", {}, "localhost is on this computer or its local network"),
    ("http://127.0.0.1/", {}, "on this computer"),
])
def test_read_page_refuses_before_fetching(url, guardrails, reason):
    with pytest.raises(Refused, match=reason):
        asyncio.run(read_page({"url": url}, guardrails, Context(None, None)))


def test_read_page_allows_subdomains_of_a_listed_site():
    from teamrunner.builtin_tools import _allowed_host
    assert _allowed_host("www.api.crossref.org", ["api.crossref.org"])
    assert not _allowed_host("notcrossref.org", ["crossref.org"])


def test_read_file_reads_only_the_runs_inputs(tmp_path):
    inputs = tmp_path / "inputs"
    inputs.mkdir()
    (inputs / "paper.txt").write_text("x" * 100, encoding="utf-8")
    (tmp_path / "secret.txt").write_text("no", encoding="utf-8")
    ctx = Context(inputs, tmp_path / "work")
    answer = asyncio.run(read_file({"file": "paper.txt"}, {"max_chars": 10}, ctx))
    assert answer.text.startswith("x" * 10 + "\n\n[cut at 10 characters of 100]")
    with pytest.raises(Refused, match="only the files this run was given: paper.txt"):
        asyncio.run(read_file({"file": str(tmp_path / "secret.txt")}, {}, ctx))
    with pytest.raises(Refused, match="only the files"):
        asyncio.run(read_file({"file": "../secret.txt"}, {}, ctx))
    with pytest.raises(Refused, match="not a PDF"):
        asyncio.run(read_file({"file": "paper.txt"}, {"types": ["pdf"]}, ctx))


def test_html_becomes_text():
    html = "<html><head><title>t</title><script>x()</script></head><body><h1>Title</h1>" \
           "<p>One  two</p><p>three</p></body></html>"
    assert html_text(html) == "t\nTitle\nOne two\nthree"


def test_a_rehearsal_needs_no_model_and_no_key(tmp_path, home):
    """D115: placeholder words, random valid answers, pretend toolkeeper requests."""
    from test_engine import FACT_CHECKED
    from test_flow import CHECKER
    (home / ".env").write_text("", encoding="utf-8")  # no keys at all
    web = LIBRARY.replace("read_page: {calls_per_turn: 2, timeout: 1}", "web_search_brave: {}") \
                 .replace("for: standard", "fact_checker: standard")
    team = debate(tmp_path, **{**NAMED, "fact_checker.md": CHECKER, "web.md": web,
                               "chair.md": FACT_CHECKED.replace("-> judge", "-> judge:research -> web -> judge")})
    stream = io.StringIO()
    outcome = run_team(team, Options(rehearse=True), console=Console(False, stream))
    assert outcome.status == "completed", outcome.reason
    run = json.loads((outcome.folder / "run.json").read_text(encoding="utf-8"))
    events = [json.loads(line) for line in (outcome.folder / "events.jsonl").read_text(encoding="utf-8").splitlines()]
    assert run["rehearsal"]["seed"] and run["cost_usd"] == 0 and run["keys"] == {}
    said = [e for e in events if e["event"] == "agent_message"]
    assert all(e["content"].startswith("[rehearsal]") for e in said)
    assert {e["output"]["verdict"] for e in said if e["from"] == "fact_checker"} <= {"pass", "fail"}
    assert said[-1]["output"]["winner"] in ("for", "against")
    tool_calls = [e for e in events if e["event"] == "tool_call"]
    assert tool_calls and all(c["service"] == "rehearsal" for c in tool_calls if c["access"] == "allowed")
    assert any(c["mandatory"] and c["from"] == "judge" for c in tool_calls)
    assert "rehearsal · no model is called" in stream.getvalue()
    assert (outcome.folder / "output.md").read_text(encoding="utf-8").startswith("*A rehearsal (seed ")


def test_a_runs_cost_includes_its_searches(tmp_path, brave, home):
    """D118: each search costs Brave's list price; run.json splits models and tools."""
    (home / ".env").write_text("OPENAI_API_KEY=sk-test\nBRAVE_API_KEY=brv-test\n", encoding="utf-8")
    web = LIBRARY.replace("read_page: {calls_per_turn: 2, timeout: 1}", "web_search_brave: {}")
    team = debate(tmp_path, **{**NAMED, "web.md": web, "chair.md": "for -> against -> judge\n"})
    outcome, _, events, printed = go(team, {
        "for": [asks("web", query="remote work"), asks("web", query="hybrid work"), "Pro."],
        "judge": [fits("Pro.", winner="for", reasoning="")]})
    assert outcome.status == "completed", outcome.reason
    assert [c["cost_usd"] for c in calls(events)] == [0.005, 0.005]
    run = json.loads((outcome.folder / "run.json").read_text(encoding="utf-8"))
    assert run["cost_tools_usd"] == 0.01 and run["cost_models_usd"] == pytest.approx(0.005)
    assert run["cost_usd"] == pytest.approx(0.015)
    assert "$0.0150 (models $0.0050 · tools $0.0100)" in printed


def test_web_search_results_are_read_but_not_kept(brave):
    """D131: Brave's terms forbid storing results without storage rights, so the
    member reads them and the log keeps only how many came back, unless keep_results."""
    from teamrunner.builtin_tools import web_search_brave
    answer = asyncio.run(web_search_brave({"query": "remote work"}, {}, Context(None, None, {})))
    assert answer.text.startswith("1. Remote work study")
    assert answer.logged == "2 results, not kept (Brave's terms; `keep_results: true` keeps them)"
    kept = asyncio.run(web_search_brave({"query": "remote work"}, {"keep_results": True}, Context(None, None, {})))
    assert kept.logged is None


def test_a_runs_log_keeps_the_query_not_the_results(tmp_path, brave, home):
    (home / ".env").write_text("OPENAI_API_KEY=sk-test\nBRAVE_API_KEY=brv-test\n", encoding="utf-8")
    web = LIBRARY.replace("read_page: {calls_per_turn: 2, timeout: 1}", "web_search_brave: {}")
    team = debate(tmp_path, **{**NAMED, "web.md": web, "chair.md": "for -> against -> judge\n"})
    outcome, model, events, _ = go(team, {
        "for": [asks("web", query="remote work"), "Pro."],
        "judge": [fits("Pro.", winner="for", reasoning="")]})
    assert outcome.status == "completed", outcome.reason
    [call] = calls(events)
    assert call["arguments"] == {"query": "remote work"} and call["result_kept"] is False
    assert call["result"].startswith("2 results, not kept")
    log = (outcome.folder / "events.jsonl").read_text(encoding="utf-8")
    assert "stanford.edu" not in log and "Output rose" not in log


def test_read_page_refuses_a_redirect_to_this_computer(monkeypatch):
    """A public page that redirects to a local or metadata address is refused, not read."""
    import httpx
    def handle(request):
        if request.url.host == "example.com":
            return httpx.Response(302, headers={"Location": "http://169.254.169.254/latest/meta-data/"})
        return httpx.Response(200, text="secret")
    real = httpx.AsyncClient
    monkeypatch.setattr(httpx, "AsyncClient", lambda **kw: real(transport=httpx.MockTransport(handle), **kw))
    monkeypatch.setattr("teamrunner.builtin_tools._public", lambda host: host != "169.254.169.254")
    with pytest.raises(Refused, match="169.254.169.254"):
        asyncio.run(read_page({"url": "https://example.com/x"}, {}, Context(None, None, {})))


@pytest.mark.parametrize("address", ["127.0.0.1", "10.0.0.5", "169.254.169.254", "100.64.0.1",
                                     "0.0.0.0", "::1", "::ffff:127.0.0.1", "fd00::1"])
def test_local_addresses_are_not_public(monkeypatch, address):
    import socket
    from teamrunner.builtin_tools import _public
    monkeypatch.setattr(socket, "getaddrinfo", lambda host, port: [(0, 0, 0, "", (address, 0))])
    assert not _public("anything.example")
