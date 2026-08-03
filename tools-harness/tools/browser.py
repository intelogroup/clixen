"""
Browser automation tools using Playwright + real Chrome profile for stealth & persistence.

Stateful session: one browser instance per harness process, reused across tool calls.
Uses your real Chrome profile (bookmarks, passwords, cookies) when Chrome is closed.
Falls back to incognito when Chrome is running.

Typical flow for a login + action task:
  1. browser_navigate(url)              -> land on page
  2. browser_snapshot()                 -> see page structure + find selectors
  3. browser_type(selector, text)       -> fill fields
  4. browser_click(selector)            -> click buttons / links
  5. browser_wait(selector)             -> wait for result to appear
  6. browser_get_content(selector)      -> extract visible text
  7. browser_close()                    -> release session

For SPA / JS-heavy sites (jQuery, React, Vue):
  - Use browser_run_js() to trigger jQuery events, set values, or inspect JS state.
  - Use browser_check() for checkboxes that ignore browser_click().
  - Use browser_get_attribute() to read href, value, data-* from elements.
"""

from __future__ import annotations

import json
import re
import threading
import time
from pathlib import Path

_pw_lock = threading.Lock()      # protects browser init/teardown
_op_lock = threading.Lock()      # serialises all browser operations (Playwright is not thread-safe)


def _serialised(fn):
    """Decorator: acquire _op_lock around any browser operation."""
    import functools
    @functools.wraps(fn)
    def wrapper(*args, **kwargs):
        with _op_lock:
            return fn(*args, **kwargs)
    return wrapper
_browser = None
_page = None
_context = None
_playwright_ctx = None
_is_profile_mode = False

_SESSION_DIR = Path.home() / ".config" / "g4l" / "sessions"

_log = __import__("logging").getLogger(__name__)


def _get_chrome_profile_path() -> Path | None:
    """Find Chrome's default user data directory on this machine."""
    candidates = [
        Path.home() / "Library" / "Application Support" / "Google" / "Chrome",
        Path.home() / ".config" / "google-chrome",
    ]
    for p in candidates:
        if (p / "Default").exists() or list(p.glob("Profile *")):
            return p
    return None


_CHROME_PROFILE = _get_chrome_profile_path()


def _get_page():
    """Return the current page, launching a new browser session if needed.
    
    Uses real Chrome profile for persistent cookies when Chrome is closed.
    Falls back to incognito when profile is locked (Chrome running).
    """
    global _browser, _page, _context, _playwright_ctx, _is_profile_mode
    with _pw_lock:
        if _page is not None and not _page.is_closed():
            return _page
        try:
            from playwright.sync_api import sync_playwright
        except ImportError:
            raise RuntimeError(
                "Playwright not installed. "
                "Run: pip install playwright && playwright install chromium"
            )
        # Tear down any stale state
        for obj in (_playwright_ctx, _context, _browser):
            if obj is not None:
                try:
                    obj.close() if hasattr(obj, "close") else obj.stop()
                except Exception:
                    pass
        _context = _browser = _page = _playwright_ctx = None
        _is_profile_mode = False

        _playwright_ctx = sync_playwright().start()

        # Try real Chrome profile for persistent cookies + undetectable browsing
        if _CHROME_PROFILE:
            try:
                _context = _playwright_ctx.chromium.launch_persistent_context(
                    user_data_dir=str(_CHROME_PROFILE),
                    headless=True,
                    args=["--disable-blink-features=AutomationControlled"],
                )
                _is_profile_mode = True
                _browser = None
                _page = _context.pages[0] if _context.pages else _context.new_page()
                _page.set_viewport_size({"width": 1440, "height": 900})
                _page.on("console", lambda _: None)
                return _page
            except Exception as exc:
                _log.info("Chrome profile locked (%s), using incognito", exc)

        # Fallback: incognito with custom user-agent
        _browser = _playwright_ctx.chromium.launch(
            headless=True,
            args=["--disable-blink-features=AutomationControlled"],
        )
        _context = _browser.new_context(
            user_agent=(
                "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/125.0.0.0 Safari/537.36"
            ),
        )
        _page = _context.new_page()
        _page.set_viewport_size({"width": 1440, "height": 900})
        _page.on("console", lambda _: None)
        return _page


