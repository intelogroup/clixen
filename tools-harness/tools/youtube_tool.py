"""
YouTube search tool — video search + transcript extraction.

Usage:
    from tools.youtube_tool import search_youtube, get_youtube_transcript

Dependencies:
    pip install yt-dlp youtube-search-python

Features:
    - search_youtube(query, max_results=5) → video titles, channels, durations, URLs
    - get_youtube_transcript(video_url) → full transcript text for summarization
"""

from __future__ import annotations

import json
import logging
import subprocess
import tempfile
from pathlib import Path

log = logging.getLogger("youtube_tool")

_YOUTUBE_SEARCH_AVAILABLE = True  # uses yt-dlp, always works if yt-dlp is installed

_YT_DLP_AVAILABLE = False
try:
    _yt_dlp_check = subprocess.run(
        ["yt-dlp", "--version"], capture_output=True, text=True
    )
    if _yt_dlp_check.returncode == 0:
        _YT_DLP_AVAILABLE = True
except FileNotFoundError:
    pass

SEARCH_SCHEMA = {
    "type": "function",
    "function": {
        "name": "search_youtube",
        "description": (
            "Search YouTube for videos by keyword, topic, or channel name. "
            "Returns video title, channel, duration, views, and URL. "
            "Use this to find videos on a topic, then optionally get their transcripts."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "query": {
                    "type": "string",
                    "description": "Search query — topic, keyword, or channel name",
                },
                "max_results": {
                    "type": "integer",
                    "description": "Max number of results (1-10, default 5)",
                    "default": 5,
                },
            },
            "required": ["query"],
        },
    },
}

TRANSCRIPT_SCHEMA = {
    "type": "function",
    "function": {
        "name": "get_youtube_transcript",
        "description": (
            "Get the full transcript/subtitles from a YouTube video. "
            "Returns the transcript text, optionally with timestamps. "
            "Use this to summarize or search within video content."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "video_url": {
                    "type": "string",
                    "description": "Full YouTube video URL (e.g. https://www.youtube.com/watch?v=...)",
                },
                "include_timestamps": {
                    "type": "boolean",
                    "description": "Include [MM:SS] timestamps before each line (default false for cleaner text)",
                    "default": False,
                },
            },
            "required": ["video_url"],
        },
    },
}

SCHEMAS = [SEARCH_SCHEMA, TRANSCRIPT_SCHEMA]


def search_youtube(query: str, max_results: int = 5) -> str:
    """Search YouTube via yt-dlp and return formatted results."""
    import subprocess, json

    limit = min(max_results, 10)
    cmd = ["yt-dlp", "--flat-playlist", "--dump-json", f"ytsearch{limit}:{query}"]

    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=20)
        if r.returncode != 0:
            return f"[youtube] yt-dlp search failed: {r.stderr[:200]}"

        lines = [f"YouTube results for '{query}':"]
        for i, line in enumerate(r.stdout.strip().splitlines(), 1):
            data = json.loads(line)
            title = data.get("title", "Untitled")
            channel = data.get("channel", "Unknown")
            duration = data.get("duration", "?")
            views = data.get("view_count", "?")
            url = data.get("webpage_url", data.get("url", ""))
            lines.append(
                f"  {i}. {title}\n"
                f"     Channel: {channel} | Duration: {duration}s | Views: {views}\n"
                f"     URL: {url}"
            )
        return "\n".join(lines) if len(lines) > 1 else f"[youtube] no results for: {query}"

    except subprocess.TimeoutExpired:
        return "[youtube] search timed out (yt-dlp)"
    except Exception as e:
        log.error("youtube search failed: %s", e, exc_info=True)
        return f"[youtube] search failed: {e}"


def _whisper_fallback_transcript(video_url: str) -> str:
    """No captions/subs — download audio and transcribe locally with whisper."""
    from tools.audio_tools import transcribe_audio

    try:
        with tempfile.TemporaryDirectory() as tmpdir:
            out_tmpl = str(Path(tmpdir) / "audio.%(ext)s")
            dl = subprocess.run(
                ["yt-dlp", "-x", "--audio-format", "mp3", "--audio-quality", "5",
                 "-o", out_tmpl, video_url],
                capture_output=True, text=True, timeout=180,
            )
            mp3 = Path(tmpdir) / "audio.mp3"
            if dl.returncode != 0 or not mp3.exists():
                log.warning("yt-dlp audio download failed: %s", dl.stderr[:300])
                return "[youtube] no transcript (subtitles) available and audio fallback failed"

            result = transcribe_audio(str(mp3), max_chars=8000)
            text = result.get("text", "").strip()
            if not text:
                return "[youtube] whisper produced no transcript for this video"
            note = " (via whisper — no captions were available)"
            return text + note

    except subprocess.TimeoutExpired:
        return "[youtube] audio download timed out (whisper fallback)"
    except Exception as e:
        log.error("whisper fallback failed: %s", e, exc_info=True)
        return f"[youtube] transcript failed (whisper fallback): {e}"


def get_youtube_transcript(video_url: str, include_timestamps: bool = False) -> str:
    """Get transcript from a YouTube video using yt-dlp."""
    if not _YT_DLP_AVAILABLE:
        return "[youtube] yt-dlp not installed. Run: pip install yt-dlp"

    try:
        with tempfile.TemporaryDirectory() as tmpdir:
            out_path = Path(tmpdir) / "transcript"

            cmd = [
                "yt-dlp",
                "--skip-download",
                "--write-subs",
                "--write-auto-subs",
                "--sub-lang", "en",
                "--sub-format", "vtt",
                "--output", str(out_path),
                video_url,
            ]

            result = subprocess.run(cmd, capture_output=True, text=True, timeout=30)

            if result.returncode != 0:
                log.warning("yt-dlp stderr: %s", result.stderr[:300])
                return _whisper_fallback_transcript(video_url)

            vtt_files = list(Path(tmpdir).glob("*.en.vtt"))
            if not vtt_files:
                vtt_files = list(Path(tmpdir).glob("*.vtt"))
            if not vtt_files:
                return _whisper_fallback_transcript(video_url)

            import re
            vtt_content = vtt_files[0].read_text(errors="replace")

            # strip VTT/XML inline tags + timing cues like <00:00:01.120>
            tag_re = re.compile(r"<[^>]+>")
            lines = []
            last = None
            in_text = False
            for raw in vtt_content.splitlines():
                line = raw.strip()
                if not line or line == "WEBVTT" or line.isdigit():
                    continue
                if "-->" in line:
                    in_text = True
                    if include_timestamps:
                        ts = line.split("-->")[0].strip()
                        lines.append(f"[{ts}]")
                    continue
                if line.startswith(("Kind:", "Language:", "NOTE", "STYLE")):
                    continue
                if not in_text:
                    continue
                text = tag_re.sub("", line).strip()
                if not text or text == last:  # drop cue duplicates (tagged line repeats plain)
                    continue
                last = text
                lines.append(text)

            transcript = " ".join(lines) if not include_timestamps else "\n".join(lines)

            if not transcript.strip():
                return "[youtube] transcript is empty"

            # Truncate very long transcripts
            max_len = 8000
            if len(transcript) > max_len:
                transcript = transcript[:max_len] + f"\n\n... (transcript truncated at {max_len} chars)"

            return transcript

    except subprocess.TimeoutExpired:
        return "[youtube] transcript download timed out"
    except Exception as e:
        log.error("youtube transcript failed: %s", e, exc_info=True)
        return f"[youtube] transcript failed: {e}"
