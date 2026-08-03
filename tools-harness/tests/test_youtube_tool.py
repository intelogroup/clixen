import os
import sys
import types

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import pytest

from tools import youtube_tool as yt


# --- unit tests (no network) ------------------------------------------------

def _fake_search_run(result_lines, returncode=0):
    def _run(cmd, *a, **k):
        # mimic yt-dlp: only return the requested ytsearchN count
        import re
        m = re.search(r"ytsearch(\d+):", " ".join(cmd))
        n = int(m.group(1)) if m else len(result_lines)
        lines = result_lines[:n]
        return types.SimpleNamespace(
            returncode=returncode,
            stdout="\n".join(lines),
            stderr="",
        )
    return _run


def test_search_youtube_formats_results(monkeypatch):
    payload = [
        '{"title": "Asyncio Full Tutorial", "channel": "Tech With Tim", '
        '"duration": 1499.0, "view_count": 349205, '
        '"webpage_url": "https://www.youtube.com/watch?v=Qb9s3UiMSTA"}',
        '{"title": "AsyncIO Guide", "channel": "Corey Schafer", '
        '"duration": 6161.0, "view_count": 152001, '
        '"webpage_url": "https://www.youtube.com/watch?v=oAkLSJNr5zY"}',
    ]
    monkeypatch.setattr(yt.subprocess, "run", _fake_search_run(payload))

    out = yt.search_youtube("python asyncio tutorial", max_results=2)

    assert "Asyncio Full Tutorial" in out
    assert "Tech With Tim" in out
    assert "https://www.youtube.com/watch?v=Qb9s3UiMSTA" in out
    assert "AsyncIO Guide" in out
    assert out.count("URL:") == 2


def test_search_youtube_limits_to_ten(monkeypatch):
    payload = [f'{{"title": "v{i}", "webpage_url": "u{i}"}}' for i in range(15)]
    monkeypatch.setattr(yt.subprocess, "run", _fake_search_run(payload))

    out = yt.search_youtube("anything", max_results=50)
    assert out.count("URL:") == 10


def test_search_youtube_handles_failure(monkeypatch):
    def _run(cmd, *a, **k):
        class R:
            returncode = 1
            stdout = ""
            stderr = "error: bad query"
        return R()
    monkeypatch.setattr(yt.subprocess, "run", _run)

    out = yt.search_youtube("x")
    assert out.startswith("[youtube] yt-dlp search failed")


def _write_fake_vtt(cmd, tmpdir_root, content):
    """Find the --output path in the yt-dlp cmd and drop a fake .en.vtt there."""
    out_idx = cmd.index("--output")
    out_path = cmd[out_idx + 1]
    from pathlib import Path
    p = Path(out_path + ".en.vtt")
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(content, encoding="utf-8")


def test_get_transcript_strips_tags_and_dedupes(monkeypatch):
    vtt = (
        "WEBVTT\n\n"
        "00:00:00.080 --> 00:00:02.000\n"
        "imagine<00:00:00.640><c> programming</c><00:00:01.120><c> is</c> a journey\n\n"
        "00:00:02.030 --> 00:00:04.000\n"
        "imagine programming is a journey\n\n"
        "00:00:04.789 --> 00:00:06.000\n"
        "point A to D in traditional\n"
    )

    def _run(cmd, *a, **k):
        _write_fake_vtt(cmd, None, vtt)
        class R:
            returncode = 0
            stdout = ""
            stderr = ""
        return R()
    monkeypatch.setattr(yt.subprocess, "run", _run)

    out = yt.get_youtube_transcript("https://www.youtube.com/watch?v=abc")
    assert "<c>" not in out
    assert "imagine programming is a journey" in out
    # duplicate cue line collapsed — only one occurrence
    assert out.count("imagine programming is a journey") == 1
    assert "point A to D in traditional" in out


def test_get_transcript_includes_timestamps(monkeypatch):
    vtt = (
        "WEBVTT\n\n"
        "00:00:00.080 --> 00:00:02.000\n"
        "hello world\n\n"
        "00:00:02.030 --> 00:00:04.000\n"
        "second line\n"
    )

    def _run(cmd, *a, **k):
        _write_fake_vtt(cmd, None, vtt)
        class R:
            returncode = 0
            stdout = ""
            stderr = ""
        return R()
    monkeypatch.setattr(yt.subprocess, "run", _run)

    out = yt.get_youtube_transcript(
        "https://www.youtube.com/watch?v=abc", include_timestamps=True
    )
    assert "[00:00:00.080]" in out
    assert "[00:00:02.030]" in out
    assert "hello world" in out


def test_get_transcript_no_subs(monkeypatch):
    def _run(cmd, *a, **k):
        # return success but write nothing
        class R:
            returncode = 0
            stdout = ""
            stderr = ""
        return R()
    monkeypatch.setattr(yt.subprocess, "run", _run)

    out = yt.get_youtube_transcript("https://www.youtube.com/watch?v=abc")
    assert "no transcript" in out


def test_get_transcript_truncates_long(monkeypatch):
    vtt = "WEBVTT\n\n"
    # craft many cues so text exceeds 8000 chars
    big = "word " * 2000
    vtt += "00:00:00.000 --> 00:00:01.000\n" + big + "\n"

    def _run(cmd, *a, **k):
        _write_fake_vtt(cmd, None, vtt)
        class R:
            returncode = 0
            stdout = ""
            stderr = ""
        return R()
    monkeypatch.setattr(yt.subprocess, "run", _run)

    out = yt.get_youtube_transcript("https://www.youtube.com/watch?v=abc")
    assert "transcript truncated" in out


# --- integration (needs network) -------------------------------------------

@pytest.mark.live
def test_search_youtube_live():
    out = yt.search_youtube("python asyncio tutorial", max_results=3)
    assert "URL:" in out
    assert "youtube.com" in out


@pytest.mark.live
def test_get_transcript_live():
    out = yt.get_youtube_transcript(
        "https://www.youtube.com/watch?v=Qb9s3UiMSTA"
    )
    assert len(out) > 100
    assert "<c>" not in out