def _clean_snapshot(html: str, max_chars: int = 6000) -> str:
    """Strip non-content tags and collapse whitespace for LLM readability."""
    html = re.sub(
        r"<(script|style|svg|head|noscript|iframe)[^>]*>.*?</\1>",
        "", html, flags=re.DOTALL | re.IGNORECASE,
    )
    html = re.sub(r"<!--.*?-->", "", html, flags=re.DOTALL)
    html = re.sub(r"\s{2,}", " ", html)
    html = re.sub(r">\s+<", "><", html)
    if len(html) > max_chars:
        html = html[:max_chars] + f"\n... (snapshot truncated at {max_chars} chars)"
    return html.strip()


def _wait_settle(page, timeout_ms: int = 6000) -> None:
    """Best-effort wait for the page to stop loading after an action."""
    try:
        page.wait_for_load_state("networkidle", timeout=timeout_ms)
    except Exception:
        try:
            page.wait_for_load_state("domcontentloaded", timeout=2000)
        except Exception:
            pass


# ---------------------------------------------------------------------------
# Schemas
# ---------------------------------------------------------------------------

BROWSER_NAVIGATE_SCHEMA = {
    "type": "function",
    "function": {
        "name": "browser_navigate",
        "description": (
            "Open a URL in the headless browser and wait for the page to fully load "
            "(including JS-rendered content). Returns the page title and final URL.\n"
            "Always call browser_snapshot() after this to see the page structure and find selectors."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "url": {
                    "type": "string",
                    "description": "Full URL including https://",
                },
                "timeout": {
                    "type": "integer",
                    "description": "Max wait in ms (default 20000)",
                    "default": 20000,
                },
            },
            "required": ["url"],
        },
    },
}

BROWSER_SNAPSHOT_SCHEMA = {
    "type": "function",
    "function": {
        "name": "browser_snapshot",
        "description": (
            "Get a simplified HTML snapshot of the current page or a specific element.\n"
            "Scripts, styles, and SVGs are stripped — what remains shows form fields, "
            "buttons, links, and IDs you can use as CSS selectors.\n"
            "Use after navigating, after clicking, or to inspect a specific area with selector=."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "selector": {
                    "type": "string",
                    "description": "CSS selector to snapshot only that element (omit for full page)",
                    "default": "",
                },
                "max_chars": {
                    "type": "integer",
                    "description": "Truncation limit (default 6000)",
                    "default": 6000,
                },
            },
            "required": [],
        },
    },
}

BROWSER_GET_URL_SCHEMA = {
    "type": "function",
    "function": {
        "name": "browser_get_url",
        "description": (
            "Return the current page URL and title. "
            "Use this to check where the browser is after a navigation or redirect, "
            "or to verify a login succeeded."
        ),
        "parameters": {
            "type": "object",
            "properties": {},
            "required": [],
        },
    },
}

BROWSER_CLICK_SCHEMA = {
    "type": "function",
    "function": {
        "name": "browser_click",
        "description": (
            "Click an element on the current page. "
            "Accepts CSS selectors ('#id', '.class', 'button[type=submit]') "
            "or Playwright text selectors ('text=Sign In').\n"
            "If clicking triggers a page navigation, the tool waits for load to complete "
            "and reports the new URL.\n"
            "Use force=true for elements that are off-screen or covered by overlays."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "selector": {
                    "type": "string",
                    "description": "CSS or text selector of the element to click",
                },
                "timeout": {
                    "type": "integer",
                    "description": "Max wait for element to be clickable, in ms (default 5000)",
                    "default": 5000,
                },
                "force": {
                    "type": "boolean",
                    "description": "Force click even if element is off-screen or behind overlay (default false)",
                    "default": False,
                },
            },
            "required": ["selector"],
        },
    },
}

