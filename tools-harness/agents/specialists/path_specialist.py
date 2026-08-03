"""
Path/Discovery specialist — zero-LLM direct execution for common cases.

Fast path (action + pattern parseable from query):
  Execute fd_find / find_files / list_directory directly — 0 LLM calls.

Fallback (intent ambiguous):
  Capped ReAct loop with max 3 LLM calls (down from 8).

Public API:
    run_path_specialist(query, model="gemma4", cwd=None, max_steps=8) -> PathResult
"""

from __future__ import annotations

import json
import logging
import os
import re
import time
from pathlib import Path

import ollama
from pydantic import BaseModel, Field

from tools.registry import ALL_TOOLS, EXECUTORS, execute_tool, tools_with_tags

_log = logging.getLogger("path_specialist")

# ---------------------------------------------------------------------------
# Toolset
# ---------------------------------------------------------------------------

PATH_TOOL_NAMES = tools_with_tags("spec_path")


def _path_tool_schemas() -> list[dict]:
    return [t for t in ALL_TOOLS if t["function"]["name"] in PATH_TOOL_NAMES]


# ---------------------------------------------------------------------------
# Output model
# ---------------------------------------------------------------------------

class PathResult(BaseModel):
    paths: list[str] = Field(default_factory=list)
    summary: str = ""
    tool_trace: list[str] = Field(default_factory=list)
    elapsed_s: float = 0.0
    error: str | None = None


# ---------------------------------------------------------------------------
# System prompt
# ---------------------------------------------------------------------------

_SYSTEM_PROMPT = """You are PATH DISCOVERY. Your only job: resolve the user's reference into absolute file or directory paths on this machine. You do NOT read, transcribe, fill, or write — only locate.

User's home: {home_dir}
Default scope (when none given): {cwd}

TOOLS:
  fd_find(pattern, path)       — FAST file search (23x find, respects .gitignore)
  find_files(pattern, path)    — file search (fallback)
  list_directory(path)         — list contents of one directory
  file_tree(path, depth)       — recursive tree view
  ripgrep(pattern, path)       — FAST content search (36x grep)
  grep_files(pattern, path)    — search file contents for a text pattern
  glob(pattern, path)          — simple glob matching (non-recursive)

RULES:
- Always use absolute paths. "Documents" → {home_dir}/Documents, "Downloads" → {home_dir}/Downloads.
- No scope hint → search from {cwd}.
- Prefer find_files when you know a name/type pattern.
- After the tool result comes back, check whether it found the paths you need.
  If yes: stop and summarize. If no: try a different tool or a broader scope.
- Write your summary as a short sentence — the system will extract paths automatically from your tool results.
- Out-of-scope query (e.g. "transcribe X.m4a"): resolve the referenced path anyway.
- Stop after you have found paths, or after {max_steps} calls."""


def _build_system_prompt(cwd: str, max_steps: int) -> str:
    return _SYSTEM_PROMPT.format(
        home_dir=str(Path.home()),
        cwd=cwd,
        max_steps=max_steps,
    )


# ---------------------------------------------------------------------------
# Path extraction from tool results
# ---------------------------------------------------------------------------

# Matches absolute Unix paths starting with common prefixes
_ABS_PATH_RE = re.compile(
    r"/(?:Users|tmp|var|opt|private|home|usr|etc|Applications|Library|System|Volumes)"
    r"/[^\s\"'`)\]]*"
)


