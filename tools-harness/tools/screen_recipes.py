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


RECIPES = {"gmail.send": _gmail_send}


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
            "If it returns sent:false, check the inbox before trying again: it may still have gone out."
        ),
        "parameters": {"type": "object", "properties": {
            "name": {"type": "string", "enum": sorted(RECIPES)}, "args": {"type": "object"}}, "required": ["name", "args"]},
    },
}
