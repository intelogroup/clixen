"""
Fast search tools — fd (find), ripgrep (grep), Python glob.

Best OSS: fd (42.9k stars) — 23x faster than find, written in Rust, respects .gitignore.
           ripgrep (63.5k stars) — 36x faster than grep, parallel, SIMD regex.

These wrappers call fd/rg via subprocess when available, falling back to Python
implementations. The original find_files/grep_files in filesystem.py remain
unchanged — these are additions, not replacements.
"""

import fnmatch
import logging
import os
import subprocess
from glob import glob as py_glob
from pathlib import Path

_log = logging.getLogger("fast_search")

# ---------------------------------------------------------------------------
# glob — Python glob.glob (non-recursive, no .gitignore)
# ---------------------------------------------------------------------------

GLOB_SCHEMA = {
    "type": "function",
    "function": {
        "name": "glob",
        "description": (
            "Find files matching a glob pattern in a directory (non-recursive). "
            "Use for simple filename matching like *.py, *.txt. Does NOT search subdirectories. "
            "For recursive search use fd_find or find_files."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "pattern": {
                    "type": "string",
                    "description": "Glob pattern like '*.py', 'test_*.txt', 'data/**/*.csv' (recursive if ** used)",
                },
                "path": {
                    "type": "string",
                    "description": "Directory to search in (default: current directory)",
                    "default": ".",
                },
            },
            "required": ["pattern"],
        },
    },
}


def glob_execute(pattern: str, path: str = ".") -> str:
    """Run glob.glob and return results."""
    root = Path(path).expanduser().resolve()
    full_pattern = str(root / pattern)
    try:
        matches = py_glob(full_pattern, recursive=("**" in pattern))
        if not matches:
            return f"No files matching '{pattern}' in {root}"
        return "\n".join(sorted(matches)[:50])
    except Exception as e:
        return f"glob failed: {type(e).__name__}: {e}"


# ---------------------------------------------------------------------------
# fd_find — fast file finding (fd via subprocess)
# ---------------------------------------------------------------------------

FD_SCHEMA = {
    "type": "function",
    "function": {
        "name": "fd_find",
        "description": (
            "Fast recursive file search using fd (Rust). 23x faster than find. "
            "Respects .gitignore by default. Best for finding files by name or extension. "
            "Use this instead of find_files when fd is available for dramatically faster results."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "pattern": {
                    "type": "string",
                    "description": "Filename pattern (regex or glob). E.g. 'test.*\\.py', '*.rs', 'README'",
                },
                "path": {
                    "type": "string",
                    "description": "Root directory to search (default: current)",
                    "default": ".",
                },
                "extension": {
                    "type": "string",
                    "description": "Filter by file extension: 'py', 'rs', 'txt', etc.",
                    "default": "",
                },
                "max_results": {
                    "type": "integer",
                    "description": "Max results to return (default: 30)",
                    "default": 30,
                },
                "no_ignore": {
                    "type": "boolean",
                    "description": "Disable .gitignore filtering (show all files)",
                    "default": False,
                },
            },
            "required": ["pattern"],
        },
    },
}


def fd_execute(pattern: str, path: str = ".", extension: str = "",
               max_results: int = 30, no_ignore: bool = False) -> str:
    """Run fd and return results."""
    root = Path(path).expanduser().resolve()

    try:
        cmd = ["fd", "--color", "never", "-g"]

        if extension:
            cmd += ["-e", extension]

        if no_ignore:
            cmd += ["-I", "-H"]  # no-ignore + hidden
        else:
            cmd += ["-H"]  # show hidden but respect .gitignore

        cmd += ["--max-results", str(max_results)]
        cmd += [pattern, str(root)]

        result = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
        if result.returncode != 0 and result.stderr:
            # fd error (e.g. bad pattern) — surface the reason, then fall back
            _log.warning("fd failed on %r: %s — falling back to find_files", pattern, result.stderr.strip()[:120])
            return _fallback_find_files(pattern, str(root), max_results)

        output = result.stdout.strip()
        if not output:
            return f"No files matching '{pattern}' under {root}"
        return output

    except FileNotFoundError:
        _log.warning("fd binary not found — falling back to find_files")
        return _fallback_find_files(pattern, str(root), max_results)
    except subprocess.TimeoutExpired:
        return f"fd timed out searching {root}"
    except Exception as e:
        return f"fd failed: {type(e).__name__}: {e}"


