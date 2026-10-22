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