BROWSER_TYPE_SCHEMA = {
    "type": "function",
    "function": {
        "name": "browser_type",
        "description": (
            "Clear an input field and type new text into it. "
            "Works on <input>, <textarea>, and contenteditable elements.\n"
            "Selectors: 'input[name=email]', '#password', 'textarea.notes', etc.\n"
            "For login: browser_type(email field) → browser_type(password field) → browser_click(submit)."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "selector": {
                    "type": "string",
                    "description": "CSS selector of the input element",
                },
                "text": {
                    "type": "string",
                    "description": "Text to type",
                },
                "timeout": {
                    "type": "integer",
                    "description": "Max wait for element, in ms (default 5000)",
                    "default": 5000,
                },
            },
            "required": ["selector", "text"],
        },
    },
}

BROWSER_CHECK_SCHEMA = {
    "type": "function",
    "function": {
        "name": "browser_check",
        "description": (
            "Check or uncheck a checkbox or radio button. "
            "More reliable than browser_click for checkboxes — handles disabled/hidden/off-screen inputs.\n"
            "Use check=true to select, check=false to deselect.\n"
            "For 'Select All' checkboxes that drive jQuery events, use browser_run_js instead."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "selector": {
                    "type": "string",
                    "description": "CSS selector of the checkbox or radio input",
                },
                "check": {
                    "type": "boolean",
                    "description": "True to check, False to uncheck (default true)",
                    "default": True,
                },
            },
            "required": ["selector"],
        },
    },
}

BROWSER_SELECT_SCHEMA = {
    "type": "function",
    "function": {
        "name": "browser_select",
        "description": (
            "Select an option in a <select> dropdown by value or label. "
            "Use browser_snapshot to find the selector and available option values."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "selector": {
                    "type": "string",
                    "description": "CSS selector of the <select> element",
                },
                "value": {
                    "type": "string",
                    "description": "Option value or visible label text to select",
                },
            },
            "required": ["selector", "value"],
        },
    },
}

BROWSER_WAIT_SCHEMA = {
    "type": "function",
    "function": {
        "name": "browser_wait",
        "description": (
            "Wait for a condition before proceeding:\n"
            "- selector given: waits for that element to appear in the DOM\n"
            "- selector empty: waits for network to go idle (all XHR/fetch done)\n"
            "Use after browser_run_js triggers an AJAX action or page transition."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "selector": {
                    "type": "string",
                    "description": "CSS selector to wait for (empty = wait for network idle)",
                    "default": "",
                },
                "timeout": {
                    "type": "integer",
                    "description": "Max wait in ms (default 10000)",
                    "default": 10000,
                },
            },
            "required": [],
        },
    },
}

BROWSER_GET_CONTENT_SCHEMA = {
    "type": "function",
    "function": {
        "name": "browser_get_content",
        "description": (
            "Extract visible text from the current page or a specific element. "
            "Returns clean text (no HTML tags). "
            "Use a CSS selector to target a specific section, table, card, or div. "
            "Omit selector for the full page body."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "selector": {
                    "type": "string",
                    "description": "CSS selector (empty = full page body)",
                    "default": "",
                },
                "max_chars": {
                    "type": "integer",
                    "description": "Max characters to return (default 4000)",
                    "default": 4000,
                },
            },
            "required": [],
        },
    },
}

BROWSER_GET_ATTRIBUTE_SCHEMA = {
    "type": "function",
    "function": {
        "name": "browser_get_attribute",
        "description": (
            "Read an attribute from a DOM element — href, src, value, data-id, class, etc.\n"
            "Returns the attribute string value, or an error if the element/attribute is not found.\n"
            "Example: browser_get_attribute('#login-form', 'action') returns the form's POST URL."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "selector": {
                    "type": "string",
                    "description": "CSS selector of the element",
                },
                "attribute": {
                    "type": "string",
                    "description": "Attribute name (e.g. 'href', 'value', 'data-id', 'class')",
                },
            },
            "required": ["selector", "attribute"],
        },
    },
}

