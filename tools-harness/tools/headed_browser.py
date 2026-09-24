"""
Headed browser agent tools — visible Chrome, accessibility-tree refs, vault-safe typing.

Why this module exists (2026-09-24):
  The legacy tools/browser.py session runs Playwright's sync API on whatever
  thread first calls it; the harness executes tool calls on pool threads, so a
  later call from a different thread crashes with Playwright's
  "cannot switch to a different thread" (greenlet thread-affinity). It also
  launches headless Chromium, which breaks interactive logins (2FA/CAPTCHA)
  and trips bot detection on real sites.

Design:
  * ONE dedicated worker thread owns the Playwright instance for its whole
    life. Every public tool function submits a closure to the worker queue and
    blocks on the result — callers may be on any thread.
  * Headed-first launch: real Chrome (channel="chrome") with a dedicated
    persistent profile (~/.config/g4l/headed-profile) so logins survive across
    runs without locking the user's everyday Chrome profile. Optional CDP
    attach to a user-started Chrome (CLIXEN_BROWSER_CDP_URL). Headless is an
    explicit opt-in (mode="headless" or headless=true) or last-resort
    fallback when no display is available.
  * Ref-based interaction (browser-use/Stagehand style): browser_tree() walks
    the DOM in-page, tags interactive elements with data-cx-ref attributes,
    and returns a compact a11y-style listing (@e3 [textbox] "Email"). Actions
    take refs, not CSS selectors — no selector guessing, resilient to
    obfuscated class names. Refs go stale when the DOM changes: re-snapshot
    after any navigation or major click.
  * browser_fill_secret() types credentials straight from the macOS Keychain
    vault (tools/vault.py) — the secret never enters model context or traces.

Env:
  CLIXEN_BROWSER_MODE        "headed" (default) | "headless" | "cdp"
  CLIXEN_BROWSER_CDP_URL     e.g. http://localhost:9222 (forces cdp mode when set)
  CLIXEN_BROWSER_SLOW_MO     ms delay between Playwright ops in headed mode (default 80)
  CLIXEN_BROWSER_OP_TIMEOUT  per-tool-call hard timeout in seconds (default 120)
"""

from __future__ import annotations

import json
import logging
import os
import queue
import threading
from pathlib import Path

log = logging.getLogger("headed_browser")

_PROFILE_DIR = Path.home() / ".config" / "g4l" / "headed-profile"


# ---------------------------------------------------------------------------
# Worker thread — ALL Playwright objects live and die on this one thread.
# ---------------------------------------------------------------------------


class _Worker:
    """Single persistent thread that executes every browser operation.

    Playwright's sync API is greenlet-bound to the thread that created it;
    routing all calls through one thread is the fix for the
    'cannot switch to a different thread' crash seen when tool calls ran on
    per-call ThreadPoolExecutor threads.
    """

    def __init__(self) -> None:
        self._q: queue.Queue = queue.Queue()
        self._thread: threading.Thread | None = None
        self._start_lock = threading.Lock()
        self.op_timeout = float(os.environ.get("CLIXEN_BROWSER_OP_TIMEOUT", "120"))

    def _ensure_started(self) -> None:
        with self._start_lock:
            if self._thread is None or not self._thread.is_alive():
                self._thread = threading.Thread(
                    target=self._loop, name="headed-browser-worker", daemon=True
                )
                self._thread.start()

    def _loop(self) -> None:
        while True:
            fn, args, kwargs, out = self._q.get()
            try:
                out.put(("ok", fn(*args, **kwargs)))
            except Exception as exc:  # noqa: BLE001 — surfaced to the tool caller
                out.put(("err", exc))

    def run(self, fn, *args, **kwargs):
        """Run fn on the worker thread and wait for the result. Raises on
        worker exception or timeout."""
        self._ensure_started()
        out: queue.Queue = queue.Queue(maxsize=1)
        self._q.put((fn, args, kwargs, out))
        try:
            status, val = out.get(timeout=self.op_timeout)
        except queue.Empty:
            raise RuntimeError(
                f"browser operation timed out after {self.op_timeout:.0f}s — "
                "the page may be hung; call browser_quit() and retry with browser_open()"
            )
        if status == "err":
            raise val
        return val


_WORKER = _Worker()