def _extract_paths_from_result(tool_name: str, tool_args: dict, result: str) -> list[str]:
    """
    Extract absolute paths from a tool's output string.

    For list_directory: lines like "name/  dir" are relative — we join with
    the path argument to create absolute paths.
    For find_files/file_tree/grep_files: the output already contains absolute paths.
    """
    result = result or ""

    # find_files, file_tree, glob, fd_find already return absolute paths
    # grep_files, ripgrep return "path:line:match" — strip line info
    if tool_name == "find_files":
        if result.startswith("No files matching") or result.startswith("No files found"):
            return []
        return _dedupe(_ABS_PATH_RE.findall(result))

    if tool_name == "fd_find":
        # fd output: absolute paths, one per line (or "No files matching")
        if result.startswith("No files matching") or result.startswith("No files found"):
            return []
        return _dedupe(_ABS_PATH_RE.findall(result))

    if tool_name == "glob":
        # glob output: absolute paths, one per line (or "No files matching")
        if result.startswith("No files matching") or result.startswith("No files found"):
            return []
        return _dedupe(_ABS_PATH_RE.findall(result))

    if tool_name == "file_tree":
        # file_tree output contains absolute paths inline
        return _dedupe(_ABS_PATH_RE.findall(result))

    if tool_name in {"grep_files", "ripgrep"}:
        raw_paths = []
        for line in result.splitlines():
            line = line.strip()
            if line.startswith("/") and ":" in line:
                raw_paths.append(line.split(":", 1)[0])
            elif line.startswith("/"):
                raw_paths.append(line)
        return _dedupe(raw_paths)

    if tool_name == "list_directory":
        base = str(tool_args.get("path") or tool_args.get("file_path") or "")
        if not base:
            return []

        base = base.rstrip("/")
        paths: list[str] = []

        for line in result.splitlines():
            line = line.strip()
            if not line:
                continue
            # First token is the entry name (may include trailing / for dirs)
            entry = line.split()[0] if line.split() else ""
            if not entry or entry in {".", ".."}:
                continue
            # If entry is already absolute (e.g. "/private/tmp/"), take as-is
            if entry.startswith("/"):
                paths.append(entry.rstrip("/"))
            else:
                paths.append(f"{base}/{entry.rstrip('/')}")
        return _dedupe(paths)

    return []


def _dedupe(items: list[str]) -> list[str]:
    seen: set[str] = set()
    out: list[str] = []
    for item in items:
        # Normalize trailing slashes
        normal = item.rstrip("/")
        if normal not in seen:
            seen.add(normal)
            out.append(normal)
    return out


# ---------------------------------------------------------------------------
# Direct-find fast path (0 LLM calls for common queries)
# ---------------------------------------------------------------------------

_DIR_ALIASES: dict[str, str] = {
    "downloads": "~/Downloads",
    "documents": "~/Documents",
    "desktop": "~/Desktop",
    "home": "~",
    "pictures": "~/Pictures",
    "movies": "~/Movies",
    "music": "~/Music",
    "library": "~/Library",
}

# Type-word → fd brace-expand pattern
_EXT_ALIASES: dict[str, str] = {
    "audio": "*.{m4a,mp3,wav,aac,flac,ogg}",
    "video": "*.{mp4,mkv,mov,avi,m4v,webm}",
    "image": "*.{jpg,jpeg,png,gif,webp,heic,tiff}",
    "pdf":   "*.pdf",
    "document": "*.{pdf,docx,doc,txt,rtf}",
    "spreadsheet": "*.{xlsx,xls,csv,ods}",
    "code":  "*.{py,js,ts,go,rs,java,c,cpp,swift,rb}",
    "script": "*.{py,sh,js,ts,rb}",
    "text":  "*.{txt,md,log}",
}

_FILE_EXT_RE = re.compile(
    r"\b([\w\-.]+\.(?:pdf|docx?|txt|py|js|ts|go|rs|m4a|mp3|wav|mp4|mkv|"
    r"jpg|jpeg|png|csv|json|yaml|yml|sh|md|xlsx|pptx|swift|rb|c|h|cpp|log|"
    r"toml|env|html|css|xml|tsv|ogg|aac|flac|m4v|webp|heic))\b",
    re.IGNORECASE,
)


