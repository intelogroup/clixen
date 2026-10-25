"""The four tools added for "things a desktop agent is asked by name".

The interesting one is read_file: granting it without a binary guard produced 139
lines of replacement characters for a PNG (measured), which is worse than a refusal —
it costs tokens and teaches the model nothing. So the guard is tested here, not just
the grant.
"""
import sys
from pathlib import Path

import pytest

HARNESS = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(HARNESS))

from tools.filesystem import _is_probably_binary, read_file  # noqa: E402


@pytest.fixture
def files(tmp_path):
    text = tmp_path / "notes.txt"
    text.write_text("line one\nline two\n")
    png = tmp_path / "chart.png"
    png.write_bytes(b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR" + bytes(range(64)))
    return text, png


def test_text_is_read_normally(files):
    text, _ = files
    out = read_file(str(text))
    assert "line one" in out and "2 lines total" in out


def test_a_binary_is_refused_with_the_right_alternative(files):
    _, png = files
    out = read_file(str(png))
    assert out.startswith("Not text:"), out
    assert "analyze_image" in out, "a refusal must point at the tool that can help"
    assert "\ufffd" not in out, "no replacement-character garbage may be returned"


def test_nul_bytes_mean_binary():
    assert _is_probably_binary(b"abc\x00def")


def test_invalid_utf8_means_binary():
    assert _is_probably_binary(b"\xff\xfe\x00\x01")


def test_plain_ascii_is_not_binary():
    assert not _is_probably_binary(b"hello, world\n")


def test_utf8_text_with_accents_is_not_binary():
    assert not _is_probably_binary("cafe\u0301 \u00e9\u00e8".encode())


def test_the_four_new_tools_are_granted_with_handlers():
    import orb_agent
    from tools.registry import ALL_TOOLS, EXECUTORS

    names = {t["function"]["name"] for t in ALL_TOOLS}
    for tool in ("system_status", "clipboard_read", "clipboard_write", "read_file"):
        assert tool in orb_agent.TOOL_NAMES, f"{tool} missing from the orb set"
        assert tool in names, f"{tool} has no schema"
        assert tool in EXECUTORS, f"{tool} has no handler"
        assert tool in orb_agent.PROMPT, f"{tool} is granted but never named in the prompt"


def test_memory_pressure_does_not_impersonate_a_level(monkeypatch):
    """Free percentage is context, not a verdict.

    With only "51% free" the orb answered "not under memory pressure" — an inference
    the number cannot support, since macOS reports the real level separately and it was
    unavailable here. The wording must say unknown.
    """
    from tools import system_status as ss

    monkeypatch.setattr(ss, "_run", lambda cmd: "0" if cmd[0] == "sysctl" else
                        "System-wide memory free percentage: 52%")
    monkeypatch.setattr(ss, "_PRESSURE_CACHE", ("", 0.0))
    text = ss._memory_pressure()
    assert text.startswith("unknown"), text
    assert "52%" in text


def test_a_real_sysctl_level_is_used_verbatim(monkeypatch):
    from tools import system_status as ss

    monkeypatch.setattr(ss, "_run", lambda cmd: "2")
    monkeypatch.setattr(ss, "_PRESSURE_CACHE", ("", 0.0))
    assert ss._memory_pressure() == "warning"
