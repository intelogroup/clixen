"""Skills are procedures, and the loader is a boundary: no traversal, no oversize read."""
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from tools import skills as sk  # noqa: E402


def test_the_installed_procedures_are_listed():
    names = sk.list_skills()
    assert {"form-filling", "multi-tab-research", "screenshot-to-action", "send-mail"} <= set(names)


def test_no_argument_lists_instead_of_failing():
    out = sk.load_skill()
    assert "form-filling" in out and "load_skill(name)" in out


def test_a_skill_loads_with_its_heading():
    out = sk.load_skill("form-filling")
    assert out.startswith("## Procedure: form-filling")
    assert "act(steps=" in out  # the actual guidance, not an empty shell


@pytest.mark.parametrize(
    "bad",
    ["../etc/passwd", "/etc/passwd", "form filling", "a" * 60, "..", "x/y"],
)
def test_names_cannot_escape_or_be_weird(bad):
    out = sk.load_skill(bad)
    assert out.startswith("[error]")


def test_names_are_case_insensitive():
    """A model may type the name in capitals; that is friendliness, not a hole:
    the resolved name is lowercased and validated before it touches the path."""
    assert sk.load_skill("FORM-FILLING").startswith("## Procedure: form-filling")


def test_an_unknown_name_lists_what_exists():
    out = sk.load_skill("does-not-exist")
    assert "[error]" in out and "form-filling" in out


def test_an_oversized_skill_is_refused(monkeypatch, tmp_path):
    monkeypatch.setattr(sk, "MAX_SKILL_BYTES", 50)
    out = sk.load_skill("send-mail")
    assert "[error]" in out and "larger than" in out


def test_the_skills_dir_is_the_only_place_read(monkeypatch, tmp_path):
    # A skill file symlinked outside the directory must not be readable.
    outside = tmp_path / "secret.md"
    outside.write_text("SECRET")
    staged = tmp_path / "skills"
    staged.mkdir()
    (staged / "leak.md").symlink_to(outside)
    monkeypatch.setattr(sk, "SKILLS_DIR", staged)
    out = sk.load_skill("leak")
    assert "SECRET" not in out and "[error]" in out


def test_schema_matches_the_behaviour():
    fn = sk.LOAD_SKILL_SCHEMA["function"]
    assert fn["name"] == "load_skill"
    assert "required" not in fn["parameters"]  # calling with no name must be legal
