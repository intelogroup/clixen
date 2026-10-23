from tools import screen_act
from tools.screen_act import find_text

LINES = [
    ("Breaking Enigma news today", 100, 200, 400, 20),
    ("Breaking Enigma", 500, 300, 150, 20),
    ("Donate", 10, 10, 60, 20),
]


def test_find_text_prefers_exact_line():
    assert find_text(LINES, "breaking enigma") == (575, 310)


def test_find_text_substring_centre_and_miss():
    x, y = find_text(LINES, "news")
    assert 100 < x < 500 and y == 210
    assert find_text(LINES, "nothing here") is None


def test_type_text_refuses_terminal(monkeypatch):
    monkeypatch.setattr(screen_act, "_screen_locked", lambda: False)
    monkeypatch.setattr(screen_act, "_front_window", lambda: ("Ghostty", "zsh"))
    assert "terminal" in screen_act.type_text("rm -rf x", submit=True)


def test_type_text_verifies_despite_newlines_the_field_value_drops(monkeypatch):
    monkeypatch.setattr(screen_act, "_guard", lambda typing=False: None)
    monkeypatch.setattr(screen_act, "_focus", lambda: ("AXTextArea", "ports. No links.Best,Kalinov"))  # Gmail's body value has no newlines
    monkeypatch.setattr(screen_act.orb_cursor, "typing", lambda *a, **k: None)
    monkeypatch.setattr(screen_act.subprocess, "run", lambda *a, **k: type("R", (), {"returncode": 0, "stderr": ""})())
    assert "check it landed" not in screen_act.type_text("ports. No links.\n\nBest,\nKalinov")


def test_screen_failures_are_tagged_so_the_tool_loop_counts_them_as_errors(monkeypatch):
    from tools.registry import is_error_result

    monkeypatch.setattr(screen_act, "_guard", lambda typing=False: None)
    monkeypatch.setattr(screen_act, "find_element", lambda t: None)
    monkeypatch.setattr(screen_act, "dismiss_popovers", lambda app="": [])
    monkeypatch.setattr(screen_act, "_ocr_boxes", lambda accurate=False: [])
    monkeypatch.setattr(screen_act, "_through_folded_menu", lambda app, t: (None, None))
    assert is_error_result(screen_act.click_text("Nope"))  # was a bare "Could not find ..." the loop treated as success

    def many(t):
        raise screen_act.AmbiguousTarget(["a", "b"])
    monkeypatch.setattr(screen_act, "find_element", many)
    assert is_error_result(screen_act.click_text("To"))



def test_a_locked_screen_is_a_tagged_error(monkeypatch):
    from tools.registry import is_error_result

    monkeypatch.setattr(screen_act, "_screen_locked", lambda: True)
    assert is_error_result(screen_act.type_text("x"))


def test_click_reports_what_has_focus_afterwards(monkeypatch):
    monkeypatch.setattr(screen_act, "_guard", lambda typing=False: None)
    monkeypatch.setattr(screen_act, "find_element", lambda t: (10.0, 20.0))
    monkeypatch.setattr(screen_act, "labels_at", lambda x, y: ["To recipients"])
    monkeypatch.setattr(screen_act, "_click", lambda x, y: None)
    monkeypatch.setattr(screen_act, "window_title", lambda app="": "Gmail")
    monkeypatch.setattr(screen_act, "settle_title", lambda before, app="": before)
    monkeypatch.setattr(screen_act, "_focus", lambda: ("AXTextField", "jim"))
    assert "Focus now: AXTextField" in screen_act.click_text("To recipients")


def test_type_text_refuses_gmails_ai_write_box(monkeypatch):
    from tools.registry import is_error_result

    monkeypatch.setattr(screen_act, "_guard", lambda typing=False: None)
    monkeypatch.setattr(screen_act, "_focus", lambda: ("AXTextArea", ""))
    monkeypatch.setattr(screen_act, "_focus_label", lambda: "Describe your message")
    out = screen_act.type_text("Hi Jim, here are the headlines")
    assert is_error_result(out) and "AI" in out and "Message Body" in out


def test_close_extension_panels_clicks_the_x_then_clears_popovers(monkeypatch):
    clicked = []
    monkeypatch.setattr(screen_act, "close_side_panel", lambda app="": clicked.append("x") or True)
    monkeypatch.setattr(screen_act, "dismiss_popovers", lambda app="": ["Monica sign in"])
    out = screen_act.close_extension_panels("Google Chrome")
    assert clicked == ["x"] and "side panel" in out and "Monica sign in" in out


def test_close_extension_panels_says_so_when_nothing_is_open(monkeypatch):
    monkeypatch.setattr(screen_act, "close_side_panel", lambda app="": False)
    monkeypatch.setattr(screen_act, "dismiss_popovers", lambda app="": [])
    assert "Nothing to close" in screen_act.close_extension_panels()