class _Session:
    """Module-level session state — only ever touched on the worker thread."""

    pw = None
    browser = None  # only set in cdp mode
    context = None
    page = None
    mode = ""  # "headed (chrome)" | "headed (chromium)" | "headless (...)" | "cdp"


_S = _Session()
_S = _Session()


# ---------------------------------------------------------------------------
# Launch / teardown (worker-thread only)
# ---------------------------------------------------------------------------


def _close_inner() -> None:
    """Tear down any existing session. In CDP mode this only disconnects —
    the user's own Chrome is never killed."""
    if _S.context is not None:
        try:
            _S.context.close()
        except Exception:
            pass
    if _S.browser is not None:
        try:
            _S.browser.close()  # connect_over_cdp: disconnects, does not kill Chrome
        except Exception:
            pass
    if _S.pw is not None:
        try:
            _S.pw.stop()
        except Exception:
            pass
    _S.pw = _S.browser = _S.context = _S.page = None
    _S.mode = ""


def _alive() -> bool:
    return _S.page is not None and not _S.page.is_closed()


def _launch_inner(headless: bool | None, url: str) -> str:
    _close_inner()
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        raise RuntimeError(
            "Playwright not installed. Run: pip install playwright && playwright install chromium"
        )

    mode_pref = os.environ.get("CLIXEN_BROWSER_MODE", "headed").strip().lower()
    cdp_url = os.environ.get("CLIXEN_BROWSER_CDP_URL", "").strip()
    if headless is True:
        mode_pref = "headless"
    elif headless is False:
        mode_pref = "headed"
    if cdp_url:
        mode_pref = "cdp"

    _S.pw = sync_playwright().start()

    if mode_pref == "cdp":
        try:
            _S.browser = _S.pw.chromium.connect_over_cdp(cdp_url or "http://localhost:9222")
            _S.context = _S.browser.contexts[0] if _S.browser.contexts else _S.browser.new_context()
            _S.mode = "cdp"
        except Exception as exc:
            try:
                _S.pw.stop()
            except Exception:
                pass
            _S.pw = None
            raise RuntimeError(
                f"CDP attach to {cdp_url} failed: {exc}. Start Chrome with "
                "--remote-debugging-port=9222, or unset CLIXEN_BROWSER_CDP_URL."
            )
    else:
        _PROFILE_DIR.mkdir(parents=True, exist_ok=True)
        slow_mo = int(os.environ.get("CLIXEN_BROWSER_SLOW_MO", "80") or 0)
        args = ["--disable-blink-features=AutomationControlled"]
        errors: list[str] = []
        headless_tries = [True] if mode_pref == "headless" else [False, True]
        for channel in ("chrome", None):
            for hl in headless_tries:
                try:
                    _S.context = _S.pw.chromium.launch_persistent_context(
                        user_data_dir=str(_PROFILE_DIR),
                        channel=channel,
                        headless=hl,
                        slow_mo=0 if hl else slow_mo,
                        args=args,
                        viewport={"width": 1440, "height": 900},
                        ignore_https_errors=True,
                    )
                    kind = "headless" if hl else "headed"
                    _S.mode = f"{kind} ({channel or 'chromium'})"
                    if hl and not headless:
                        log.warning("headed launch unavailable — fell back to headless")
                    break
                except Exception as exc:  # noqa: BLE001 — try next combination
                    errors.append(f"{channel or 'chromium'}/{'headless' if hl else 'headed'}: {exc}")
            if _S.context is not None:
                break
        if _S.context is None:
            try:
                _S.pw.stop()
            except Exception:
                pass
            _S.pw = None
            raise RuntimeError("all launch attempts failed: " + " | ".join(errors))

    _S.context.set_default_timeout(8000)
    _S.page = _S.context.pages[0] if _S.context.pages else _S.context.new_page()
    try:
        _S.page.on("console", lambda _: None)
    except Exception:
        pass
    if url:
        _goto_inner(url)
    return _S.mode


def _goto_inner(url: str, timeout: int = 30000) -> None:
    for wait_until in ("networkidle", "load", "domcontentloaded"):
        try:
            _S.page.goto(url, timeout=timeout, wait_until=wait_until)
            return
        except Exception:
            continue


def _settle(ms: int = 1500) -> None:
    try:
        _S.page.wait_for_load_state("networkidle", timeout=ms)
    except Exception:
        pass


def _require_page():
    if not _alive():
        raise RuntimeError("no headed browser session — call browser_open() first")
    return _S.page


