"""Tests for tools/headed_browser.py — headed-browser worker + ref engine.

Unit tests run without a browser (worker thread mechanics, ref formatting,
schema/registry wiring). The end-to-end test drives a real local form page
and is marked `live` (needs Playwright browsers installed).
"""
from __future__ import annotations

import threading

import pytest

from tools import headed_browser as hb

pytestmark = pytest.mark.timeout(60)


# --------------------------------------------------------------------------
# Worker thread (the Playwright thread-affinity fix)
# --------------------------------------------------------------------------

def test_run_in_worker_executes_on_single_dedicated_thread():
    tid_main = threading.get_ident()
    tid1 = hb._WORKER.run(lambda: threading.get_ident())
    tid2 = hb._WORKER.run(lambda: threading.get_ident())
    assert tid1 == tid2 != tid_main


def test_run_in_worker_from_multiple_caller_threads():
    caller_tids = []
    results = []

    def caller():
        caller_tids.append(threading.get_ident())
        results.append(hb._WORKER.run(lambda: "ok"))

    threads = [threading.Thread(target=caller) for _ in range(4)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert results == ["ok"] * 4
    assert len(set(caller_tids)) > 1  # actually ran on different caller threads


def test_run_in_worker_propagates_exception_text():
    with pytest.raises(RuntimeError, match="boom"):
        hb._WORKER.run(lambda: (_ for _ in ()).throw(RuntimeError("boom")))


def test_run_in_worker_returns_values_and_kwargs():
    assert hb._WORKER.run(lambda a, b=0: a + b, 2, b=3) == 5


def test_worker_survives_prior_exception():
    with pytest.raises(ValueError):
        hb._WORKER.run(lambda: (_ for _ in ()).throw(ValueError("x")))
    assert hb._WORKER.run(lambda: "alive") == "alive"


# --------------------------------------------------------------------------
# Ref formatter
# --------------------------------------------------------------------------

def _el(**over):
    base = dict(ref="e1", tag="input", role="", type="text", name="email",
                aria="", ph="you@example.com", text="", label="Email",
                value="", disabled=False, checked=False, href="", inView=True)
    base.update(over)
    return base


def test_fmt_ref_prefers_text_then_aria_then_label():
    assert hb._fmt_ref(_el(text="Save")) == '@e1 [textbox] "Save" label="Email" placeholder="you@example.com" name=email'
    assert '"Aria name"' in hb._fmt_ref(_el(aria="Aria name"))


def test_fmt_ref_shows_placeholder_and_name_separately():
    line = hb._fmt_ref(_el())
    assert 'placeholder="you@example.com"' in line
    assert "name=email" in line
    assert 'name=you@example.com' not in line


def test_fmt_ref_checkbox_has_no_value_noise():
    line = hb._fmt_ref(_el(type="checkbox", label="Accept terms", name="tos", checked=True))
    assert "[checkbox]" in line and '"Accept terms"' in line and "checked" in line
    assert "value=" not in line


def test_fmt_ref_kind_mapping():
    assert "[combobox]" in hb._fmt_ref(_el(tag="select", text="Free Pro"))
    assert "[link]" in hb._fmt_ref(_el(tag="a", text="", href="https://x.io"))
    assert "[button]" in hb._fmt_ref(_el(type="submit", text="Go"))
    assert "[radio]" in hb._fmt_ref(_el(type="radio", label="Yes"))




# --------------------------------------------------------------------------
# No-session error paths (no browser launched)
# --------------------------------------------------------------------------

def test_ops_without_session_fail_cleanly():
    hb._S.page = None
    for fn, args in [
        (hb.browser_tree, ()),
        (hb.browser_click_ref, ("e1",)),
        (hb.browser_fill_ref, ("e1", "x")),
        (hb.browser_select_ref, ("e1", "x")),
        (hb.browser_check_ref, ("e1", True)),
        (hb.browser_press_key, ("Enter",)),
        (hb.browser_scroll_page, ()),
        (hb.browser_wait_for, ()),
        (hb.browser_capture, ()),
        (hb.browser_eval, ("1",)),
    ]:
        out = fn(*args)
        assert "browser_open" in out, f"{fn.__name__}: {out}"
    assert "No headed browser session" in hb.browser_status()


def test_fill_secret_missing_vault_entry(monkeypatch):
    monkeypatch.setattr("tools.vault._kc_get", lambda service: None)
    out = hb.browser_fill_secret("e1", "no-such-service-xyz")
    assert out.startswith("[vault]")
    assert "no-such-service-xyz" in out


# --------------------------------------------------------------------------
# Schema + registry wiring
# --------------------------------------------------------------------------

EXPECTED_TOOLS = [
    "browser_open", "browser_tree", "browser_click_ref", "browser_fill_ref",
    "browser_fill_secret", "browser_select_ref", "browser_check_ref",
    "browser_press_key", "browser_scroll_page", "browser_wait_for",
    "browser_status", "browser_capture", "browser_eval", "browser_quit",
]


def test_all_schemas_well_formed():
    names = [s["function"]["name"] for s in hb.HEADED_BROWSER_SCHEMAS]
    assert sorted(names) == sorted(EXPECTED_TOOLS)
    for s in hb.HEADED_BROWSER_SCHEMAS:
        f = s["function"]
        assert f["description"] and f["parameters"]["type"] == "object"


def test_registry_wiring():
    from tools._registry.schemas import ALL_TOOLS
    from tools.registry import EXECUTORS, tools_with_tags
    names = [t["function"]["name"] for t in ALL_TOOLS]
    tagged = tools_with_tags("browser")
    for n in EXPECTED_TOOLS:
        assert n in names, f"{n} missing from ALL_TOOLS"
        assert n in EXECUTORS, f"{n} missing executor"
        assert n in tagged, f"{n} not browser-tagged"
    assert len(names) == len(set(names)), "duplicate tool names in registry"


def test_executors_dispatch():
    from tools.registry import EXECUTORS
    # executor calls the real function (no session -> clean error string)
    assert "browser_open" in EXECUTORS["browser_status"]({})

def test_fmt_ref_disabled_flag():
    assert "disabled" in hb._fmt_ref(_el(text="Nope", disabled=True))


# --------------------------------------------------------------------------
# End-to-end against a local form (needs playwright + browser binaries)
# --------------------------------------------------------------------------

FORM_HTML = """<html><head><title>Test Form</title></head><body>
<h1>Signup</h1>
<input id="email" name="email" type="email" placeholder="Email address">
<input id="pw" name="password" type="password" placeholder="Password">
<select id="plan" name="plan"><option value="free">Free</option><option value="pro">Pro</option></select>
<label><input id="tos" type="checkbox"> Accept terms</label>
<button id="go" type="button"
  onclick="document.getElementById('out').textContent='submitted:'+document.getElementById('email').value+':'+document.getElementById('plan').value">
Create account</button>
<div id="out"></div>
</body></html>"""


@pytest.mark.live
def test_end_to_end_form_flow(tmp_path):
    pytest.importorskip("playwright.sync_api")
    form = tmp_path / "form.html"
    form.write_text(FORM_HTML)
    try:
        out = hb.browser_open(form.as_uri(), headless=True)
        assert "Browser open" in out, out
        tree = hb.browser_tree()
        email_ref = next(l.split()[0][1:] for l in tree.splitlines() if "name=email" in l)
        assert "Filled" in hb.browser_fill_ref(email_ref, "agent@example.com")
        btn_ref = next(l.split()[0][1:] for l in tree.splitlines() if "Create account" in l)
        assert "Clicked" in hb.browser_click_ref(btn_ref)
        assert "submitted:agent@example.com" in hb.browser_eval(
            "document.getElementById('out').textContent")
    finally:
        hb.browser_quit()
    assert "No headed browser session" in hb.browser_status()
