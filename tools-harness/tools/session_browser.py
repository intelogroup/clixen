"""
Session-based browser automation for third-party services (DoorDash, Uber).
One login, persistent sessions. No API keys needed.

Flow:
    1. session_login("doordash")   → opens visible browser, user logs in manually (handles 2FA, CAPTCHA)
    2. browser_load_session("doordash")  → restore session in headless browser
    3. browser_navigate("https://www.doordash.com/orders/")  → browse authenticated pages
"""

from __future__ import annotations

import logging
import re
import time
from pathlib import Path

log = logging.getLogger("session_browser")

_SESSION_DIR = Path.home() / ".config" / "g4l" / "sessions"

_SERVICES = {
    "doordash": {
        "name": "DoorDash",
        "login_url": "https://www.doordash.com/auth/",
        "success_pattern": re.compile(r"(/home/?$|/orders/?$|/account/?$|/store/)"),
        "orders_url": "https://www.doordash.com/orders/",
    },
    "uber": {
        "name": "Uber",
        "login_url": "https://www.uber.com/login/",
        "success_pattern": re.compile(r"(riders|trips|m\.uber\.com|uber\.com/orders)"),
        "orders_url": "https://m.uber.com/",
    },
}

SESSION_LOGIN_SCHEMA = {
    "type": "function",
    "function": {
        "name": "session_login",
        "description": (
            "Log into a third-party service (DoorDash, Uber) in a visible browser window. "
            "YOU will see the browser open. Log in manually with your credentials. "
            "The tool waits for you to complete the login, handles 2FA/CAPTCHA naturally, "
            "and saves the session for future use. "
            "After this, call browser_load_session with the same service name to restore the session."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "service": {
                    "type": "string",
                    "enum": ["doordash", "uber"],
                    "description": "Service to log into",
                },
                "timeout_s": {
                    "type": "integer",
                    "description": "How long to wait for you to log in (seconds, default 120)",
                    "default": 120,
                },
            },
            "required": ["service"],
        },
    },
}

SESSION_CHECK_SCHEMA = {
    "type": "function",
    "function": {
        "name": "session_check",
        "description": (
            "Check if a saved browser session (cookies) is still valid for a service. "
            "Loads the session, navigates to a page that requires authentication, "
            "and reports whether the session worked or needs re-authentication."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "service": {
                    "type": "string",
                    "enum": ["doordash", "uber"],
                    "description": "Service to check",
                },
            },
            "required": ["service"],
        },
    },
}

SCHEMAS = [SESSION_LOGIN_SCHEMA, SESSION_CHECK_SCHEMA]


def _session_path(service: str) -> Path:
    return _SESSION_DIR / f"{service}.json"


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


def session_login(service: str, timeout_s: int = 120) -> str:
    """Open a visible browser, navigate to login page, wait for user to log in, save session."""
    svc = _SERVICES.get(service)
    if not svc:
        return f"[session] Unknown service: {service}. Choose: {', '.join(_SERVICES)}"

    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        return "[session] Playwright not installed. Run: pip install playwright && playwright install chromium"

    session_file = _session_path(service)
    _SESSION_DIR.mkdir(parents=True, exist_ok=True)

    log.info("session_login: opening %s at %s", service, svc["login_url"])

    try:
        pw = sync_playwright().start()

        # Try real Chrome profile first for persistent cookies
        profile_path = _get_chrome_profile_path()
        if profile_path:
            log.info("session_login: using real Chrome profile at %s", profile_path)
            page = None
            try:
                ctx = pw.chromium.launch_persistent_context(
                    user_data_dir=str(profile_path),
                    headless=False,
                    args=["--disable-blink-features=AutomationControlled"],
                )
                page = ctx.pages[0] if ctx.pages else ctx.new_page()
            except Exception as exc:
                log.info("session_login: Chrome profile locked (%s), using incognito", exc)
                profile_path = None

        if not profile_path:
            browser = pw.chromium.launch(
                headless=False,
                args=["--disable-blink-features=AutomationControlled"],
            )
            ctx_kwargs = {}
            if session_file.exists():
                ctx_kwargs["storage_state"] = str(session_file)
            ctx = browser.new_context(
                **ctx_kwargs,
                user_agent=(
                    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                    "AppleWebKit/537.36 (KHTML, like Gecko) "
                    "Chrome/125.0.0.0 Safari/537.36"
                ),
            )
            page = ctx.new_page()

        page.set_viewport_size({"width": 1440, "height": 900})
        page.goto(svc["login_url"], wait_until="domcontentloaded", timeout=30000)

        # Poll for login success
        deadline = time.time() + timeout_s
        logged_in = False
        while time.time() < deadline:
            time.sleep(1.5)
            try:
                current_url = page.url
                if svc["success_pattern"].search(current_url):
                    logged_in = True
                    break
            except Exception:
                pass

        if not logged_in:
            # Check one more time (user might have just logged in)
            try:
                current_url = page.url
                if svc["success_pattern"].search(current_url):
                    logged_in = True
            except Exception:
                pass

        if logged_in:
            ctx.storage_state(path=str(session_file))
            ctx.close() if profile_path else browser.close()
            pw.stop()
            log.info("session_login: %s login successful, session saved", service)
            return (
                f"Login to {svc['name']} successful! Session saved.\n"
                f"Your real Chrome profile now has the cookies, so browser_load_session is optional.\n"
                f"Next step: call browser_navigate to the service's dashboard."
            )
        else:
            ctx.close() if profile_path else browser.close()
            pw.stop()
            return (
                f"Login to {svc['name']} timed out after {timeout_s}s.\n"
                f"The browser opened to {svc['login_url']}. Log in manually and I'll detect it.\n"
                f"Try again with a longer timeout if needed."
            )

    except Exception as e:
        log.error("session_login failed: %s", e, exc_info=True)
        return f"[session] Login failed: {e}"


def session_check(service: str) -> str:
    """Check if a saved session is still valid."""
    from tools.browser import browser_load_session, browser_navigate, browser_get_url, browser_close

    svc = _SERVICES.get(service)
    if not svc:
        return f"[session] Unknown service: {service}"

    session_file = _session_path(service)

    try:
        r = browser_load_session(service)

        # Profile mode: jump straight to navigation check
        if "Chrome profile active" in r or "cookies persist" in r:
            pass  # proceed to check
        elif "No saved session" in r:
            if not session_file.exists():
                return f"[session] No saved session for {service}. Run session_login(service='{service}') first."
            return r
        else:
            # loaded from file
            pass

        r = browser_navigate(svc["login_url"])
        r2 = browser_get_url()
        if svc["success_pattern"].search(r2):
            return f"Session for {svc['name']} is VALID. You are logged in.\n{r2}"
        else:
            return (
                f"Session for {svc['name']} has EXPIRED or is invalid.\n"
                f"Browser is at: {r2}\n"
                f"Run session_login(service='{service}') to re-authenticate."
            )
    except Exception as e:
        return f"[session] Check failed: {e}"