def _locator_for(ref: str):
    page = _require_page()
    ref = ref.lstrip("@").strip()
    loc = page.locator(f'[data-cx-ref="{ref}"]').first
    if loc.count() == 0:
        raise RuntimeError(
            f"ref @{ref} not found — the DOM changed since the last snapshot; "
            "call browser_tree() again for fresh refs"
        )
    return loc, ref

    return loc, ref


# ---------------------------------------------------------------------------
# Ref snapshot — tag interactive elements in-page, return compact listing
# ---------------------------------------------------------------------------

_SNAPSHOT_JS = r"""
() => {
  // Clear stale refs from a previous snapshot
  document.querySelectorAll('[data-cx-ref]').forEach(el => el.removeAttribute('data-cx-ref'));
  const sel = [
    'a[href]', 'button', 'input:not([type=hidden])', 'select', 'textarea',
    '[role="button"]', '[role="link"]', '[role="textbox"]', '[role="checkbox"]',
    '[role="combobox"]', '[role="radio"]', '[role="tab"]', '[role="menuitem"]',
    '[role="switch"]', '[role="option"]', '[onclick]', '[contenteditable="true"]',
    'summary'
  ].join(',');
  const out = [];
  let i = 0;
  for (const el of document.querySelectorAll(sel)) {
    const style = getComputedStyle(el);
    if (style.display === 'none' || style.visibility === 'hidden') continue;
    const r = el.getBoundingClientRect();
    if (r.width === 0 || r.height === 0) continue;
    i++;
    const ref = 'e' + i;
    el.setAttribute('data-cx-ref', ref);
    const tag = el.tagName.toLowerCase();
    const role = el.getAttribute('role') || '';
    const type = (el.getAttribute('type') || '').toLowerCase();
    const isToggle = type === 'checkbox' || type === 'radio';
    const name = (el.getAttribute('name') || el.getAttribute('id') || '').slice(0, 60);
    const aria = (el.getAttribute('aria-label') || '').slice(0, 60);
    const ph = (el.getAttribute('placeholder') || '').slice(0, 60);
    let text = isToggle ? '' : (el.innerText || el.getAttribute('value') || '')
      .replace(/\s+/g, ' ').trim().slice(0, 80);
    const labelEl = el.labels && el.labels.length ? el.labels[0] : null;
    const label = labelEl ? (labelEl.innerText || '').replace(/\s+/g, ' ').trim().slice(0, 60) : '';
    const value = (tag === 'input' || tag === 'textarea') && type !== 'password' && !isToggle
      ? String(el.value || '').slice(0, 40) : '';
    out.push({
      ref, tag, role, type, name, aria, ph, text, label, value,
      disabled: !!el.disabled || el.getAttribute('aria-disabled') === 'true',
      checked: !!el.checked,
      href: tag === 'a' ? (el.getAttribute('href') || '').slice(0, 80) : '',
      inView: r.top < window.innerHeight && r.bottom > 0,
    });
  }
  return out;
}
"""


def _fmt_ref(el: dict) -> str:
    kind = el["role"] or (
        {"input": {"checkbox": "checkbox", "radio": "radio", "submit": "button",
                    "button": "button"}.get(el["type"], "textbox"),
         "select": "combobox", "textarea": "textbox", "a": "link"}.get(el["tag"], el["tag"])
    )
    if isinstance(kind, dict):
        kind = "textbox"
    label = el["text"] or el["aria"] or el["label"] or el["ph"] or el["name"] or el["href"]
    parts = [f'@{el["ref"]} [{kind}]']
    if label:
        parts.append(f'"{label}"')
    if el["label"] and el["text"]:
        parts.append(f'label="{el["label"]}"')
    if el["ph"] and el["ph"] != label:
        parts.append(f'placeholder="{el["ph"]}"')
    if el["name"] and el["name"] != label:
        parts.append(f'name={el["name"]}')
    if el["value"]:
        parts.append(f'value="{el["value"]}"')
    if el["checked"]:
        parts.append("checked")
    if el["disabled"]:
        parts.append("disabled")
    return " ".join(parts)


