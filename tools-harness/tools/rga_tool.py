"""ripgrep-all (rga) wrapper — full-text search inside PDFs, docx, zip, e-books, etc.

rga is a ripgrep front-end with adapters for pandoc (epub/docx/odt/html),
poppler (pdf), ffmpeg (audio/video metadata + subtitles), and zip recursion.

We expose it as a single `archive_grep` tool so the agent can answer
"have I written about X anywhere on disk" without pre-extracting every file.
"""
from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

_BIN = "rga"
_DEFAULT_MAX_COUNT = 5  # max matches per file
_DEFAULT_MAX_FILES = 30  # cap total result lines we return to the model
_TIMEOUT_S = 60


SCHEMA = {
    "type": "function",
    "function": {
        "name": "archive_grep",
        "description": (
            "Full-text search INSIDE PDFs, docx, epub, odt, zip, and other binary "
            "formats using ripgrep-all. Use when the user asks 'have I written about X', "
            "'find the contract that mentions Y', 'search my downloads/PDFs for Z'. "
            "No pre-extraction needed — rga handles format adapters internally. "
            "Returns matching files with line/page context."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "pattern": {
                    "type": "string",
                    "description": "Regex or literal string to search for (ripgrep syntax).",
                },
                "path": {
                    "type": "string",
                    "description": (
                        "Directory or file to search. Defaults to current working dir. "
                        "Tip: use ~/Downloads, ~/Documents, or a project root."
                    ),
                    "default": ".",
                },
                "max_count": {
                    "type": "integer",
                    "description": f"Max matches per file (default {_DEFAULT_MAX_COUNT}).",
                    "default": _DEFAULT_MAX_COUNT,
                },
                "case_insensitive": {
                    "type": "boolean",
                    "description": "Case-insensitive match. Default true.",
                    "default": True,
                },
            },
            "required": ["pattern"],
        },
    },
}


def _ensure_binary() -> str | None:
    if shutil.which(_BIN) is None:
        return (
            "[archive_grep error] `rga` (ripgrep-all) not installed. "
            "Run: brew install ripgrep-all"
        )
    return None


def execute(pattern: str, path: str = ".", max_count: int = _DEFAULT_MAX_COUNT,
            case_insensitive: bool = True) -> str:
    err = _ensure_binary()
    if err:
        return err

    pattern = (pattern or "").strip()
    if not pattern:
        return "[archive_grep error] empty pattern."

    target = Path(path or ".").expanduser()
    if not target.exists():
        return f"[archive_grep error] path does not exist: {target}"

    cmd = [
        _BIN,
        "--max-count", str(max(1, min(int(max_count or _DEFAULT_MAX_COUNT), 25))),
        "--no-heading",
        "--with-filename",
        "--line-number",
        "--color", "never",
    ]
    if case_insensitive:
        cmd.append("-i")
    cmd += [pattern, str(target)]

    try:
        result = subprocess.run(
            cmd, capture_output=True, text=True, timeout=_TIMEOUT_S
        )
    except subprocess.TimeoutExpired:
        return f"[archive_grep error] timed out after {_TIMEOUT_S}s. Narrow the path or pattern."
    except OSError as e:
        return f"[archive_grep error] could not run rga: {e}"

    if result.returncode == 1 and not result.stdout:
        return f"No matches for '{pattern}' under {target}."
    if result.returncode not in (0, 1):
        msg = (result.stderr or "").strip().splitlines()[-1] if result.stderr else ""
        return f"[archive_grep error] rga exited {result.returncode}: {msg}"

    lines = (result.stdout or "").splitlines()
    if not lines:
        return f"No matches for '{pattern}' under {target}."

    # Group by file, cap total lines for context-friendliness
    out: list[str] = []
    files_seen: dict[str, int] = {}
    for line in lines:
        # rga format: <path>:<lineno>:<text>  (or with page for PDFs)
        parts = line.split(":", 2)
        if len(parts) < 3:
            continue
        fpath, _ln, text = parts
        files_seen[fpath] = files_seen.get(fpath, 0) + 1
        if files_seen[fpath] > max_count:
            continue
        out.append(line)
        if len(out) >= _DEFAULT_MAX_FILES * max_count:
            break

    header = f"{len(out)} match{'es' if len(out) != 1 else ''} for '{pattern}' across {len(files_seen)} file(s):"
    return header + "\n" + "\n".join(out)