BROWSER_RUN_JS_SCHEMA = {
    "type": "function",
    "function": {
        "name": "browser_run_js",
        "description": (
            "Execute a JavaScript expression in the current page context and return the result as JSON.\n"
            "Use for:\n"
            "  - Triggering jQuery events: \"$('.selectAll').prop('checked',true).trigger('change')\"\n"
            "  - Reading JS variables: \"questionsForSpecifiedSubjects.length\"\n"
            "  - Setting input values with change events: \"$('#qty').val('5').trigger('change')\"\n"
            "  - Clicking elements off-screen: \"document.getElementById('tab').click()\"\n"
            "  - Reading computed state not visible in HTML\n"
            "Returns: JSON-serialized result of the expression, or '[void]' for statements."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "script": {
                    "type": "string",
                    "description": "JavaScript expression to run (jQuery $ is available if the page uses it)",
                },
            },
            "required": ["script"],
        },
    },
}

BROWSER_SCREENSHOT_SCHEMA = {
    "type": "function",
    "function": {
        "name": "browser_screenshot",
        "description": (
            "Take a screenshot of the current page and save it to a file. "
            "Returns the file path. "
            "Useful for debugging layout issues or capturing a visual record of the page state."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "path": {
                    "type": "string",
                    "description": "File path to save the PNG (default: ~/Downloads/browser_shot.png)",
                    "default": "",
                },
                "full_page": {
                    "type": "boolean",
                    "description": "Capture the full scrollable page (default false = viewport only)",
                    "default": False,
                },
            },
            "required": [],
        },
    },
}

BROWSER_CLOSE_SCHEMA = {
    "type": "function",
    "function": {
        "name": "browser_close",
        "description": (
            "Close the headless browser and release all resources. "
            "The next browser tool call will open a fresh session."
        ),
        "parameters": {
            "type": "object",
            "properties": {},
            "required": [],
        },
    },
}

BROWSER_SAVE_SESSION_SCHEMA = {
    "type": "function",
    "function": {
        "name": "browser_save_session",
        "description": (
            "Save the current browser session (cookies + localStorage) to disk. "
            "Use this AFTER logging into a site so you can restore the session later without re-authenticating. "
            "Sessions are saved to ~/.config/g4l/sessions/<name>.json"
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "name": {
                    "type": "string",
                    "description": "Session name (e.g. 'doordash', 'uber', 'default')",
                    "default": "default",
                },
            },
            "required": [],
        },
    },
}

BROWSER_LOAD_SESSION_SCHEMA = {
    "type": "function",
    "function": {
        "name": "browser_load_session",
        "description": (
            "Restore a previously saved browser session (cookies + localStorage) from disk. "
            "Use this BEFORE navigating to a site you've already logged into. "
            "If the session is expired, you'll need to log in again and save a new session."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "name": {
                    "type": "string",
                    "description": "Session name (e.g. 'doordash', 'uber', 'default')",
                    "default": "default",
                },
            },
            "required": [],
        },
    },
}

ALL_BROWSER_SCHEMAS = [
    BROWSER_NAVIGATE_SCHEMA,
    BROWSER_SNAPSHOT_SCHEMA,
    BROWSER_GET_URL_SCHEMA,
    BROWSER_CLICK_SCHEMA,
    BROWSER_TYPE_SCHEMA,
    BROWSER_CHECK_SCHEMA,
    BROWSER_SELECT_SCHEMA,
    BROWSER_WAIT_SCHEMA,
    BROWSER_GET_CONTENT_SCHEMA,
    BROWSER_GET_ATTRIBUTE_SCHEMA,
    BROWSER_RUN_JS_SCHEMA,
    BROWSER_SCREENSHOT_SCHEMA,
    BROWSER_SAVE_SESSION_SCHEMA,
    BROWSER_LOAD_SESSION_SCHEMA,
    BROWSER_CLOSE_SCHEMA,
]


# ---------------------------------------------------------------------------
# Executors
# ---------------------------------------------------------------------------