def _tree_inner(max_chars: int, interactive_only: bool) -> str:
    page = _require_page()
    elements = page.evaluate(_SNAPSHOT_JS)
    lines: list[str] = []
    offscreen = 0
    for el in elements:
        if interactive_only and not el["inView"]:
            offscreen += 1
            continue
        lines.append(_fmt_ref(el))
    body = "\n".join(lines)
    if len(body) > max_chars:
        body = body[:max_chars] + f"\n... (tree truncated at {max_chars} chars — scroll or lower max_chars)"
    header = f"Page: {page.title()} — {page.url}\nMode: {_S.mode} | {len(lines)} interactive elements"
    if offscreen:
        header += f" (+{offscreen} off-screen — use browser_scroll_page then browser_tree again)"
    return header + "\n" + body
    return header + "\n" + body


# ---------------------------------------------------------------------------
# Action internals (worker-thread only)
# ---------------------------------------------------------------------------


def _status_line() -> str:
    try:
        return f"url: {_S.page.url} | title: {_S.page.title()}"
    except Exception:
        return ""


def _click_inner(ref: str, force: bool) -> str:
    loc, ref = _locator_for(ref)
    loc.scroll_into_view_if_needed(timeout=3000)
    loc.click(force=force)
    _settle()
    return f"Clicked @{ref}. {_status_line()}"


def _fill_inner(ref: str, text: str, press_enter: bool) -> str:
    loc, ref = _locator_for(ref)
    loc.scroll_into_view_if_needed(timeout=3000)
    try:
        loc.fill(text, timeout=5000)
    except Exception:
        # contenteditable / non-standard input — click and type char by char
        loc.click(timeout=3000)
        _S.page.keyboard.press("ControlOrMeta+a")
        _S.page.keyboard.type(text, delay=20)
    if press_enter:
        _S.page.keyboard.press("Enter")
        _settle()
    return f"Filled @{ref} ({len(text)} chars). {_status_line()}"


def _fill_secret_inner(ref: str, service: str, field: str) -> str:
    from tools.vault import _kc_get

    data = _kc_get(service)
    if data is None:
        return (
            f"[vault] No credentials stored for '{service}'. "
            "Ask the user to provide them and save via vault_save — never ask them "
            "to paste a password into chat if the vault entry exists under another name "
            "(check with vault_list)."
        )
    value = data.get(field)
    if value is None:
        return (
            f"[vault] Credentials for '{service}' exist but have no '{field}' field. "
            f"Available fields: {list(data.keys())}"
        )
    loc, ref = _locator_for(ref)
    loc.scroll_into_view_if_needed(timeout=3000)
    try:
        loc.fill(str(value), timeout=5000)
    except Exception:
        loc.click(timeout=3000)
        _S.page.keyboard.press("ControlOrMeta+a")
        _S.page.keyboard.type(str(value), delay=20)
    # The secret is intentionally NOT included in this return value.
    return f"Filled @{ref} with '{field}' from vault service '{service}' (value hidden). {_status_line()}"


def _select_inner(ref: str, value: str) -> str:
    loc, ref = _locator_for(ref)
    loc.scroll_into_view_if_needed(timeout=3000)
    try:
        loc.select_option(value=value, timeout=4000)
    except Exception:
        loc.select_option(label=value, timeout=4000)
    return f"Selected '{value}' on @{ref}. {_status_line()}"


def _check_inner(ref: str, checked: bool) -> str:
    loc, ref = _locator_for(ref)
    loc.scroll_into_view_if_needed(timeout=3000)
    try:
        (loc.check if checked else loc.uncheck)(timeout=4000)
    except Exception:
        loc.click(force=True)
    return f"{'Checked' if checked else 'Unchecked'} @{ref}. {_status_line()}"


def _press_inner(key: str) -> str:
    _require_page()
    _S.page.keyboard.press(key)
    _settle(800)
    return f"Pressed {key}. {_status_line()}"


def _scroll_inner(direction: str, amount: int) -> str:
    _require_page()
    delta = amount if direction == "down" else -amount
    _S.page.mouse.wheel(0, delta)
    _settle(800)
    return f"Scrolled {direction} {amount}px. {_status_line()}"


def _wait_inner(text: str, selector: str, timeout_s: int) -> str:
    page = _require_page()
    timeout_ms = timeout_s * 1000
    if text:
        page.get_by_text(text, exact=False).first.wait_for(state="visible", timeout=timeout_ms)
        return f"Text appeared: '{text}'. {_status_line()}"
    if selector:
        page.wait_for_selector(selector, state="visible", timeout=timeout_ms)
        return f"Selector visible: {selector}. {_status_line()}"
    _settle(timeout_ms)
    return f"Waited {timeout_s}s. {_status_line()}"


