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


def test_click_text_prefers_native_position(monkeypatch):
    monkeypatch.setattr(screen_act, "_guard", lambda typing=False: None)
    monkeypatch.setattr(screen_act, "find_element", lambda t: (10.0, 20.0))
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