def test_close_extension_panels_is_wired_for_the_agent():
    from tools.registry import EXECUTORS, ALL_TOOLS
    import orb_agent

    assert "close_extension_panels" in EXECUTORS
    assert any(t["function"]["name"] == "close_extension_panels" for t in ALL_TOOLS)
    assert "close_extension_panels" not in orb_agent.TOOL_NAMES  # act() closes extension panels itself


def test_click_text_prefers_native_position(monkeypatch):
    monkeypatch.setattr(screen_act, "_guard", lambda typing=False: None)
    monkeypatch.setattr(screen_act, "find_element", lambda t: (10.0, 20.0))
    monkeypatch.setattr(screen_act, "labels_at", lambda x, y: ["Donate"])
    monkeypatch.setattr(screen_act, "_ocr_boxes", lambda accurate=False: (_ for _ in ()).throw(AssertionError("OCR used")))
    clicked = []
    monkeypatch.setattr(screen_act, "_click", lambda x, y: clicked.append((x, y)))
    assert "Clicked" in screen_act.click_text("Donate")
    assert clicked == [(10.0, 20.0)]


def test_cut_keeps_only_the_visible_part():
    from tools.ax_context import _cut

    assert _cut((0, 0, 100, 100), (50, 50, 100, 100)) == (50, 50, 50, 50)
    assert _cut((0, 892, 100, 0), (0, 0, 1000, 892)) is None  # height 0 below the fold: needs scrolling
    assert _cut((5, 5, 10, 10), None) == (5, 5, 10, 10)


def test_settle_title_waits_for_change_then_quiet(monkeypatch):
    import tools.ax_context as ax

    seq = iter(["A", "A", "B", "B", "C", "C", "C", "C", "C", "C"])
    last = ["A"]

    def fake(app=""):
        last[0] = next(seq, last[0])
        return last[0]

    monkeypatch.setattr(ax, "window_title", fake)
    assert ax.settle_title("A", limit=3.0, quiet=0.25) == "C"


def test_settle_title_unchanged_returns_before(monkeypatch):
    import tools.ax_context as ax

    monkeypatch.setattr(ax, "window_title", lambda app="": "A")
    assert ax.settle_title("A", limit=0.3) == "A"


def test_taxonomy_keeps_clock_questions_off_web_search():
    from clients.router import _INTENT_TAXONOMY

    temporal = next(l for l in _INTENT_TAXONOMY.splitlines() if l.startswith("temporal"))
    status = next(l for l in _INTENT_TAXONOMY.splitlines() if l.startswith("system_status"))
    assert "never the current time or date" in temporal
    assert "what time is it" in status and "time in Tokyo" in status


def test_type_script_types_each_character_and_escapes():
    from tools.screen_act import _type_script

    s = _type_script('say "hi"\\', 0.04, submit=True)
    assert 'repeat with c in characters of "say \\"hi\\"\\\\"' in s
    assert "keystroke c" in s and "delay 0.040" in s and s.rstrip().endswith("end tell")
    assert "key code 36" in s and "key code 36" not in _type_script("x", 0.04, submit=False)


def test_orb_cursor_typing_writes_event(tmp_path, monkeypatch):
    import json
    from tools import orb_cursor

    monkeypatch.setattr(orb_cursor, "_FILE", tmp_path / "orb_cursor.json")
    orb_cursor.typing("hello", 0.03)
    v = json.loads((tmp_path / "orb_cursor.json").read_text())
    assert v["type"] == {"text": "hello", "per": 0.03} and "seq" in v and "ts" in v


def test_scroll_deltas_wheel_signs():
    from tools.screen_act import scroll_deltas

    assert scroll_deltas("down", 1, 800) == (0, -800, "up")  # wheel: negative = reveal what is below
    assert scroll_deltas("up", 0.5, 800) == (0, 400, "down")
    assert scroll_deltas("right", 1, 800)[0] < 0
    assert scroll_deltas("bottom", 1, 100)[1] == -8000  # pages ignored for the ends
    assert scroll_deltas("top", 1, 100)[2] == "down"


def test_orb_cursor_scrolling_writes_event(tmp_path, monkeypatch):
    import json

    from tools import orb_cursor

    monkeypatch.setattr(orb_cursor, "_FILE", tmp_path / "c.json")
    orb_cursor.scrolling("up", 0.7)
    v = json.loads((tmp_path / "c.json").read_text())
    assert v["scroll"] == {"dir": "up", "dur": 0.7} and v["seq"]


def test_same_view_ignores_a_blinking_caret_but_not_a_scroll():
    from tools.screen_act import _same_view

    base = [200] * 2560
    caret = base[:]
    caret[100] = 0  # one pixel of 2560
    scrolled = [200 if i % 64 < 32 else 40 for i in range(2560)]
    assert _same_view(base, caret)
    assert not _same_view(base, scrolled)