def _capture_inner(path: str, full_page: bool) -> str:
    page = _require_page()
    if not path:
        path = str(Path.home() / "Downloads" / "clixen_headed_shot.png")
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    page.screenshot(path=path, full_page=full_page)
    return f"Screenshot saved: {path}"


def _eval_inner(js: str) -> str:
    page = _require_page()
    result = page.evaluate(js)
    out = json.dumps(result, default=str, ensure_ascii=False)
    return out if len(out) <= 4000 else out[:4000] + "... (eval result truncated)"

    return out if len(out) <= 4000 else out[:4000] + "... (eval result truncated)"


# ---------------------------------------------------------------------------
# Public tool functions — safe to call from ANY thread (they hop to the worker)
# ---------------------------------------------------------------------------


def _call(fn, *args, **kwargs) -> str:
    try:
        return _WORKER.run(fn, *args, **kwargs)
    except Exception as exc:  # noqa: BLE001 — tool errors return strings, never raise
        log.warning("headed_browser op failed: %s", exc)
        return f"[hbrowser] {exc}"


def browser_open(url: str = "", headless: bool | None = None) -> str:
    def _op():
        if _alive():
            if url:
                _goto_inner(url)
            return f"Reusing open browser (mode: {_S.mode}). {_status_line()}"
        mode = _launch_inner(headless, url)
        return f"Browser open (mode: {mode}). {_status_line()}"

    return _call(_op)


def browser_tree(max_chars: int = 6000, interactive_only: bool = True) -> str:
    return _call(_tree_inner, max_chars, interactive_only)


def browser_click_ref(ref: str, force: bool = False) -> str:
    return _call(_click_inner, ref, force)


def browser_fill_ref(ref: str, text: str, press_enter: bool = False) -> str:
    return _call(_fill_inner, ref, text, press_enter)


def browser_fill_secret(ref: str, service: str, field: str = "password") -> str:
    return _call(_fill_secret_inner, ref, service, field)


def browser_select_ref(ref: str, value: str) -> str:
    return _call(_select_inner, ref, value)


def browser_check_ref(ref: str, checked: bool = True) -> str:
    return _call(_check_inner, ref, checked)


def browser_press_key(key: str) -> str:
    return _call(_press_inner, key)


def browser_scroll_page(direction: str = "down", amount: int = 800) -> str:
    return _call(_scroll_inner, direction, amount)


def browser_wait_for(text: str = "", selector: str = "", timeout_s: int = 10) -> str:
    return _call(_wait_inner, text, selector, timeout_s)


def browser_status() -> str:
    def _op():
        if not _alive():
            return "No headed browser session open. Call browser_open() first."
        return f"Mode: {_S.mode} | {_status_line()}"

    return _call(_op)


def browser_capture(path: str = "", full_page: bool = False) -> str:
    return _call(_capture_inner, path, full_page)


def browser_eval(js: str) -> str:
    return _call(_eval_inner, js)


def browser_quit() -> str:
    def _op():
        had = _S.context is not None or _S.page is not None
        mode = _S.mode
        _close_inner()
        if not had:
            return "No headed browser session was open."
        if mode == "cdp":
            return "Disconnected from Chrome (CDP) — your Chrome window stays open."
        return "Browser closed. Persistent profile kept — logins survive the next browser_open()."

    return _call(_op)
    return _call(_op)


# ---------------------------------------------------------------------------
# Schemas
# ---------------------------------------------------------------------------

_COMMON_NOTE = (
    "This drives the VISIBLE (headed) browser session started by browser_open — "
    "separate from the headless browser_navigate/browser_snapshot tools. "
    "Prefer this session for logins, signups, and form filling."
)

BROWSER_OPEN_SCHEMA = {
    "type": "function",
    "function": {
        "name": "browser_open",
        "description": (
            "Start the VISIBLE browser (real Chrome window on the user's screen) for "
            "interactive automation: logins, signups, 2FA, CAPTCHAs, form filling. "
            "Uses a persistent Clixen profile, so sites you logged into before stay "
            "logged in. If CLIXEN_BROWSER_CDP_URL is set, attaches to the user's own "
            "running Chrome instead. Call this FIRST, then browser_tree to see the page. "
            + _COMMON_NOTE
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "url": {"type": "string", "description": "Optional URL to open immediately"},
                "headless": {
                    "type": "boolean",
                    "description": "Force headless (true) or headed (false). Omit to use env default (headed).",
                },
            },
            "required": [],
        },
    },
}

