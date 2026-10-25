"""The extension's page probe, tested as JavaScript against a real page.

Chrome refuses `--load-extension` on the stable channel and a real grant needs a
human click, so the extension itself cannot live in CI. Its page-side function can:
extract `pageProbe` from background.js and run it in a real browser. Every assertion
here corresponds to a defect that shipped at least once:

  * level="error" returned nothing while uncaught exceptions (type 'pageerror')
    existed - the most important errors were the ones being filtered out;
  * level="warning" matched nothing, because the browser calls it 'warn';
  * calling the probe with no arguments threw on `args.limit`.
"""
from pathlib import Path

import pytest

playwright = pytest.importorskip("playwright.sync_api")

ROOT = Path(__file__).resolve().parents[2]
BACKGROUND = ROOT / "extension" / "background.js"


def _page_probe_source() -> str:
    src = BACKGROUND.read_text()
    start = src.index("function pageProbe(args) {")
    depth = 0
    for i in range(start, len(src)):
        if src[i] == "{":
            depth += 1
        elif src[i] == "}":
            depth -= 1
            if depth == 0:
                return src[start : i + 1]
    raise AssertionError("pageProbe is not a balanced function any more")


@pytest.fixture(scope="module")

def page():
    try:
        with playwright.sync_playwright() as p:
            browser = p.chromium.launch()
            pg = browser.new_page()
            pg.goto("data:text/html,<title>Probe</title><h1>Hello</h1><button>Reject all</button>")
            pg.evaluate(f"window.__probe = {_page_probe_source()}")
            pg.evaluate("window.__probe({kind:'console'})")  # install the hooks
            pg.evaluate("console.error('plain error'); console.warn('a warning'); console.log('chatter')")
            pg.evaluate("setTimeout(()=>{throw new Error('uncaught boom')},0)")
            pg.wait_for_timeout(300)
            yield pg
            browser.close()
    except Exception as exc:  # no browser binaries in this environment
        pytest.skip(f"no chromium available: {exc}")


def test_probe_works_with_no_arguments(page):
    """It threw on args.limit before: a caller that omits args is not a crash."""
    out = page.evaluate("window.__probe()")
    assert "elements" in out and out["title"] == "Probe"


def test_all_four_message_types_are_captured(page):
    out = page.evaluate("window.__probe({kind:'console'})")
    assert out["total"] == 4
    assert out["types"] == {"error": 1, "warn": 1, "log": 1, "pageerror": 1}


def test_uncaught_exceptions_are_errors(page):
    out = page.evaluate("window.__probe({kind:'console'})")
    assert out["errors"] == 2  # 'error' + 'pageerror'


def test_level_error_includes_pageerror(page):
    out = page.evaluate("window.__probe({kind:'console', level:'error'})")
    assert out["count"] == 2
    assert {e["type"] for e in out["entries"]} == {"error", "pageerror"}


def test_level_warning_matches_warn(page):
    out = page.evaluate("window.__probe({kind:'console', level:'warning'})")
    assert out["count"] == 1 and out["entries"][0]["type"] == "warn"


def test_the_probe_reports_totals_so_a_filter_cannot_look_empty(page):
    out = page.evaluate("window.__probe({kind:'console', level:'error'})")
    assert out["total"] == 4 and out["filtered_by"] == "error"


def test_consent_controls_are_found(page):
    out = page.evaluate("window.__probe({kind:'dom'})")
    assert "Reject all" in out["consent"]


def test_click_presses_the_visible_control(page):
    out = page.evaluate("window.__probe({kind:'click', text:'Reject'})")
    assert out == {"ok": True, "clicked": "Reject all"}
