"""
Live evals for the orb's screen actions. Each case sets up a known page in the user's Chrome, gives
the orchestrator one natural-language request, then checks the REAL state afterwards (window title,
front app, whether the page moved), never just the reply text: a reply that says "Clicked X" while
the page did not change is the exact failure these evals exist to catch.

Run:  .venv/bin/python -m evals.screen_actions [case_id ...] [--list]
Needs: macOS with Chrome, Accessibility + Screen Recording granted, network. Benign public pages only
(Wikipedia, Google, YouTube, GitHub), no logins, nothing typed beyond search boxes.
Exit code 0 when every selected case passes. Results JSON: evals/results/screen_actions-<ts>.json
"""
from __future__ import annotations

import contextlib
import io
import json
import re
import subprocess
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

CHROME = "Google Chrome"
TURING = "https://en.wikipedia.org/wiki/Alan_Turing"


@dataclass
class State:
    title: str
    front: str
    reply: str
    tools: list[str]
    seconds: float
    moved: bool | None  # did the page pixels change between setup and the end (None: not measured)


@dataclass
class Case:
    id: str
    prompt: str
    setup: Callable[[], None]
    check: Callable[[State], str | None]  # None = pass, else the failure reason
    max_s: float = 30.0
    measure_motion: bool = False
    teardown: Callable[[], None] = lambda: None
    tags: list[str] = field(default_factory=list)


# ---- local fixture pages (deterministic, nothing to guess from training data) --------------------

_PORT = 8765
_PAGES = {
    "/long": "<h1>Field notes</h1>" + "".join(f"<p>Paragraph {i}: weather, gardens and other filler text.</p>" for i in range(1, 90))
             + "<p>The calibration code for the zebra project is 7731-ZULU.</p>",
    "/specs": "<h1>Kestrel X9 recorder</h1><p>A small voice recorder.</p>"
              "<details><summary>Product information</summary><p>Battery capacity: 4,731 mAh. Weight: 88 grams.</p></details>"
              "<button aria-expanded='false' onclick=\"var d=document.getElementById('w');var o=this.getAttribute('aria-expanded')=='true';"
              "this.setAttribute('aria-expanded',!o);d.style.display=o?'none':'block'\">Warranty</button>"
              "<div id='w' style='display:none'><p>Warranty period: 37 months.</p></div>",
}
_server = None


def ensure_fixture_server() -> None:
    global _server
    if _server:
        return
    import threading
    from http.server import BaseHTTPRequestHandler, HTTPServer

    class H(BaseHTTPRequestHandler):
        def do_GET(self):
            body = ("<!doctype html><meta charset=utf-8><title>%s</title><body style='font:20px sans-serif'>%s" % (self.path.strip("/"), _PAGES.get(self.path, "<p>404</p>"))).encode()
            self.send_response(200 if self.path in _PAGES else 404)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *a):
            pass

    _server = HTTPServer(("127.0.0.1", _PORT), H)
    threading.Thread(target=_server.serve_forever, daemon=True).start()


def fixture(path: str) -> Callable[[], None]:
    def go() -> None:
        ensure_fixture_server()
        open_page(f"http://127.0.0.1:{_PORT}{path}", 2.0)()

    return go


# ---- setup helpers -----------------------------------------------------------------------------

def open_page(url: str, wait: float = 3.0) -> Callable[[], None]:
    def go() -> None:
        from tools.registry import EXECUTORS

        EXECUTORS["open_url"]({"url": url, "app": CHROME})
        time.sleep(wait)

    return go


def open_then_scroll(url: str, pages: float) -> Callable[[], None]:
    def go() -> None:
        from tools import screen_act

        open_page(url)()
        screen_act.scroll_page("down", pages, CHROME)
        time.sleep(0.6)

    return go


def _chrome_tabs() -> int:
    from AppKit import NSWorkspace

    from tools.ax_context import tab_count

    return sum(tab_count(a.processIdentifier()) for a in NSWorkspace.sharedWorkspace().runningApplications()
               if a.localizedName() == CHROME)


def open_tabs(n: int) -> Callable[[], None]:
    def go() -> None:
        subprocess.run(["/usr/bin/open", "-a", CHROME, *[f"http://127.0.0.1:{_PORT}/long?t={i}" for i in range(n)]], capture_output=True)
        time.sleep(3)

    return go