BROWSER_TREE_SCHEMA = {
    "type": "function",
    "function": {
        "name": "browser_tree",
        "description": (
            "Snapshot the current page as an accessibility-style tree of interactive "
            "elements with refs: '@e3 [textbox] \"Email\" name=email'. Use the @refs with "
            "browser_click_ref / browser_fill_ref / browser_fill_secret / etc. "
            "Call after every navigation and after any action that changes the DOM — "
            "refs go stale when the page changes. " + _COMMON_NOTE
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "max_chars": {"type": "integer", "description": "Truncation limit (default 6000)", "default": 6000},
                "interactive_only": {
                    "type": "boolean",
                    "description": "Only on-screen elements (default true). Set false to list every interactive element on the page.",
                    "default": True,
                },
            },
            "required": [],
        },
    },
}

BROWSER_CLICK_REF_SCHEMA = {
    "type": "function",
    "function": {
        "name": "browser_click_ref",
        "description": (
            "Click an element by its @ref from browser_tree (e.g. ref='e3' or '@e3'). "
            "Waits for the page to settle and reports the new URL/title. "
            "If the click changed the page, call browser_tree for fresh refs. " + _COMMON_NOTE
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "ref": {"type": "string", "description": "Element ref from browser_tree, e.g. 'e3'"},
                "force": {"type": "boolean", "description": "Click even if covered by an overlay (default false)", "default": False},
            },
            "required": ["ref"],
        },
    },
}
BROWSER_FILL_REF_SCHEMA = {
    "type": "function",
    "function": {
        "name": "browser_fill_ref",
        "description": (
            "Type text into an input/textarea/contenteditable by its @ref from browser_tree. "
            "For passwords/2FA codes stored in the vault, use browser_fill_secret instead "
            "so the secret never appears in the conversation. " + _COMMON_NOTE
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "ref": {"type": "string", "description": "Element ref from browser_tree"},
                "text": {"type": "string", "description": "Text to type (never put passwords here — use browser_fill_secret)"},
                "press_enter": {"type": "boolean", "description": "Press Enter after typing (default false)", "default": False},
            },
            "required": ["ref", "text"],
        },
    },
}

BROWSER_FILL_SECRET_SCHEMA = {
    "type": "function",
    "function": {
        "name": "browser_fill_secret",
        "description": (
            "Fill a field with a credential from the macOS Keychain vault (vault_save/vault_list) "
            "WITHOUT exposing it: the value is typed directly into the page and never enters "
            "the conversation. Use for passwords, 2FA seeds, card numbers. If the vault entry "
            "is missing, ask the user for the credentials and save them with vault_save first. "
            + _COMMON_NOTE
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "ref": {"type": "string", "description": "Element ref from browser_tree"},
                "service": {"type": "string", "description": "Vault service name (see vault_list), e.g. 'medicospira'"},
                "field": {"type": "string", "description": "Field inside the vault entry (default 'password'; 'username'/'email' also common)", "default": "password"},
            },
            "required": ["ref", "service"],
        },
    },
}

BROWSER_SELECT_REF_SCHEMA = {
    "type": "function",
    "function": {
        "name": "browser_select_ref",
        "description": "Pick an option in a <select> dropdown by @ref; accepts a value or visible label. " + _COMMON_NOTE,
        "parameters": {
            "type": "object",
            "properties": {
                "ref": {"type": "string", "description": "Element ref from browser_tree"},
                "value": {"type": "string", "description": "Option value or visible label"},
            },
            "required": ["ref", "value"],
        },
    },
}

BROWSER_CHECK_REF_SCHEMA = {
    "type": "function",
    "function": {
        "name": "browser_check_ref",
        "description": "Check or uncheck a checkbox/radio by @ref (falls back to a click for custom widgets). " + _COMMON_NOTE,
        "parameters": {
            "type": "object",
            "properties": {
                "ref": {"type": "string", "description": "Element ref from browser_tree"},
                "checked": {"type": "boolean", "description": "true to check, false to uncheck (default true)", "default": True},
            },
            "required": ["ref"],
        },
    },
}
BROWSER_PRESS_KEY_SCHEMA = {
    "type": "function",
    "function": {
        "name": "browser_press_key",
        "description": (
            "Press a keyboard key on the page (Enter, Tab, Escape, ArrowDown, Backspace...). "
            "Useful to dismiss overlays (Escape), move focus (Tab), or submit (Enter). " + _COMMON_NOTE
        ),
        "parameters": {
            "type": "object",
            "properties": {"key": {"type": "string", "description": "Playwright key name, e.g. 'Enter'"}},
            "required": ["key"],
        },
    },
}

