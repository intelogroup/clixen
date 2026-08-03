"""Spotlight (mdfind) wrapper — instant filesystem search using macOS metadata index.

mdfind indexes filenames + content + EXIF + Spotlight metadata, so it finds
"the budget PDF" or "screenshots from last week" in milliseconds without
walking the filesystem.

Read-only. Returns absolute paths.
"""
from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

_BIN = "mdfind"
_DEFAULT_LIMIT = 25
_TIMEOUT_S = 15


SCHEMA = {
    "type": "function",
    "function": {
        "name": "spotlight_search",
        "description": (
            "Search the macOS Spotlight index for files by name, content, or metadata. "
            "Use for 'find that PDF about X', 'where is the screenshot from yesterday', "
            "'list all docx files I edited this week'. Faster than walking the filesystem. "
            "Returns absolute paths."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "query": {
                    "type": "string",
                    "description": (
                        "Spotlight query. Plain text matches name+content. "
                        "Use 'kind:' for type filter (kind:pdf, kind:image, kind:folder). "
                        "Use 'date:' for time filter (date:today, date:this week). "
                        "Combine: 'budget kind:pdf date:this month'."
                    ),
                },
                "directory": {
                    "type": "string",
                    "description": "Optional dir to scope the search (e.g. ~/Documents). Omit for whole home.",
                    "default": "",
                },
                "limit": {
                    "type": "integer",
                    "description": f"Max paths to return (default {_DEFAULT_LIMIT}, max 100).",
                    "default": _DEFAULT_LIMIT,
                },
            },
            "required": ["query"],
        },
    },
}


def _ensure_binary() -> str | None:
    if shutil.which(_BIN) is None:
        return "[spotlight error] `mdfind` not found (this tool is macOS-only)."
    return None


def execute(query: str, directory: str = "", limit: int = _DEFAULT_LIMIT) -> str:
    err = _ensure_binary()
    if err:
        return err

    q = (query or "").strip()
    if not q:
        return "[spotlight error] empty query."

    cap = max(1, min(int(limit or _DEFAULT_LIMIT), 100))
    cmd = [_BIN]
    if directory:
        target = Path(directory).expanduser()
        if not target.exists():
            return f"[spotlight error] directory not found: {target}"
        cmd += ["-onlyin", str(target)]
    cmd.append(q)

    try:
        result = subprocess.run(
            cmd, capture_output=True, text=True, timeout=_TIMEOUT_S
        )
    except subprocess.TimeoutExpired:
        return f"[spotlight error] timed out after {_TIMEOUT_S}s."
    except OSError as e:
        return f"[spotlight error] could not run mdfind: {e}"

    if result.returncode != 0:
        msg = (result.stderr or "").strip().splitlines()[-1] if result.stderr else "(no stderr)"
        return f"[spotlight error] mdfind exited {result.returncode}: {msg}"

    paths = [p for p in (result.stdout or "").splitlines() if p.strip()]
    if not paths:
        scope = f" in {directory}" if directory else ""
        return f"No Spotlight matches for '{q}'{scope}."

    truncated = paths[:cap]
    header = f"{len(paths)} match{'es' if len(paths) != 1 else ''} for '{q}'"
    if len(paths) > cap:
        header += f" (showing first {cap})"
    return header + ":\n" + "\n".join(truncated)


# ─── find_recent ───────────────────────────────────────────────────────────
# Companion tool: filter by file kind + recency, with optional name/content
# substring. Built on the same mdfind binary, but with a narrower API tuned
# for "what did I touch recently" questions.

_KIND_MAP: dict[str, str] = {
    "pdf":          'kMDItemContentType == "com.adobe.pdf"',
    "image":        'kMDItemContentTypeTree == "public.image"',
    "video":        'kMDItemContentTypeTree == "public.movie"',
    "audio":        'kMDItemContentTypeTree == "public.audio"',
    "doc":          '(kMDItemContentType == "com.microsoft.word.doc" || kMDItemContentType == "org.openxmlformats.wordprocessingml.document")',
    "docx":         'kMDItemContentType == "org.openxmlformats.wordprocessingml.document"',
    "spreadsheet":  '(kMDItemContentTypeTree == "public.spreadsheet" || kMDItemContentType == "org.openxmlformats.spreadsheetml.sheet")',
    "xlsx":         'kMDItemContentType == "org.openxmlformats.spreadsheetml.sheet"',
    "presentation": '(kMDItemContentTypeTree == "public.presentation" || kMDItemContentType == "org.openxmlformats.presentationml.presentation")',
    "pptx":         'kMDItemContentType == "org.openxmlformats.presentationml.presentation"',
    "screenshot":   'kMDItemIsScreenCapture == 1',
    "code":         'kMDItemContentTypeTree == "public.source-code"',
    "markdown":     'kMDItemContentType == "net.daringfireball.markdown"',
    "folder":       'kMDItemContentType == "public.folder"',
    "any":          "",
}