def no_tabs_left(s: State) -> str | None:
    left = _chrome_tabs()
    return None if left == 0 else f"{left} Chrome tab(s) still open (counted over every Chrome instance)"


def calculator_in_front() -> None:
    subprocess.run(["/usr/bin/open", "-a", "Calculator"], capture_output=True)
    time.sleep(1.5)


def quit_calculator() -> None:
    subprocess.run(["osascript", "-e", 'tell application "Calculator" to quit'], capture_output=True)


# ---- check helpers -----------------------------------------------------------------------------

def title_has(*needles: str) -> Callable[[State], str | None]:
    def chk(s: State) -> str | None:
        t = s.title.lower()
        miss = [n for n in needles if n.lower() not in t]
        return f"title {s.title!r} lacks {miss}" if miss else None

    return chk


def all_of(*checks: Callable[[State], str | None]) -> Callable[[State], str | None]:
    def chk(s: State) -> str | None:
        for c in checks:
            if (why := c(s)):
                return why
        return None

    return chk


def used(tool: str) -> Callable[[State], str | None]:
    return lambda s: None if tool in s.tools else f"never called {tool} (called {s.tools})"


def not_used(tool: str) -> Callable[[State], str | None]:
    return lambda s: None if tool not in s.tools else f"called {tool} (it should not be needed)"


def page_moved(s: State) -> str | None:
    return None if s.moved else "page did not move"


def reply_matches(pattern: str, want: bool = True) -> Callable[[State], str | None]:
    rx = re.compile(pattern, re.I)

    def chk(s: State) -> str | None:
        hit = bool(rx.search(s.reply))
        if hit != want:
            return f"reply {'lacks' if want else 'wrongly matches'} /{pattern}/: {s.reply[:140]!r}"
        return None

    return chk


def front_is(app: str) -> Callable[[State], str | None]:
    return lambda s: None if s.front == app else f"front app is {s.front!r}, wanted {app!r}"


# ---- cases -------------------------------------------------------------------------------------

CASES: list[Case] = [
    Case("open-url", "open the Alan Turing wikipedia article in chrome",
         open_page("https://www.google.com", 2.0), title_has("alan turing", "wikipedia"), 20, tags=["open"]),
    Case("click-link", "click the link Bletchley Park",
         open_page(TURING), title_has("bletchley"), 25, tags=["click"]),
    Case("click-after-scroll", "click the link Bletchley Park",
         open_then_scroll(TURING, 2), title_has("bletchley"), 30, tags=["click", "regression"]),
    Case("google-search", "search google for Alan Turing",
         open_page("https://www.google.com", 2.5), title_has("alan turing", "google search"), 40, tags=["type", "regression"]),
    Case("youtube-search", "search youtube for lofi hip hop",
         open_page("https://www.youtube.com", 3.5), title_has("lofi hip hop", "youtube"), 45, tags=["type", "regression"]),
    Case("scroll-down", "scroll down the page",
         open_page(TURING), all_of(used("scroll_page"), page_moved), 25, measure_motion=True, tags=["scroll"]),
    Case("scroll-bottom", "scroll to the very bottom of the page",
         open_page(TURING), all_of(used("scroll_page"), page_moved), 40, measure_motion=True, tags=["scroll"]),
    Case("scroll-noop-fast", "scroll to the bottom",
         open_page("https://www.google.com", 2.5), reply_matches(r"already|bottom"), 12, tags=["scroll", "regression"]),
    Case("switch-app", "go back to chrome",
         calculator_in_front, front_is(CHROME), 20, teardown=quit_calculator, tags=["app", "regression"]),
    Case("honest-miss", "click the link called Quantum Banana Embezzlement",
         open_page("https://www.google.com", 2.5),
         all_of(title_has("google"), reply_matches(r"couldn.?t|could not|not find|can.?t find|no .*(link|match)|isn.?t|unable")),
         25, tags=["honesty"]),
    Case("no-field-no-typing", "type hello world",
         open_page(TURING),
         all_of(title_has("alan turing"), reply_matches(r"not|couldn|nothing|no text field|click|need", True)),
         25, tags=["honesty", "regression"]),
    Case("read-screen", "what page am I on right now?",
         open_page(TURING), reply_matches(r"turing"), 20, tags=["read"]),
    Case("clock", "what time is it in Tokyo",
         lambda: None, all_of(used("get_current_time"), reply_matches(r"\d")), 15, tags=["info"]),
    Case("open-same-page-again", "open http://127.0.0.1:8765/long in chrome",
         fixture("/long"), all_of(used("open_url"), reply_matches(r"already|still|same|didn.?t reopen|not reopen|not opened")), 20, tags=["open", "regression"]),
    Case("page-text-offscreen", "what is the calibration code for the zebra project on this page?",
         fixture("/long"), all_of(used("read_page_text"), not_used("scroll_page"), reply_matches(r"7731")), 25, tags=["read-page"]),
    Case("page-text-collapsed", "what battery capacity do the specs on this page list?",
         fixture("/specs"), all_of(used("read_page_text"), reply_matches(r"4,?731")), 25, tags=["read-page"]),
    Case("page-text-accordion", "what warranty period does this page list?",
         fixture("/specs"), all_of(used("read_page_text"), reply_matches(r"37")), 25, tags=["read-page"]),
    Case("page-text-absent", "what is the price of the item on this page?",
         fixture("/specs"), all_of(used("read_page_text"), reply_matches(r"\$\s?\d|\d+\s?(usd|dollars)", False)), 25, tags=["read-page", "honesty"]),
    Case("close-all-tabs", "close all the tabs",
         open_tabs(5), all_of(used("close_tabs"), no_tabs_left), 40, tags=["tabs", "regression"]),
    Case("chain-scroll-click", "scroll down and then click the link Bletchley Park",
         open_page(TURING), title_has("bletchley"), 45, tags=["chain"]),
]


