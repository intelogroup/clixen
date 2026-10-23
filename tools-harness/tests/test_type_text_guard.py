from unittest.mock import patch

from tools import screen_act


def _run(focus, osa_calls):
    class R:
        returncode, stderr = 0, ""

    with (
        patch.object(screen_act, "_guard", return_value=None),
        patch.object(screen_act, "_focus", side_effect=focus),
        patch.object(screen_act, "_focus_label", return_value=""),
        patch.object(screen_act.orb_cursor, "typing"),
        patch.object(screen_act.subprocess, "run", side_effect=lambda *a, **k: osa_calls.append(a) or R()),
    ):
        return screen_act.type_text("Alan Turing", True)


def test_refuses_when_a_button_has_focus():
    calls = []
    out = _run([("AXButton", "")], calls)
    assert "Nothing typed" in out and out.startswith("[screen error]") and "button" in out
    assert calls == []  # no keystrokes sent into the void


def test_reports_what_the_field_reads_and_submits_after_verifying():
    calls = []
    out = _run([("AXTextField", ""), ("AXTextField", "Alan Turing")], calls)
    assert "Field reads: 'Alan Turing'" in out and "pressed Return" in out
    assert len(calls) == 2  # the typing, then the separate Return


def test_flags_text_that_did_not_land():
    calls = []
    out = _run([("AXTextField", ""), ("AXTextField", "something else")], calls)
    assert "check it landed" in out