BROWSER_SCROLL_PAGE_SCHEMA = {
    "type": "function",
    "function": {
        "name": "browser_scroll_page",
        "description": "Scroll the page up/down, then call browser_tree to see newly visible elements. " + _COMMON_NOTE,
        "parameters": {
            "type": "object",
            "properties": {
                "direction": {"type": "string", "enum": ["up", "down"], "description": "Scroll direction (default down)", "default": "down"},
                "amount": {"type": "integer", "description": "Pixels to scroll (default 800)", "default": 800},
            },
            "required": [],
        },
    },
}

BROWSER_WAIT_FOR_SCHEMA = {
    "type": "function",
    "function": {
        "name": "browser_wait_for",
        "description": (
            "Wait until some text (e.g. 'Welcome back') or a CSS selector is visible, "
            "or just idle-wait N seconds for the page to settle. " + _COMMON_NOTE
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "text": {"type": "string", "description": "Visible text to wait for (optional)"},
                "selector": {"type": "string", "description": "CSS selector to wait for (optional)"},
                "timeout_s": {"type": "integer", "description": "Max seconds to wait (default 10)", "default": 10},
            },
            "required": [],
        },
    },
}

BROWSER_STATUS_SCHEMA = {
    "type": "function",
    "function": {
        "name": "browser_status",
        "description": "Report the headed session's mode, current URL and page title — e.g. to verify a login redirect landed. " + _COMMON_NOTE,
        "parameters": {"type": "object", "properties": {}, "required": []},
    },
}

BROWSER_CAPTURE_SCHEMA = {
    "type": "function",
    "function": {
        "name": "browser_capture",
        "description": (
            "Screenshot the headed browser to a PNG file. Use when the tree is ambiguous "
            "(canvas/map/custom widget) — then analyse it with the vision agent. " + _COMMON_NOTE
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "path": {"type": "string", "description": "Output path (default ~/Downloads/clixen_headed_shot.png)"},
                "full_page": {"type": "boolean", "description": "Capture full scroll height (default false)", "default": False},
            },
            "required": [],
        },
    },
}

BROWSER_EVAL_SCHEMA = {
    "type": "function",
    "function": {
        "name": "browser_eval",
        "description": (
            "Run JavaScript in the headed page and return the JSON result. Escape hatch for "
            "SPA state, jQuery triggers, or reading values the tree doesn't show. " + _COMMON_NOTE
        ),
        "parameters": {
            "type": "object",
            "properties": {"js": {"type": "string", "description": "JS expression or (() => {...})() to evaluate"}},
            "required": ["js"],
        },
    },
}

BROWSER_QUIT_SCHEMA = {
    "type": "function",
    "function": {
        "name": "browser_quit",
        "description": (
            "Close the headed browser session (or disconnect from CDP without killing the "
            "user's Chrome). Call when the task is done. Logins persist via the profile. " + _COMMON_NOTE
        ),
        "parameters": {"type": "object", "properties": {}, "required": []},
    },
}

HEADED_BROWSER_SCHEMAS = [
    BROWSER_OPEN_SCHEMA,
    BROWSER_TREE_SCHEMA,
    BROWSER_CLICK_REF_SCHEMA,
    BROWSER_FILL_REF_SCHEMA,
    BROWSER_FILL_SECRET_SCHEMA,
    BROWSER_SELECT_REF_SCHEMA,
    BROWSER_CHECK_REF_SCHEMA,
    BROWSER_PRESS_KEY_SCHEMA,
    BROWSER_SCROLL_PAGE_SCHEMA,
    BROWSER_WAIT_FOR_SCHEMA,
    BROWSER_STATUS_SCHEMA,
    BROWSER_CAPTURE_SCHEMA,
    BROWSER_EVAL_SCHEMA,
    BROWSER_QUIT_SCHEMA,
]
