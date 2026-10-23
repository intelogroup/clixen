import json

import pytest

from tools import screen_recipes as rc


def _j(o):
    return json.loads(o)


@pytest.fixture
def mail(monkeypatch):
    class M:
        opened = []
        acts = []
        labels = ["Send ‪(⌘Enter)‬", "More send options", "Kalinov jim (jimkalinov@gmail.com)", "Subject"]
        api = "ID: 1\n"

    m = M()
    monkeypatch.setattr(rc, "_open", lambda url: m.opened.append(url))
    monkeypatch.setattr(rc.v2, "observe", lambda app="", query="": json.dumps({"rev": 3, "elements": [
        {"id": f"e{i + 1}.3", "role": "Button", "label": l} for i, l in enumerate(m.labels)]}))
    monkeypatch.setattr(rc.v2, "act", lambda **k: m.acts.append(k) or json.dumps({"ok": True, "expect": "met"}))
    monkeypatch.setattr(rc, "_sent_since", lambda to, subject, since: m.api)
    monkeypatch.setattr(rc.time, "sleep", lambda s: None)
    return m


def test_gmail_send_opens_the_prefilled_link_clicks_send_and_confirms_by_api(mail):
    r = _j(rc.app_task("gmail.send", {"to": "jimkalinov@gmail.com", "subject": "AI headlines", "body": "Line 1\nLine 2"}))
    assert r["ok"] and r["sent"] and r["confirmed_by"] == "gmail_api"
    url = mail.opened[0]
    assert url.startswith("https://mail.google.com/mail/?view=cm&fs=1&to=jimkalinov%40gmail.com&su=AI%20headlines&body=Line%201%0ALine%202")
    assert mail.acts[0]["target"] == "e1.3" and mail.acts[0]["action"] == "click"  # the Send button, not 'More send options'


def test_gmail_send_refuses_a_bad_address_and_a_missing_body(mail):
    assert _j(rc.app_task("gmail.send", {"to": "not-an-address", "subject": "s", "body": "b"}))["error"]["code"] == "bad_args"
    assert _j(rc.app_task("gmail.send", {"to": "a@b.co", "subject": "s", "body": ""}))["error"]["code"] == "bad_args"
    assert mail.opened == []


def test_gmail_send_never_sends_without_a_send_button(mail):
    mail.labels = ["Subject", "Message Body"]  # the compose page did not render
    r = _j(rc.app_task("gmail.send", {"to": "jimkalinov@gmail.com", "subject": "s", "body": "b"}))
    assert not r["ok"] and r["error"]["code"] == "compose_not_ready" and mail.acts == []


def test_gmail_send_reports_unconfirmed_when_nothing_proves_it(mail, monkeypatch):
    mail.api = "No emails found."
    monkeypatch.setattr(rc.v2, "act", lambda **k: json.dumps({"ok": False, "error": {"code": "expect_failed"}}))
    r = _j(rc.app_task("gmail.send", {"to": "jimkalinov@gmail.com", "subject": "s", "body": "b"}))
    assert r["ok"] and r["sent"] is False and r["confirmed_by"] is None


def test_unknown_recipes_list_the_known_ones(mail):
    r = _j(rc.app_task("nope.send", {}))
    assert r["error"]["code"] == "unknown_task" and "gmail.send" in r["error"]["hint"]


def test_recipes_are_wired_for_the_agent():
    import harness
    import orb_agent
    from tools.registry import ALL_TOOLS, EXECUTORS

    for n in ("app_task", "wait_for", "tabs"):
        assert n in EXECUTORS and n in orb_agent.TOOL_NAMES
        assert any(t["function"]["name"] == n for t in ALL_TOOLS)
    line = next(l for l in open(harness.__file__).read().splitlines() if '"get_screen_context", "set_screen_watch"' in l)
    assert all(f'"{n}"' in line for n in ("app_task", "wait_for", "tabs"))
