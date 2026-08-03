"""
Video specialist subagent.

Mission: download, process, and extract information from videos.

Tools: download_video (yt-dlp), process_video (ffmpeg), transcribe_audio (whisper),
       search_youtube, get_youtube_transcript, find_files

Public API:
    run_video_specialist(query, model="gemma4", url=None, max_steps=6) -> VideoResult
"""

from __future__ import annotations

import json
import logging
import re
import time

from clients.ollama_client import DEFAULT_MODEL
import ollama
from pydantic import BaseModel, Field

from tools.registry import ALL_TOOLS, execute_tool, tools_with_tags

_log = logging.getLogger("video_specialist")

VIDEO_TOOL_NAMES = tools_with_tags("spec_video")


def _video_tool_schemas() -> list[dict]:
    return [t for t in ALL_TOOLS if t["function"]["name"] in VIDEO_TOOL_NAMES]


class VideoResult(BaseModel):
    file_path: str = ""
    transcript: str = ""
    info: str = ""
    tool_trace: list[str] = Field(default_factory=list)
    elapsed_s: float = 0.0
    error: str | None = None


_SYSTEM_PROMPT = """You are VIDEO PROCESSOR. Your job: download and process videos.

TOOLS:
  download_video(url, output_dir, format, audio_only) — download with yt-dlp
  process_video(input_path, operation, ...)           — trim, convert, resize, extract audio, info
  transcribe_audio(file_path, language)               — transcribe audio to text
  search_youtube(query, max_results)                  — search YouTube
  get_youtube_transcript(video_url)                   — get transcript from YouTube video
  find_files(pattern, path)                           — find local video files

RULES:
- For URLs: use download_video to save locally, then process/transcribe as needed.
- For local files: use process_video for info/trim/convert, transcribe_audio for speech.
- For YouTube searches: use search_youtube.
- For extracting audio from video: process_video with extract_audio, then transcribe_audio.
- Stop after {max_steps} calls."""


def _build_system_prompt(max_steps: int) -> str:
    return _SYSTEM_PROMPT.format(max_steps=max_steps)


def run_video_specialist(
    query: str,
    model: str = DEFAULT_MODEL,
    url: str | None = None,
    max_steps: int = 6,
    timeout_s: float = 600.0,
) -> VideoResult:
    t0 = time.time()

    # ── Fast path: URL in query → download ──
    if not url:
        url_match = re.search(r"(https?://[^\s\"']+)", query)
        if url_match:
            url = url_match.group(0)

    tools = _video_tool_schemas()
    tool_names = {t["function"]["name"] for t in tools}
    system = _build_system_prompt(max_steps)
    from tools.memory_tools import mem_block_for
    _mem = mem_block_for(query)
    if _mem:
        system = _mem + "\n" + system

    if url:
        query = f"Download and process this video: {url}\n{query}"

    messages = [
        {"role": "system", "content": system},
        {"role": "user", "content": query},
    ]

    tool_trace: list[str] = []
    file_path: str = ""
    transcript: str = ""
    info: str = ""

    for step in range(max_steps):
        if time.time() - t0 > timeout_s:
            return VideoResult(
                tool_trace=tool_trace,
                elapsed_s=time.time() - t0,
                error="timeout",
            )

        try:
            resp = ollama.chat(
                model=model,
                messages=messages,
                tools=tools,
                options={"temperature": 0.1, "num_ctx": 8192},
            )
        except Exception as e:
            return VideoResult(
                tool_trace=tool_trace,
                elapsed_s=time.time() - t0,
                error=f"ollama error: {e}",
            )

        msg = resp.get("message", {})
        content = msg.get("content", "") or ""
        tool_calls = msg.get("tool_calls") or []

        if not tool_calls:
            break

        for tc in tool_calls:
            fn = tc.get("function", tc) if isinstance(tc, dict) else getattr(tc, "function", None)
            if not fn:
                continue
            name = fn.get("name", "") or getattr(fn, "name", "")
            args_raw = fn.get("arguments", {}) or getattr(fn, "arguments", {})
            if isinstance(args_raw, str):
                try:
                    args = json.loads(args_raw)
                except json.JSONDecodeError:
                    args = {}
            else:
                args = dict(args_raw) if args_raw else {}

            if name not in tool_names:
                continue

            tool_trace.append(name)

            try:
                r = execute_tool(name, args)
                r_str = str(r) if r else ""
            except Exception as e:
                r_str = f"Error: {type(e).__name__}: {e}"

            if name == "download_video" and "Downloaded:" in r_str:
                file_path = r_str.split("Downloaded: ", 1)[1].strip()
            elif name == "transcribe_audio":
                transcript = r_str
            elif name == "process_video" and "info" in str(args.get("operation", "")):
                info = r_str

            messages.append({"role": "assistant", "content": "", "tool_calls": [tc]})
            messages.append({"role": "tool", "content": r_str})

    return VideoResult(
        file_path=file_path,
        transcript=transcript,
        info=info,
        tool_trace=tool_trace,
        elapsed_s=time.time() - t0,
    )
