"""The bazooka click: payload carries the style, the real click waits for the impact, and the Python
timing matches the animation constants in clixen-orb."""
import json
import re
from pathlib import Path

from tools import orb_cursor

ORB = Path(__file__).resolve().parents[2] / "clixen-orb" / "src"


def _const(file: str, name: str) -> float:
    return float(re.search(rf"\b{name}\s*=\s*([0-9.]+)", (ORB / file).read_text()).group(1))


def test_bazooka_impact_matches_the_animation_timeline():
    wind, fly = _const("cursor.js", "T_WIND"), _const("cursor.js", "T_FLY")
    draw, aim, bullet = (_const("shoot.js", n) for n in ("B_DRAW", "B_AIM", "B_BULLET"))
    assert abs(orb_cursor.IMPACT_S["bazooka"] - (wind + fly + draw + aim + bullet)) < 0.01


def test_point_carries_style_only_when_given(tmp_path, monkeypatch):
    monkeypatch.setattr(orb_cursor, "_FILE", tmp_path / "orb_cursor.json")
    orb_cursor.point(10, 20)
    assert "style" not in json.loads((tmp_path / "orb_cursor.json").read_text())
    orb_cursor.point(10, 20, True, style="bazooka")
    assert json.loads((tmp_path / "orb_cursor.json").read_text())["style"] == "bazooka"


def test_click_style_env_switch(monkeypatch):
    monkeypatch.delenv("CLIXEN_ORB_CLICK_STYLE", raising=False)
    assert orb_cursor.click_style() == "bazooka"
    monkeypatch.setenv("CLIXEN_ORB_CLICK_STYLE", "press")
    assert orb_cursor.click_style() == "press"
    monkeypatch.setenv("CLIXEN_ORB_CLICK_STYLE", "nonsense")
    assert orb_cursor.click_style() == "bazooka"


def test_travel_env_pins_the_mode(tmp_path, monkeypatch):
    monkeypatch.setattr(orb_cursor, "_FILE", tmp_path / "orb_cursor.json")
    monkeypatch.delenv("CLIXEN_ORB_TRAVEL", raising=False)
    orb_cursor.point(1, 2)
    assert "travel" not in json.loads((tmp_path / "orb_cursor.json").read_text())
    monkeypatch.setenv("CLIXEN_ORB_TRAVEL", "rocket")
    orb_cursor.point(1, 2)
    assert json.loads((tmp_path / "orb_cursor.json").read_text())["travel"] == "rocket"
    monkeypatch.setenv("CLIXEN_ORB_TRAVEL", "teleport")
    assert orb_cursor.travel_mode() is None
