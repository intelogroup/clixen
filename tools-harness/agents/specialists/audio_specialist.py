"""
Audio specialist subagent.

Mission: transcribe audio files — auto-discover audio paths if needed,
then transcribe with whisper.

Tools: transcribe_audio, find_files, list_directory

Public API:
    run_audio_specialist(query, model="gemma4", known_path=None, max_steps=5) -> AudioResult
"""

from __future__ import annotations

import json
import logging
import os
import re
import time
from pathlib import Path

from clients.ollama_client import DEFAULT_MODEL
import ollama
from pydantic import BaseModel, Field

from tools.registry import ALL_TOOLS, execute_tool, tools_with_tags

_log = logging.getLogger("audio_specialist")

AUDIO_TOOL_NAMES = tools_with_tags("spec_audio")

_AUDIO_EXTS = ("m4a", "mp3", "wav", "flac", "ogg", "aac", "wma", "opus")
_AUDIO_PATTERNS = tuple(f"*.{e}" for e in _AUDIO_EXTS)

_AUDIO_EXT_RE = re.compile(
    r"(?:~|/[^\s\"'])[^\s\"']*\.(?:" + "|".join(_AUDIO_EXTS) + r")\b",
    re.IGNORECASE,
)

# Matches "convert X.m4a to mp3" or "X.wav → flac" etc.
_CONVERT_RE = re.compile(
    r"convert\b.{0,60}?\.(m4a|mp3|wav|flac|ogg|aac|opus).{0,40}?\b(mp3|m4a|wav|flac|ogg|aac|opus)\b",
    re.IGNORECASE,
)


def _audio_tool_schemas() -> list[dict]:
    return [t for t in ALL_TOOLS if t["function"]["name"] in AUDIO_TOOL_NAMES]


class AudioResult(BaseModel):
    transcript: str = ""
    path: str = ""
    output_path: str = ""      # set on convert_audio operations
    tool_trace: list[str] = Field(default_factory=list)
    elapsed_s: float = 0.0
    error: str | None = None


_SYSTEM_PROMPT = """You are AUDIO TRANSCRIBER. Your job: find audio files and transcribe them.

TOOLS:
  transcribe_audio(file_path, language)  — transcribe audio with whisper
  find_files(pattern, path)              — find audio files by pattern
  list_directory(path)                   — list contents of a directory

RULES:
- If the user gives you a specific audio file path: transcribe it immediately.
- If the user gives you a folder: use list_directory or find_files to discover audio files first.
- Use find_files with patterns like *.m4a, *.mp3, *.wav to locate audio.
- After transcribing, return the full transcript text.
- Stop after transcribing the requested files.
- Stop after {max_steps} calls."""


def _build_system_prompt(max_steps: int) -> str:
    return _SYSTEM_PROMPT.format(max_steps=max_steps)


def _discover_audio_in(scope: str) -> list[str]:
    """Find audio files in a directory without LLM."""
    paths = []
    try:
        for pattern in _AUDIO_PATTERNS:
            result = execute_tool("find_files", {"pattern": pattern, "path": scope, "max_results": 10})
            for line in str(result).splitlines():
                line = line.strip()
                if line.startswith("/") and not line.endswith("/"):
                    paths.append(line)
    except Exception:
        pass
    return paths[:5]