def _parse_query_intent(query: str, cwd: str) -> tuple[str, str | None, str | None]:
    """
    Return (action, pattern, scope_dir).
    action: "list" | "find" | "grep" | "unknown"
    pattern: glob pattern or None
    scope_dir: absolute directory path or None
    """
    q = query.lower()
    home = str(Path.home())

    # Action
    action = "find"
    if re.search(r"\b(?:list|ls|contents?\s+of|what(?:'s|\s+is)\s+in|show\s+(?:me\s+)?(?:the\s+)?(?:files?|contents?))\b", q):
        action = "list"
    elif re.search(r"\b(?:grep|search\s+(?:for\s+)?text|contains?|with\s+text)\b", q):
        action = "grep"

    # Scope: tilde path
    scope: str | None = None
    m = re.search(r"~/[^\s\"',)]*", query)
    if m:
        candidate = os.path.expanduser(m.group(0))
        if os.path.isdir(candidate):
            scope = candidate
        else:
            parent = str(Path(candidate).parent)
            if os.path.isdir(parent):
                scope = parent

    # Scope: absolute path
    if not scope:
        m = re.search(r"/(?:Users|home|tmp|var|private)[^\s\"',)]*", query)
        if m:
            candidate = m.group(0)
            if os.path.isdir(candidate):
                scope = candidate

    # Scope: named dir aliases
    if not scope:
        for alias, path_tmpl in _DIR_ALIASES.items():
            if re.search(rf"\b{re.escape(alias)}\b", q):
                scope = os.path.expanduser(path_tmpl)
                break

    # Pattern: explicit filename.ext
    pattern: str | None = None
    m = _FILE_EXT_RE.search(query)
    if m:
        pattern = m.group(1).strip()

    # Pattern: glob like *.pdf
    if not pattern:
        m = re.search(r"\*\.?\w+", query)
        if m:
            pattern = m.group(0)

    # Pattern: type-word → extension brace pattern
    if not pattern:
        for type_word, ext_pat in _EXT_ALIASES.items():
            if re.search(rf"\b{re.escape(type_word)}s?\b", q):
                pattern = ext_pat
                break

    return action, pattern, scope


def _try_direct_find(query: str, cwd: str) -> "PathResult | None":
    """
    Attempt path resolution without LLM.
    Returns PathResult on clear intent, None when query is too ambiguous.
    """
    t0 = time.time()
    home = str(Path.home())
    action, pattern, scope = _parse_query_intent(query, cwd)
    tool_trace: list[str] = []
    all_paths: list[str] = []

    # --- list ---
    if action == "list":
        target = scope or cwd
        result = _run_tool("list_directory", {"path": target})
        tool_trace.append("list_directory")
        paths = _extract_paths_from_result("list_directory", {"path": target}, result)
        all_paths.extend(paths)
        summary = f"Listed {target}: {len(paths)} entries" if paths else "Directory empty or not found"
        return PathResult(
            paths=all_paths, summary=summary, tool_trace=tool_trace,
            elapsed_s=time.time() - t0,
            error=None if all_paths else "no paths found",
        )

    # --- find ---
    if action == "find" and pattern:
        search_root = scope or home
        # Try fd_find first (faster), fall back to find_files
        result = _run_tool("fd_find", {"pattern": pattern, "path": search_root})
        if result.startswith("Tool ") or "No files" in result[:40]:
            tool_trace.append("fd_find")
            result = _run_tool("find_files", {"pattern": pattern, "path": search_root})
            tool_trace.append("find_files")
        else:
            tool_trace.append("fd_find")

        paths = _extract_paths_from_result(tool_trace[-1], {"path": search_root}, result)
        all_paths.extend(paths)

        # Broaden to home if specific scope returned nothing
        if not all_paths and scope and scope != home:
            result2 = _run_tool("find_files", {"pattern": pattern, "path": home})
            tool_trace.append("find_files")
            paths2 = _extract_paths_from_result("find_files", {"path": home}, result2)
            all_paths.extend(p for p in paths2 if p not in all_paths)

        summary = f"Found {len(all_paths)} file(s)" if all_paths else "No files found"
        return PathResult(
            paths=all_paths, summary=summary, tool_trace=tool_trace,
            elapsed_s=time.time() - t0,
            error=None if all_paths else "no paths found",
        )

    # --- grep ---
    if action == "grep" and pattern:
        search_root = scope or cwd
        result = _run_tool("ripgrep", {"pattern": pattern, "path": search_root})
        if result.startswith("Tool "):
            result = _run_tool("grep_files", {"pattern": pattern, "path": search_root})
            tool_trace.append("grep_files")
        else:
            tool_trace.append("ripgrep")
        paths = _extract_paths_from_result(tool_trace[-1], {"pattern": pattern, "path": search_root}, result)
        all_paths.extend(paths)
        summary = f"Matched in {len(all_paths)} file(s)" if all_paths else "No matches"
        return PathResult(
            paths=all_paths, summary=summary, tool_trace=tool_trace,
            elapsed_s=time.time() - t0,
            error=None if all_paths else "no paths found",
        )

    return None  # Intent too ambiguous — caller falls back to LLM


# ---------------------------------------------------------------------------
# ReAct loop (fallback)
# ---------------------------------------------------------------------------

