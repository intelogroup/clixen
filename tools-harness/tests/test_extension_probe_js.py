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
import json
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


# ── kind=links: the URL instead of the Save As dialog ────────────────────────
# Measured failure this prevents: "download the January 2025 issue" ended in Chrome's
# Save As dialog with nothing saved, twice, while the PDF URL sat in an anchor.

def probe_links(page, body_html: str, **args):
    """Run kind=links against a page served from a real http origin.

    Relative hrefs cannot resolve against about:blank (`new URL` throws there), which
    let an earlier version of these tests pass on absolute URLs only.
    """
    page.unroute("**/*")
    page.route("**/*", lambda r: r.fulfill(status=200, content_type="text/html",
                                           body=f"<title>Archive</title>{body_html}"))
    page.goto("http://clixen.test/archive")
    page.evaluate(f"window.__probe = {_page_probe_source()}")
    extra = ",".join(f"{k}:{json.dumps(v)}" for k, v in args.items())
    return page.evaluate(f"window.__probe({{kind:'links'{',' + extra if extra else ''}}})")


def test_links_returns_downloadable_urls_absolute(page):
    out = probe_links(
        page,
        '<a href="/files/IGM_vol_03_no_44.pdf">January 2025</a>'
        '<a href="https://cdn.test/x/other.pdf?v=2">Other</a>'
        '<a href="/about">About us</a>'
    )
    hrefs = [link["href"] for link in out["links"]]
    assert len(hrefs) == 2, f"non-file links must be excluded, got {hrefs}"
    assert all(h.startswith("http") for h in hrefs), "hrefs must be absolute"
    assert any("IGM_vol_03_no_44.pdf" in h for h in hrefs), hrefs


def test_links_can_be_narrowed_with_match(page):
    out = probe_links(page, '<a href="/a/one.pdf">one</a><a href="/b/two.pdf">two</a>', match="two")
    assert out["matching"] == "two"
    assert len(out["links"]) == 1 and out["links"][0]["href"].endswith("two.pdf")


def test_links_reports_zero_rather_than_failing(page):
    out = probe_links(page, "<p>no files here</p>")
    assert out["count"] == 0 and out["links"] == []


def test_links_never_duplicates_or_returns_bad_schemes(page):
    out = probe_links(
        page,
        '<a href="/dup.pdf">a</a><a href="/dup.pdf">b</a>'
        '<a href="javascript:void(0)">js</a><a href="mailto:x@y.z">mail</a>'
    )
    hrefs = [link["href"] for link in out["links"]]
    assert len(hrefs) == 1, f"duplicates or non-http leaked in: {hrefs}"
    assert hrefs[0].endswith("/dup.pdf")