def run_audio_specialist(
    query: str,
    model: str = DEFAULT_MODEL,
    known_path: str | None = None,
    max_steps: int = 5,
    timeout_s: float = 600.0,
) -> AudioResult:
    t0 = time.time()

    # ── Fast path: known audio file → transcribe directly ──
    if known_path:
        if not os.path.isfile(known_path):
            return AudioResult(path=known_path, elapsed_s=time.time() - t0, error="File not found")
        _log.info("[audio] fast path: transcribe %s", known_path)
        try:
            result = execute_tool("transcribe_audio", {"file_path": known_path, "language": "en"})
            return AudioResult(
                transcript=str(result),
                path=known_path,
                tool_trace=["transcribe_audio"],
                elapsed_s=time.time() - t0,
            )
        except Exception as e:
            return AudioResult(path=known_path, elapsed_s=time.time() - t0, error=f"{type(e).__name__}: {e}")

    # ── Conversion fast path: "convert X.m4a to mp3" ──
    conv_m = _CONVERT_RE.search(query)
    if conv_m:
        # Find source file path from query
        src_m = _AUDIO_EXT_RE.search(query)
        if src_m:
            src = os.path.expanduser(src_m.group(0))
            if os.path.isfile(src):
                out_fmt = conv_m.group(2).lower()
                _log.info("[audio] convert fast path: %s → %s", src, out_fmt)
                try:
                    r = execute_tool("convert_audio", {"input_path": src, "output_format": out_fmt})
                    return AudioResult(
                        transcript=str(r),
                        path=src,
                        tool_trace=["convert_audio"],
                        elapsed_s=time.time() - t0,
                    )
                except Exception as e:
                    return AudioResult(path=src, elapsed_s=time.time() - t0, error=f"{type(e).__name__}: {e}")

    # ── Extract path from query (handles ~ and absolute paths) ──
    path_m = _AUDIO_EXT_RE.search(query)
    if path_m:
        candidate = os.path.expanduser(path_m.group(0))
        if os.path.isfile(candidate):
            try:
                result = execute_tool("transcribe_audio", {"file_path": candidate, "language": "en"})
                return AudioResult(
                    transcript=str(result),
                    path=candidate,
                    tool_trace=["transcribe_audio"],
                    elapsed_s=time.time() - t0,
                )
            except Exception as e:
                return AudioResult(path=candidate, elapsed_s=time.time() - t0, error=f"{type(e).__name__}: {e}")

    # ── Extract folder path from query for auto-discovery ──
    folder_m = re.search(r"(?:~|/[^\s\"'])[^\s\"']*/", query)
    if folder_m:
        scope = os.path.expanduser(folder_m.group(0))
        if os.path.isdir(scope):
            audio_paths = _discover_audio_in(scope)
            if audio_paths:
                # Transcribe the first one
                try:
                    result = execute_tool("transcribe_audio", {
                        "file_path": audio_paths[0], "language": "en",
                    })
                    return AudioResult(
                        transcript=str(result),
                        path=audio_paths[0],
                        tool_trace=["find_files", "transcribe_audio"],
                        elapsed_s=time.time() - t0,
                    )
                except Exception as e:
                    return AudioResult(
                        path=audio_paths[0],
                        tool_trace=["find_files"],
                        elapsed_s=time.time() - t0,
                        error=f"{type(e).__name__}: {e}",
                    )

    # ── ReAct loop ──
    tools = _audio_tool_schemas()
    tool_names = {t["function"]["name"] for t in tools}
    system = _build_system_prompt(max_steps)
    from tools.memory_tools import mem_block_for
    _mem = mem_block_for(query)
    if _mem:
        system = _mem + "\n" + system

    messages = [
        {"role": "system", "content": system},
        {"role": "user", "content": query},
    ]

    tool_trace: list[str] = []
    transcript: str = ""

    for step in range(max_steps):
        if time.time() - t0 > timeout_s:
            return AudioResult(
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
            return AudioResult(
                tool_trace=tool_trace,
                elapsed_s=time.time() - t0,
                error=f"ollama error: {e}",
            )

        msg = resp.get("message", {})
        content = msg.get("content", "") or ""
        tool_calls = msg.get("tool_calls") or []

        if not tool_calls:
            transcript = content
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

            if name == "transcribe_audio":
                transcript = r_str

            messages.append({"role": "assistant", "content": "", "tool_calls": [tc]})
            messages.append({"role": "tool", "content": r_str})

    return AudioResult(
        transcript=transcript,
        tool_trace=tool_trace,
        elapsed_s=time.time() - t0,
    )