def _run_tool(name: str, args: dict) -> str:
    """Thin wrapper used by both the direct-find fast path and the ReAct loop."""
    if name not in PATH_TOOL_NAMES:
        return f"Tool {name!r} unavailable. Allowed: {sorted(PATH_TOOL_NAMES)}"
    if name not in EXECUTORS:
        return f"Tool {name!r} not registered."
    try:
        result = execute_tool(name, args)
        if isinstance(result, str):
            return result
        return json.dumps(result, default=str)
    except Exception as e:
        return f"Tool {name} failed: {type(e).__name__}: {e}"


def run_path_specialist(
    query: str,
    model: str = "gemma4:12b-mlx",
    cwd: str | None = None,
    max_steps: int = 8,   # API compat; effective fallback cap is 3
) -> PathResult:
    t0 = time.time()
    cwd = cwd or os.getcwd()

    # ── Fast path: 0 LLM calls ──
    direct = _try_direct_find(query, cwd)
    if direct is not None:
        _log.info("[path] direct: %d paths tools=%s", len(direct.paths), direct.tool_trace)
        return direct

    # ── Fallback: capped ReAct loop (max 3 LLM calls) ──
    _log.info("[path] LLM fallback: %s", query[:60])
    llm_cap = min(max_steps, 3)
    system_prompt = _build_system_prompt(cwd=cwd, max_steps=llm_cap)
    from tools.memory_tools import mem_block_for
    _mem = mem_block_for(query)
    if _mem:
        system_prompt = _mem + "\n" + system_prompt
    messages: list[dict] = [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": query},
    ]
    tools = _path_tool_schemas()
    tool_trace: list[str] = []
    all_paths: list[str] = []
    consecutive_errors = 0
    model_summary = ""
    error: str | None = None

    for step in range(llm_cap):
        try:
            response = ollama.chat(
                model=model,
                messages=messages,
                tools=tools,
                options={"temperature": 0.0},
            )
        except Exception as e:
            error = f"ollama call failed at step {step}: {type(e).__name__}: {e}"
            _log.warning("[path_specialist] %s", error)
            break

        msg = response.message
        content = (getattr(msg, "content", "") or "").strip()
        tool_calls = getattr(msg, "tool_calls", None)

        if not tool_calls:
            model_summary = content
            break

        # Process tool calls
        tc_list = []
        for tc in tool_calls:
            name = tc.function.name
            args = tc.function.arguments
            if isinstance(args, str):
                try:
                    args = json.loads(args)
                except (json.JSONDecodeError, ValueError):
                    args = {}
            tc_list.append({
                "function": {"name": name, "arguments": args},
            })

            if name not in PATH_TOOL_NAMES:
                tool_trace.append(name)
                messages.append({
                    "role": "tool",
                    "name": name,
                    "content": f"Unknown tool {name!r}. Available: {sorted(PATH_TOOL_NAMES)}",
                })
                continue

            tool_trace.append(name)
            result = _run_tool(name, args)

            # Extract paths from this tool's output
            extracted = _extract_paths_from_result(name, args, result)
            _log.debug("[path_specialist] step=%d tool=%s extracted=%d paths",
                       step, name, len(extracted))
            for p in extracted:
                if p not in all_paths:
                    all_paths.append(p)

            if result.startswith("Tool ") and "failed" in result:
                consecutive_errors += 1
            else:
                consecutive_errors = 0

            if len(result) > 4000:
                result = result[:4000] + "\n...[truncated]"

            messages.append({
                "role": "tool",
                "name": name,
                "content": result,
            })

        messages.append({
            "role": "assistant",
            "content": content,
            "tool_calls": tc_list,
        })

        if consecutive_errors >= 3:
            error = "too many consecutive tool errors"
            _log.warning("[path_specialist] %s", error)
            break

        # Stop early once we have paths — no need for another LLM call
        if all_paths:
            model_summary = content
            break

    # Build summary: prefer model's output, fall back to description
    summary = model_summary.strip() if model_summary else ""
    if not summary and all_paths:
        summary = f"Found {len(all_paths)} path(s)"
    elif not summary:
        summary = "(no paths found)"
    # Trim long summaries
    if len(summary) > 300:
        summary = summary[:300]

    if not all_paths and not error:
        error = "no paths found"

    return PathResult(
        paths=all_paths,
        summary=summary,
        tool_trace=tool_trace,
        elapsed_s=time.time() - t0,
        error=error,
    )