# ---- runner ------------------------------------------------------------------------------------

def _probe_title() -> str:
    from tools import ax_context

    try:
        return ax_context.window_title(CHROME)
    except Exception as e:  # accessibility hiccup: report, don't crash the run
        return f"?{e!r}"


def _thumb():
    from tools import screen_act

    box = screen_act._front_bounds(CHROME)
    return screen_act._window_thumb(box) if box else None


def run_case(case: Case) -> dict:
    import harness
    from tools import screen_act
    from tools.screen_watch import _front_window

    case.setup()
    before = _thumb() if case.measure_motion else None
    buf = io.StringIO()
    t0 = time.time()
    reply, err = "", None
    try:
        with contextlib.redirect_stdout(buf):
            r = harness.run(case.prompt, chat_id=f"eval-{case.id}-{int(t0)}", orchestrated=True)
        reply = str(r[0] if isinstance(r, tuple) else r)
    except Exception as e:
        err = repr(e)
    secs = round(time.time() - t0, 1)
    time.sleep(0.6)  # let the overlay and page settle before reading state
    moved = None
    if before is not None:
        after = _thumb()
        moved = bool(after) and not screen_act._same_view(before, after)
    st = State(_probe_title(), _front_window()[0], reply, re.findall(r"^\[tool\] (\w+)\(", buf.getvalue(), re.M), secs, moved)
    case.teardown()
    why = f"exception {err}" if err else case.check(st)
    if why is None and secs > case.max_s:
        why = f"too slow: {secs}s > {case.max_s}s"
    return {"id": case.id, "pass": why is None, "why": why, "seconds": secs, "tools": st.tools,
            "title": st.title, "front": st.front, "reply": reply[:300], "tags": case.tags}


def main(argv: list[str]) -> int:
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
    if "--list" in argv:
        for c in CASES:
            print(f"{c.id:22} {','.join(c.tags):22} {c.prompt}")
        return 0
    want = [a for a in argv if not a.startswith("--")]
    cases = [c for c in CASES if not want or c.id in want]
    results = []
    for c in cases:
        res = run_case(c)
        results.append(res)
        print(f"{'PASS' if res['pass'] else 'FAIL'}  {c['id'] if False else res['id']:22} {res['seconds']:>5}s  {res['why'] or ''}", flush=True)
    out = Path(__file__).parent / "results"
    out.mkdir(exist_ok=True)
    (out / f"screen_actions-{int(time.time())}.json").write_text(json.dumps(results, indent=1))
    failed = [r for r in results if not r["pass"]]
    print(f"\n{len(results) - len(failed)}/{len(results)} passed")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