@_serialised
def browser_navigate(url: str, timeout: int = 20000) -> str:
    try:
        page = _get_page()
        try:
            page.goto(url, timeout=timeout, wait_until="networkidle")
        except Exception:
            # networkidle can hang on sites with long-poll XHR — fall back
            try:
                page.goto(url, timeout=timeout, wait_until="load")
            except Exception:
                page.goto(url, timeout=timeout, wait_until="domcontentloaded")
        title = page.title()
        return f"URL: {page.url}\nTitle: {title}"
    except Exception as e:
        return f"[browser error] navigate: {e}"


@_serialised
def browser_snapshot(selector: str = "", max_chars: int = 6000) -> str:
    try:
        page = _get_page()
        if selector:
            el = page.query_selector(selector)
            if not el:
                return f"[browser error] selector not found: {selector}"
            html = el.inner_html()
        else:
            html = page.content()
        return _clean_snapshot(html, max_chars)
    except Exception as e:
        return f"[browser error] snapshot: {e}"


@_serialised
def browser_get_url() -> str:
    try:
        page = _get_page()
        return f"URL: {page.url}\nTitle: {page.title()}"
    except Exception as e:
        return f"[browser error] get_url: {e}"


@_serialised
def browser_click(selector: str, timeout: int = 5000, force: bool = False) -> str:
    try:
        page = _get_page()
        url_before = page.url
        page.click(selector, timeout=timeout, force=force)
        # Wait for any triggered navigation to settle
        _wait_settle(page, timeout_ms=8000)
        url_after = page.url
        nav = f" → {url_after}" if url_after != url_before else ""
        return f"Clicked '{selector}'{nav}"
    except Exception as e:
        return f"[browser error] click '{selector}': {e}"


@_serialised
def browser_type(selector: str, text: str, timeout: int = 5000) -> str:
    try:
        page = _get_page()
        page.fill(selector, text, timeout=timeout)
        return f"Typed into '{selector}'"
    except Exception as e:
        return f"[browser error] type '{selector}': {e}"


@_serialised
def browser_check(selector: str, check: bool = True) -> str:
    try:
        page = _get_page()
        if check:
            page.check(selector, force=True)
            return f"Checked: {selector}"
        else:
            page.uncheck(selector, force=True)
            return f"Unchecked: {selector}"
    except Exception as e:
        return f"[browser error] check '{selector}': {e}"


@_serialised
def browser_select(selector: str, value: str) -> str:
    try:
        page = _get_page()
        page.select_option(selector, value)
        return f"Selected '{value}' in '{selector}'"
    except Exception as e:
        return f"[browser error] select '{selector}': {e}"


@_serialised
def browser_wait(selector: str = "", timeout: int = 10000) -> str:
    try:
        page = _get_page()
        if selector:
            page.wait_for_selector(selector, timeout=timeout)
            return f"Element appeared: {selector}"
        else:
            page.wait_for_load_state("networkidle", timeout=timeout)
            return f"Network idle. URL: {page.url}"
    except Exception as e:
        return f"[browser error] wait: {e}"


@_serialised
def browser_get_content(selector: str = "", max_chars: int = 4000) -> str:
    try:
        page = _get_page()
        if selector:
            el = page.query_selector(selector)
            if not el:
                return f"[browser error] selector not found: {selector}"
            text = el.inner_text()
        else:
            text = page.inner_text("body")
        text = re.sub(r"\s{3,}", "\n\n", text).strip()
        if len(text) > max_chars:
            text = text[:max_chars] + f"\n... (truncated at {max_chars} chars)"
        return text
    except Exception as e:
        return f"[browser error] get_content: {e}"


@_serialised
def browser_get_attribute(selector: str, attribute: str) -> str:
    try:
        page = _get_page()
        el = page.query_selector(selector)
        if not el:
            return f"[browser error] selector not found: {selector}"
        val = el.get_attribute(attribute)
        if val is None:
            return f"[browser error] attribute '{attribute}' not found on '{selector}'"
        return val
    except Exception as e:
        return f"[browser error] get_attribute: {e}"