def _fallback_rglob(root: Path, pattern: str):
    """rglob with per-dir OSError catch (iCloud deadlock fix)."""
    _SKIP = {".git", "__pycache__", "node_modules", ".venv", "venv", ".cache"}
    for dirpath, dirs, files in os.walk(root, onerror=lambda _: None):
        dirs[:] = [d for d in dirs if d not in _SKIP]
        for f in fnmatch.filter(files, pattern):
            yield Path(dirpath) / f


def _fallback_find_files(pattern: str, path: str, max_results: int) -> str:
    """Fallback to Python walk when fd is not available."""
    root = Path(path)
    if not root.exists():
        return f"Path not found: {root}"

    found = []
    for fp in _fallback_rglob(root, pattern):
        found.append(str(fp))
        if len(found) >= max_results:
            break

    if not found:
        return f"No files matching '{pattern}' under {root}"
    return "\n".join(found)


# ---------------------------------------------------------------------------
# ripgrep — fast content search (ripgrep via subprocess)
# ---------------------------------------------------------------------------

RG_SCHEMA = {
    "type": "function",
    "function": {
        "name": "ripgrep",
        "description": (
            "Fast regex content search using ripgrep (Rust). 36x faster than grep. "
            "Respects .gitignore by default. Best for searching file contents for patterns. "
            "Use this instead of grep_files for dramatically faster content search."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "pattern": {
                    "type": "string",
                    "description": "Regex or literal pattern to search for",
                },
                "path": {
                    "type": "string",
                    "description": "Directory or file to search in (default: current)",
                    "default": ".",
                },
                "file_type": {
                    "type": "string",
                    "description": "Limit to file type: 'py', 'rs', 'js', 'ts', 'md', etc.",
                    "default": "",
                },
                "max_results": {
                    "type": "integer",
                    "description": "Max matching lines to return (default: 50)",
                    "default": 50,
                },
                "ignore_case": {
                    "type": "boolean",
                    "description": "Case-insensitive search",
                    "default": True,
                },
                "literal": {
                    "type": "boolean",
                    "description": "Treat pattern as literal string (not regex)",
                    "default": False,
                },
                "no_ignore": {
                    "type": "boolean",
                    "description": "Disable .gitignore filtering (search all files)",
                    "default": False,
                },
            },
            "required": ["pattern"],
        },
    },
}


def rg_execute(pattern: str, path: str = ".", file_type: str = "",
               max_results: int = 50, ignore_case: bool = True,
               literal: bool = False, no_ignore: bool = False) -> str:
    """Run ripgrep and return results."""
    root = Path(path).expanduser().resolve()

    try:
        cmd = ["rg", "--color", "never", "--line-number", "--no-heading"]

        if ignore_case:
            cmd += ["-i"]
        if literal:
            cmd += ["-F"]
        if file_type:
            cmd += ["-t", file_type]
        if no_ignore:
            cmd += ["--no-ignore", "--hidden"]
        else:
            cmd += ["--hidden"]  # show hidden but respect .gitignore

        cmd += ["-m", str(max_results)]
        cmd += [pattern, str(root)]

        result = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
        if result.returncode not in (0, 1):
            return f"ripgrep failed: {result.stderr[:300]}"

        output = result.stdout.strip()
        if not output:
            return f"No matches for '{pattern}' in {root}"
        return output

    except FileNotFoundError:
        return _fallback_grep_files(pattern, str(root), max_results, ignore_case)
    except subprocess.TimeoutExpired:
        return f"ripgrep timed out searching {root}"
    except Exception as e:
        return f"ripgrep failed: {type(e).__name__}: {e}"


def _fallback_grep_files(pattern: str, path: str, max_results: int,
                         ignore_case: bool) -> str:
    """Fallback to Python when rg is not available."""
    import re
    from tools.filesystem import grep_files
    return grep_files(
        pattern=pattern, path=path, max_results=max_results,
        ignore_case=ignore_case,
    )
