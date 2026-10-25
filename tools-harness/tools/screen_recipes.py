"""Recipes: known jobs as typed functions. They drive the real screen through observe/act (the bunny does every click),
check what they did, and report the evidence. The model supplies the content; the recipe supplies the procedure."""
import json
import re
import time
import urllib.parse

from tools import screen_v2 as v2

_ADDRESS = re.compile(r"^[^@\s,;]+@[^@\s,;]+\.[^@\s,;]+$")


def _open(url: str) -> None:
    from tools.registry import EXECUTORS

    EXECUTORS["open_url"]({"url": url, "app": "Google Chrome"})


def _sent_since(to: str, subject: str, since: float) -> str:
    """Independent proof: the Gmail API (read-only here) lists a message from me to `to` newer than `since`."""
    from tools.gmail import list_emails

    return list_emails(f'from:me to:{to} after:{int(since)} subject:"{subject[:60].replace(chr(34), "")}"', 3)


def _fail(code: str, hint: str, **extra) -> str:
    return json.dumps({"ok": False, "error": {"code": code, "hint": hint, **extra}}, ensure_ascii=False)


def _gmail_send(args: dict) -> str:
    to, subject, body = str(args.get("to", "")).strip(), str(args.get("subject", "")).strip(), str(args.get("body", "")).strip()
    if not _ADDRESS.match(to):
        return _fail("bad_args", f"'to' must be one email address, got '{to}'.")
    if not subject or len(subject) > 200:
        return _fail("bad_args", "'subject' is required (200 characters at most).")
    if not body or len(body) > 1500:
        return _fail("bad_args", "'body' is required (1500 characters at most; the compose link carries it).")
    since = time.time() - 5
    _open("https://mail.google.com/mail/?view=cm&fs=1&to=" + urllib.parse.quote(to, safe="") + "&su=" + urllib.parse.quote(subject, safe="")
          + "&body=" + urllib.parse.quote(body, safe=""))
    send = None
    labels: list[str] = []
    for _ in range(14):  # the compose page takes a few seconds to render its Send button
        obs = json.loads(v2.observe("Google Chrome"))
        labels = [e["label"] for e in obs["elements"]]
        send = next((e for e in obs["elements"] if e["role"] == "Button" and e["label"].lower().startswith("send") and "option" not in e["label"].lower()), None)
        if send:
            break
        time.sleep(0.7)
    if not send:
        return _fail("compose_not_ready", "Gmail's compose page never showed a Send button.", seen=labels[:12])
    clicked = json.loads(v2.act(target=send["id"], action="click", expect={"appears": "Message sent"}))
    confirmed_by = None
    for attempt in range(6):
        if "ID:" in _sent_since(to, subject, since):
            confirmed_by = "gmail_api"
            break
        if attempt == 0 and clicked.get("expect") == "met":
            confirmed_by = "toast"
            break
        time.sleep(2)
    return json.dumps({"ok": True, "sent": confirmed_by is not None, "confirmed_by": confirmed_by,
                       "note": "sent and confirmed" if confirmed_by else "Send was clicked but no proof of delivery was found; check the inbox before sending again."})


def _web_read(args: dict) -> str:
    """Open a URL in the user's browser and read the page, in one call.

    The model's own sequence for "open X and tell me Y" was open_url -> wait_for
    -> read_page_text: three LLM rounds (~7.7 s measured 2026-10-25) to do what
    is one procedure. The bunny still opens the page and the text still comes
    from the page the user can see; only the round-trips go.
    """
    url = str(args.get("url", "")).strip()
    if not url.startswith(("http://", "https://")):
        return _fail("bad_args", f"'url' must be an absolute http(s) URL, got '{url}'.")
    host = urllib.parse.urlparse(url).netloc.lower()
    _open(url)
    # Wait on the URL the browser is actually showing, not on the window title:
    # Chrome's window title follows the frontmost tab only loosely (it reported a
    # neighbouring tab in the first version of this recipe).
    deadline = time.time() + 20
    loaded = False
    while time.time() < deadline:
        try:
            from tools import ax_context as ax

            seen = (ax.front_url("Google Chrome") or "").lower()
        except Exception:
            seen = ""
        if seen and host and host in seen:
            loaded = True
            break
        time.sleep(0.4)
    from tools.page_text import read_page_text

    text = read_page_text(app="Google Chrome")
    head = str(text).splitlines()[0] if text else ""
    m = re.match(r"Page: (.+?) - ", head)
    ok = bool(text) and "error" not in str(text)[:40].lower()
    return json.dumps({
        "ok": ok, "url": url, "loaded": loaded,
        "title": (m.group(1) if m else ""),
        "text": str(text)[:6000],
    }, ensure_ascii=False)


def _front_title() -> str:
    try:
        return v2._front_window()[1]  # (app, title)
    except Exception:
        try:
            from tools import screen_act

            return screen_act._front_window()[1]
        except Exception:
            return ""


RECIPES = {"gmail.send": _gmail_send, "web.read": _web_read}


def app_task(name: str = "", args: dict | None = None) -> str:
    fn = RECIPES.get(name)
    if fn is None:
        return _fail("unknown_task", f"known tasks: {', '.join(sorted(RECIPES))}.")
    return v2._trace("app_task", {"name": name, "args": {k: str(x)[:120] for k, x in (args or {}).items()}}, fn(args or {}))


APP_TASK_SCHEMA = {
    "type": "function",
    "function": {
        "name": "app_task",
        "description": (
            "Runs a known job end to end on the user's screen, the bunny doing every click, and reports the evidence. "
            "name 'gmail.send': args {to, subject, body}: opens Gmail's pre-filled compose page, clicks Send, "
            "and confirms delivery with a read-only Gmail check for a message to that address. Prefer it over hand-clicking Gmail. You write the subject and body; it does the rest. "
            "If it returns sent:false, check the inbox before trying again: it may still have gone out. "
            "name 'web.read': args {url}: opens the page in the user's browser, waits for it to load and returns its text. "
            "Use it instead of open_url + wait_for + read_page_text when the job is 'open X and tell me Y'."
        ),
        "parameters": {"type": "object", "properties": {
            "name": {"type": "string", "enum": sorted(RECIPES)}, "args": {"type": "object"}}, "required": ["name", "args"]},
    },
}