@_serialised
def browser_run_js(script: str) -> str:
    """Run JavaScript in the page context. Handles both expressions and statement blocks."""
    try:
        page = _get_page()
        stripped = script.strip()
        # Determine wrapping strategy:
        #   - Script has `;` or explicit `return` → statement block (IIFE body, no auto-return)
        #   - Otherwise → single expression, possibly multi-line (JSON.stringify, Array.from...)
        _is_block = stripped.count(";") >= 1 or "return " in stripped
        if _is_block:
            wrapped = f"(function(){{\ntry {{\n{stripped}\n}} catch(e){{ return String(e); }}\n}})()"
        else:
            wrapped = f"(function(){{ try {{ return ({stripped}); }} catch(e) {{ return String(e); }} }})()"
        result = page.evaluate(wrapped)
        if result is None:
            return "[void]"
        return json.dumps(result, default=str)
    except Exception as e:
        return f"[browser error] run_js: {e}"


@_serialised
def browser_screenshot(path: str = "", full_page: bool = False) -> str:
    try:
        page = _get_page()
        if not path:
            path = str(Path.home() / "Downloads" / "browser_shot.png")
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        page.screenshot(path=path, full_page=full_page)
        return f"Screenshot saved: {path}"
    except Exception as e:
        return f"[browser error] screenshot: {e}"


@_serialised
def browser_save_session(name: str = "default") -> str:
    """Save current browser session (cookies + localStorage) to disk."""
    try:
        page = _get_page()
        _SESSION_DIR.mkdir(parents=True, exist_ok=True)
        path = _SESSION_DIR / f"{name}.json"
        page.context.storage_state(path=str(path))
        return f"Session saved: {path}"
    except Exception as e:
        return f"[browser error] save_session: {e}"


@_serialised
def browser_load_session(name: str = "default") -> str:
    """Load a saved session from disk and restore cookies.

    In profile mode (real Chrome cookies are already present) this is a no-op.
    In incognito mode, reloads the browser with the saved storage_state.
    """
    global _browser, _page, _context, _playwright_ctx, _is_profile_mode

    path = _SESSION_DIR / f"{name}.json"

    # Profile mode: cookies persist naturally, nothing to load
    if _is_profile_mode:
        if path.exists():
            return f"Session already active via Chrome profile. Use browser_navigate to go to the site."
        return f"Chrome profile active (cookies persist). No saved session file needed."

    if not path.exists():
        return f"No saved session found: {path}"

    try:
        browser_close_inner()

        from playwright.sync_api import sync_playwright
        _playwright_ctx = sync_playwright().start()
        _browser = _playwright_ctx.chromium.launch(
            headless=True,
            args=["--disable-blink-features=AutomationControlled"],
        )
        _context = _browser.new_context(
            storage_state=str(path),
            user_agent=(
                "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/125.0.0.0 Safari/537.36"
            ),
        )
        _page = _context.new_page()
        _page.set_viewport_size({"width": 1440, "height": 900})
        _page.on("console", lambda _: None)
        return f"Session loaded: {path} ({name})"
    except Exception as e:
        return f"[browser error] load_session: {e}"


def browser_close_inner():
    """Internal teardown — no lock, no side effects."""
    global _browser, _page, _context, _playwright_ctx, _is_profile_mode
    if _is_profile_mode and _context is not None:
        try:
            _context.close()
        except Exception:
            pass
    else:
        for obj, attr in ((_context, "close"), (_browser, "close")):
            if obj is not None:
                try:
                    getattr(obj, attr)()
                except Exception:
                    pass
    if _playwright_ctx is not None:
        try:
            _playwright_ctx.stop()
        except Exception:
            pass
    _is_profile_mode = False
    _context = _browser = _page = _playwright_ctx = None


def browser_close() -> str:
    global _browser, _page, _context, _playwright_ctx, _is_profile_mode
    with _pw_lock:
        browser_close_inner()
    return "Browser session closed."