FIND_RECENT_SCHEMA = {
    "type": "function",
    "function": {
        "name": "find_recent",
        "description": (
            "Find files recently modified, optionally filtered by kind and a name/content match. "
            "Use for 'PDFs from last week', 'screenshots today', 'docs I edited yesterday'. "
            "Faster + more precise than spotlight_search when the user wants recency."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "kind": {
                    "type": "string",
                    "description": (
                        "File kind filter. One of: pdf, image, video, audio, doc, docx, "
                        "spreadsheet, xlsx, presentation, pptx, screenshot, code, markdown, "
                        "folder, any. Default 'any'."
                    ),
                    "default": "any",
                },
                "days": {
                    "type": "integer",
                    "description": "Limit to files modified in the last N days (1–90). Default 7.",
                    "default": 7,
                },
                "query": {
                    "type": "string",
                    "description": "Optional substring to match name OR content. Case-insensitive.",
                    "default": "",
                },
                "directory": {
                    "type": "string",
                    "description": "Optional dir to scope (e.g. ~/Documents). Omit for whole index.",
                    "default": "",
                },
                "limit": {
                    "type": "integer",
                    "description": f"Max paths to return (default {_DEFAULT_LIMIT}, max 100).",
                    "default": _DEFAULT_LIMIT,
                },
            },
            "required": [],
        },
    },
}


def find_recent(
    kind: str = "any",
    days: int = 7,
    query: str = "",
    directory: str = "",
    limit: int = _DEFAULT_LIMIT,
) -> str:
    err = _ensure_binary()
    if err:
        return err

    kind_key = (kind or "any").strip().lower()
    if kind_key not in _KIND_MAP:
        return (
            f"[find_recent error] unknown kind '{kind}'. "
            f"Try one of: {', '.join(sorted(_KIND_MAP))}."
        )

    days_i = max(1, min(int(days or 7), 90))
    cap = max(1, min(int(limit or _DEFAULT_LIMIT), 100))

    parts: list[str] = []
    kind_clause = _KIND_MAP[kind_key]
    if kind_clause:
        parts.append(kind_clause)
    parts.append(f"kMDItemFSContentChangeDate >= $time.today(-{days_i})")
    q = (query or "").strip()
    if q:
        # Escape embedded double quotes for the mdfind expression.
        q_safe = q.replace('"', '\\"')
        parts.append(
            f'(kMDItemDisplayName == "*{q_safe}*"c '
            f'|| kMDItemTextContent == "*{q_safe}*"c)'
        )

    expr = " && ".join(parts)
    cmd = [_BIN]
    if directory:
        target = Path(directory).expanduser()
        if not target.exists():
            return f"[find_recent error] directory not found: {target}"
        cmd += ["-onlyin", str(target)]
    cmd.append(expr)

    try:
        result = subprocess.run(
            cmd, capture_output=True, text=True, timeout=_TIMEOUT_S
        )
    except subprocess.TimeoutExpired:
        return f"[find_recent error] timed out after {_TIMEOUT_S}s."
    except OSError as e:
        return f"[find_recent error] could not run mdfind: {e}"

    if result.returncode != 0:
        msg = (result.stderr or "").strip().splitlines()[-1] if result.stderr else "(no stderr)"
        return f"[find_recent error] mdfind exited {result.returncode}: {msg}"

    paths = [p for p in (result.stdout or "").splitlines() if p.strip()]
    if not paths:
        bits = []
        if kind_key != "any":
            bits.append(f"kind={kind_key}")
        bits.append(f"days={days_i}")
        if q:
            bits.append(f"query='{q}'")
        if directory:
            bits.append(f"in={directory}")
        return "No recent matches (" + ", ".join(bits) + ")."

    truncated = paths[:cap]
    parts_label = []
    if kind_key != "any":
        parts_label.append(f"kind={kind_key}")
    parts_label.append(f"last {days_i}d")
    if q:
        parts_label.append(f"'{q}'")
    header = f"{len(paths)} match{'es' if len(paths) != 1 else ''} ({', '.join(parts_label)})"
    if len(paths) > cap:
        header += f" — showing first {cap}"
    return header + ":\n" + "\n".join(truncated)
