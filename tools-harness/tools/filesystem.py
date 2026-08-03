"""
Local filesystem tools — lets the model read, search, grep, and manage files on this machine.

Read tools:   read_file, grep_files, find_files, list_directory, file_tree, read_many_files, file_info, find_largest
Write tools:  rename_file, delete_file, create_directory, copy_file
"""

import fnmatch
import os
import re
import shutil
import stat
from datetime import datetime
from pathlib import Path

_HOME = str(Path.home())

# Set per-request by the harness (run()) when an IDE/project folder is known, so that bare
# relative paths the model emits ("pyproject.toml") resolve against the open project rather
# than the server's process cwd (tools-harness/). None = fall back to cwd/home as before.
_PROJECT_ROOT: str | None = None


def set_project_root(path: str | None) -> None:
    global _PROJECT_ROOT
    _PROJECT_ROOT = path or None


def get_project_root() -> str | None:
    return _PROJECT_ROOT


def _resolve_path(path_str: str) -> Path:
    p = Path(path_str).expanduser()

    # Linux-style home path from the model on a macOS box (/home/user/... -> ~/...).
    if not p.exists() and path_str.startswith("/home/"):
        home = os.path.expanduser("~")
        fallback = Path(home) / "/".join(Path(path_str).parts[3:])
        if fallback.exists():
            p = fallback.resolve()

    # Bare relative path ("pyproject.toml", "src/app.py"): if it doesn't exist relative to
    # the process cwd, try the open project root, then the home directory.
    if not p.is_absolute() and not p.exists():
        for base in (_PROJECT_ROOT, _HOME):
            if not base:
                continue
            candidate = Path(base) / path_str
            if candidate.exists():
                p = candidate.resolve()
                break

    from tools.path_policy import validate_path
    validated_str = validate_path(str(p), write=False)
    return Path(validated_str).resolve()


# ---------------------------------------------------------------------------
# Schemas
# ---------------------------------------------------------------------------

READ_FILE_SCHEMA = {
    "type": "function",
    "function": {
        "name": "read_file",
        "description": (
            "Read the contents of a file on this computer. "
            "Use to inspect source code, configs, logs, or any text file. "
            "Supports optional line range to avoid reading huge files at once."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "path": {
                    "type": "string",
                    "description": "Absolute or ~ path to the file",
                },
                "offset": {
                    "type": "integer",
                    "description": "Line number to start reading from (1-based, default 1)",
                    "default": 1,
                },
                "limit": {
                    "type": "integer",
                    "description": "Max lines to return (default 200)",
                    "default": 200,
                },
            },
            "required": ["path"],
        },
    },
}

GREP_FILES_SCHEMA = {
    "type": "function",
    "function": {
        "name": "grep_files",
        "description": (
            "Search file contents for a regex pattern, like grep -r. "
            "Use to find where a function is defined, find a config value, "
            "locate an error message, or search logs. "
            "Returns matching lines with file path and line number."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "pattern": {
                    "type": "string",
                    "description": "Regex pattern to search for",
                },
                "path": {
                    "type": "string",
                    "description": "Directory or file to search in (default: home directory)",
                    "default": _HOME,
                },
                "glob": {
                    "type": "string",
                    "description": "File name pattern to filter (e.g. '*.py', '*.log'). Default: all files.",
                    "default": "*",
                },
                "max_results": {
                    "type": "integer",
                    "description": "Max matching lines to return (default 30)",
                    "default": 30,
                },
                "ignore_case": {
                    "type": "boolean",
                    "description": "Case-insensitive match (default false)",
                    "default": False,
                },
            },
            "required": ["pattern"],
        },
    },
}

LIST_DIR_SCHEMA = {
    "type": "function",
    "function": {
        "name": "list_directory",
        "description": (
            "List files and subdirectories in a directory with sizes and types. "
            "Use to explore what's in a folder before deciding which files to read."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "path": {
                    "type": "string",
                    "description": "Directory path (default: home directory)",
                    "default": _HOME,
                },
                "show_hidden": {
                    "type": "boolean",
                    "description": "Include hidden files/dirs starting with dot (default false)",
                    "default": False,
                },
                "limits": {
                    "type": "integer",
                    "description": "Max entries to return (default 50). Use find_files to search for specific file types.",
                    "default": 50,
                },
            },
            "required": [],
        },
    },
}

FILE_TREE_SCHEMA = {
    "type": "function",
    "function": {
        "name": "file_tree",
        "description": (
            "Show a recursive tree view of a directory structure, like the 'tree' command. "
            "Use to understand a project layout before diving into individual files."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "path": {
                    "type": "string",
                    "description": "Root directory to show tree for",
                },
                "max_depth": {
                    "type": "integer",
                    "description": "How deep to recurse (default 3)",
                    "default": 3,
                },
                "show_hidden": {
                    "type": "boolean",
                    "description": "Include hidden files (default false)",
                    "default": False,
                },
            },
            "required": ["path"],
        },
    },
}

READ_MANY_SCHEMA = {
    "type": "function",
    "function": {
        "name": "read_many_files",
        "description": (
            "Read multiple files in a single call. "
            "Use when you need to compare files or understand several related files together."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "paths": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "List of file paths to read",
                },
                "limit_per_file": {
                    "type": "integer",
                    "description": "Max lines per file (default 100)",
                    "default": 100,
                },
            },
            "required": ["paths"],
        },
    },
}

FIND_FILES_SCHEMA = {
    "type": "function",
    "function": {
        "name": "find_files",
        "description": (
            "Find files on this computer by name pattern. "
            "Use to locate a file when you know its name or extension but not exact path. "
            "Supports glob patterns like '*.py', 'config.*', 'telegram_bot*'."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "pattern": {
                    "type": "string",
                    "description": "Glob pattern for the filename (e.g. '*.py', 'requirements*')",
                },
                "path": {
                    "type": "string",
                    "description": "Directory to search in (default: home directory)",
                    "default": _HOME,
                },
                "max_results": {
                    "type": "integer",
                    "description": "Max files to return (default 20)",
                    "default": 20,
                },
            },
            "required": ["pattern"],
        },
    },
}

# ---------------------------------------------------------------------------
# Executors
# ---------------------------------------------------------------------------

_MAX_FILE_BYTES = 100_000  # 100 KB hard cap on any single file read


def read_file(path: str, offset: int = 1, limit: int = 200) -> str:
    p = _resolve_path(path)
    if not p.exists():
        return f"File not found: {p}"
    if not p.is_file():
        return f"Not a file: {p}"

    try:
        size = p.stat().st_size
        raw = p.read_bytes()[:_MAX_FILE_BYTES].decode("utf-8", errors="replace")
    except PermissionError:
        return f"Permission denied: {p}"

    lines = raw.splitlines()
    total = len(lines)
    start = max(0, offset - 1)
    chunk = lines[start : start + limit]

    end = start + len(chunk)
    header = f"File: {p}  ({total} lines total"
    if size > _MAX_FILE_BYTES:
        header += f", truncated to {_MAX_FILE_BYTES // 1000}KB"
    header += ")"

    nav = ""
    if end < total:
        nav = f"\n[lines {start + 1}-{end} of {total} — next page: read_file(path, offset={end + 1})]"

    numbered = "\n".join(f"{start + i + 1:4}: {ln}" for i, ln in enumerate(chunk))
    return f"{header}\n\n{numbered}{nav}"


def grep_files(
    pattern: str,
    path: str = _HOME,
    glob: str = "*",
    max_results: int = 30,
    ignore_case: bool = False,
) -> str:
    root = _resolve_path(path)
    if not root.exists():
        return f"Path not found: {root}"

    flags = re.IGNORECASE if ignore_case else 0
    try:
        rx = re.compile(pattern, flags)
    except re.error as e:
        return f"Invalid regex: {e}"

    # Directories to skip — avoid binary/large dirs that would just waste time
    _SKIP_DIRS = {
        ".git", "__pycache__", "node_modules", ".venv", "venv",
        ".cache", "site-packages", "dist", "build", ".tox",
    }

    matches = []
    files_searched = 0

    if root.is_file():
        candidates = [root]
    else:
        candidates = root.rglob(glob)

    for fp in candidates:
        try:
            if not fp.is_file():
                continue
        except (PermissionError, OSError):
            continue
        # Skip blacklisted directories anywhere in the path
        if any(part in _SKIP_DIRS for part in fp.parts):
            continue
        # Skip binary-looking files by extension
        if fp.suffix.lower() in {
            ".pyc", ".pyo", ".so", ".dylib", ".bin", ".exe",
            ".jpg", ".jpeg", ".png", ".gif", ".mp3", ".mp4",
            ".wav", ".ogg", ".pdf", ".zip", ".tar", ".gz",
            ".onnx", ".gguf", ".safetensors", ".pt", ".pth",
        }:
            continue
        try:
            text = fp.read_bytes()[:_MAX_FILE_BYTES].decode("utf-8", errors="replace")
        except (PermissionError, OSError):
            continue

        files_searched += 1
        for lineno, line in enumerate(text.splitlines(), 1):
            if rx.search(line):
                matches.append(f"{fp}:{lineno}: {line.rstrip()}")
                if len(matches) >= max_results:
                    break
        if len(matches) >= max_results:
            break

    if not matches:
        return f"No matches for '{pattern}' in {root} (searched {files_searched} files)"

    result = "\n".join(matches)
    if len(matches) >= max_results:
        result += f"\n... (limit {max_results} reached)"
    result += f"\n\n({files_searched} files searched)"
    return result


def _safe_rglob(root: Path, pattern: str):
    """rglob with per-dir OSError catch (iCloud deadlock fix).
    os.walk onerror skips locked dirs instead of crashing the whole search."""
    _SKIP_DIRS = {
        ".git", "__pycache__", "node_modules", ".venv", "venv",
        ".cache", "site-packages", "dist", "build",
        "Mobile Documents",
    }
    for dirpath, dirs, files in os.walk(root, onerror=lambda _: None):
        dirs[:] = [d for d in dirs if d not in _SKIP_DIRS]
        for f in fnmatch.filter(files, pattern):
            yield Path(dirpath) / f


def find_files(pattern: str, path: str = _HOME, max_results: int = 20) -> str:
    root = _resolve_path(path)
    if not root.exists():
        return f"Path not found: {root}"

    found = []
    for fp in _safe_rglob(root, pattern):
        found.append(str(fp))
        if len(found) >= max_results:
            break

    if not found:
        return f"No files matching '{pattern}' under {root}"

    result = "\n".join(found)
    if len(found) >= max_results:
        result += f"\n... (limit {max_results} reached)"
    return result


def list_directory(path: str = _HOME, show_hidden: bool = False, limits: int = 50) -> str:
    p = _resolve_path(path)
    if not p.exists():
        return f"Path not found: {p}"
    if not p.is_dir():
        return f"Not a directory: {p}"

    entries = []
    try:
        items = sorted(p.iterdir(), key=lambda x: (x.is_file(), x.name.lower()))
    except PermissionError:
        return f"Permission denied: {p}"

    truncated = False
    for item in items:
        if not show_hidden and item.name.startswith("."):
            continue
        if len(entries) >= limits:
            truncated = True
            break
        try:
            stat = item.stat()
            size = stat.st_size
            if item.is_dir():
                size_str = "dir"
            elif size < 1024:
                size_str = f"{size}B"
            elif size < 1_048_576:
                size_str = f"{size // 1024}KB"
            else:
                size_str = f"{size // 1_048_576}MB"
            kind = "/" if item.is_dir() else ""
            entries.append(f"{item.name}{kind:<30}  {size_str}")
        except OSError:
            entries.append(item.name)

    if not entries:
        return f"Empty directory: {p}"
    result = f"{p}/\n" + "\n".join(entries)
    if truncated:
        result += f"\n\n[Showing first {limits} entries out of many. Use find_files with a glob pattern to search for specific file types, e.g. find_files(pattern='*.m4a', path='{p}')]"
    return result


def file_tree(path: str, max_depth: int = 3, show_hidden: bool = False) -> str:
    root = _resolve_path(path)
    if not root.exists():
        return f"Path not found: {root}"

    _SKIP = {".git", "__pycache__", "node_modules", ".venv", "venv", ".cache",
              "site-packages", "dist", "build", ".next", ".nuxt", ".output",
              "out", ".turbo", ".vercel", ".svelte-kit"}
    lines = [str(root)]

    def _walk(p: Path, prefix: str, depth: int):
        if depth > max_depth:
            return
        try:
            items = sorted(p.iterdir(), key=lambda x: (x.is_file(), x.name.lower()))
        except PermissionError:
            return
        visible = [i for i in items if show_hidden or not i.name.startswith(".")]
        for idx, item in enumerate(visible):
            if item.name in _SKIP:
                continue
            connector = "└── " if idx == len(visible) - 1 else "├── "
            lines.append(f"{prefix}{connector}{item.name}{'/' if item.is_dir() else ''}")
            if item.is_dir():
                extension = "    " if idx == len(visible) - 1 else "│   "
                _walk(item, prefix + extension, depth + 1)

    _walk(root, "", 1)
    return "\n".join(lines)


def read_many_files(paths: list, limit_per_file: int = 100) -> str:
    parts = []
    for path in paths:
        parts.append(f"\n{'='*60}\n{path}\n{'='*60}")
        parts.append(read_file(path, limit=limit_per_file))
    return "\n".join(parts)


# ---------------------------------------------------------------------------
# File management schemas
# ---------------------------------------------------------------------------

FILE_INFO_SCHEMA = {
    "type": "function",
    "function": {
        "name": "file_info",
        "description": (
            "Get detailed metadata for a file or directory: size, type, permissions, "
            "created/modified timestamps. Use before renaming or deleting to confirm the right path."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "path": {
                    "type": "string",
                    "description": "Absolute or ~ path to the file or directory",
                },
            },
            "required": ["path"],
        },
    },
}

FIND_LARGEST_SCHEMA = {
    "type": "function",
    "function": {
        "name": "find_largest",
        "description": (
            "Find the N largest files in a directory (recursive). "
            "Use to answer 'what is the biggest file in X?' or 'show me large files taking up space'."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "path": {
                    "type": "string",
                    "description": "Directory to search (default: home directory)",
                    "default": _HOME,
                },
                "n": {
                    "type": "integer",
                    "description": "Number of largest files to return (default 10)",
                    "default": 10,
                },
                "show_hidden": {
                    "type": "boolean",
                    "description": "Include hidden files (default false)",
                    "default": False,
                },
            },
            "required": [],
        },
    },
}

RENAME_FILE_SCHEMA = {
    "type": "function",
    "function": {
        "name": "rename_file",
        "description": (
            "Rename or move a file or directory. "
            "Use for 'rename X to Y', 'move X to Y folder'. "
            "If dest is a directory, the file is moved inside it."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "src": {
                    "type": "string",
                    "description": "Current path of the file or directory",
                },
                "dest": {
                    "type": "string",
                    "description": "New path or destination directory",
                },
            },
            "required": ["src", "dest"],
        },
    },
}

DELETE_FILE_SCHEMA = {
    "type": "function",
    "function": {
        "name": "delete_file",
        "description": (
            "Delete a file or directory. "
            "Set recursive=true to delete a non-empty directory and all its contents. "
            "CAUTION: deletion is permanent — always confirm the path with file_info first."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "path": {
                    "type": "string",
                    "description": "Path to the file or directory to delete",
                },
                "recursive": {
                    "type": "boolean",
                    "description": "Delete directory and all contents (default false)",
                    "default": False,
                },
            },
            "required": ["path"],
        },
    },
}

CREATE_DIRECTORY_SCHEMA = {
    "type": "function",
    "function": {
        "name": "create_directory",
        "description": (
            "Create a new directory (and any missing parent directories). "
            "Use for 'create folder X', 'make directory Y', 'mkdir Z'."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "path": {
                    "type": "string",
                    "description": "Path of the directory to create",
                },
            },
            "required": ["path"],
        },
    },
}

COPY_FILE_SCHEMA = {
    "type": "function",
    "function": {
        "name": "copy_file",
        "description": (
            "Copy a file or directory to a new location. "
            "If dest is a directory, the file is copied inside it. "
            "Set recursive=true to copy a directory and all its contents."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "src": {
                    "type": "string",
                    "description": "Source file or directory path",
                },
                "dest": {
                    "type": "string",
                    "description": "Destination path or directory",
                },
                "recursive": {
                    "type": "boolean",
                    "description": "Copy directory recursively (default false)",
                    "default": False,
                },
            },
            "required": ["src", "dest"],
        },
    },
}

# ---------------------------------------------------------------------------
# Safety guard — block operations on OS-critical paths
# ---------------------------------------------------------------------------

_BLOCKED_PREFIXES = (
    "/System", "/usr", "/bin", "/sbin", "/etc",
    "/private/etc", "/private/var/db",
    "/Library/Apple", "/Library/CoreServices",
)


def _safe_path(path: str) -> tuple[Path, str | None]:
    """Expand and resolve path. Return (Path, error_msg_or_None)."""
    from tools.path_policy import validate_path
    try:
        validated_str = validate_path(path, write=True)
        p = Path(validated_str)
    except PermissionError as e:
        return Path(path).expanduser().resolve(), f"Blocked: {e}"
    for blocked in _BLOCKED_PREFIXES:
        if str(p).startswith(blocked):
            return p, f"Blocked: {p} is in a protected system path ({blocked})"
    return p, None


# ---------------------------------------------------------------------------
# File management executors
# ---------------------------------------------------------------------------

def file_info(path: str) -> str:
    p = Path(path).expanduser().resolve()
    if not p.exists():
        return f"Not found: {p}"
    try:
        s = p.stat()
    except PermissionError:
        return f"Permission denied: {p}"

    kind = "directory" if p.is_dir() else "file"
    size_bytes = s.st_size
    if size_bytes < 1024:
        size_str = f"{size_bytes} B"
    elif size_bytes < 1_048_576:
        size_str = f"{size_bytes / 1024:.1f} KB"
    elif size_bytes < 1_073_741_824:
        size_str = f"{size_bytes / 1_048_576:.1f} MB"
    else:
        size_str = f"{size_bytes / 1_073_741_824:.2f} GB"

    modified = datetime.fromtimestamp(s.st_mtime).strftime("%Y-%m-%d %H:%M:%S")
    created = datetime.fromtimestamp(s.st_ctime).strftime("%Y-%m-%d %H:%M:%S")
    perms = stat.filemode(s.st_mode)

    return (
        f"Path:     {p}\n"
        f"Type:     {kind}\n"
        f"Size:     {size_str} ({size_bytes:,} bytes)\n"
        f"Modified: {modified}\n"
        f"Created:  {created}\n"
        f"Perms:    {perms}"
    )


def find_largest(path: str = _HOME, n: int = 10, show_hidden: bool = False) -> str:
    root = Path(path).expanduser().resolve()
    if not root.exists():
        return f"Path not found: {root}"
    if not root.is_dir():
        return f"Not a directory: {root}"

    _SKIP_DIRS = {".git", "__pycache__", "node_modules", ".venv", "venv",
                  ".cache", "site-packages", "dist", "build"}

    files: list[tuple[int, Path]] = []
    for fp in _safe_rglob(root, "*"):
        if not fp.is_file():
            continue
        if not show_hidden and any(part.startswith(".") for part in fp.relative_to(root).parts):
            continue
        if any(part in _SKIP_DIRS for part in fp.parts):
            continue
        try:
            files.append((fp.stat().st_size, fp))
        except OSError:
            continue

    files.sort(reverse=True)
    top = files[:n]

    if not top:
        return f"No files found under {root}"

    lines = [f"Largest {n} files in {root}:"]
    for size, fp in top:
        if size < 1024:
            s = f"{size} B"
        elif size < 1_048_576:
            s = f"{size / 1024:.1f} KB"
        elif size < 1_073_741_824:
            s = f"{size / 1_048_576:.1f} MB"
        else:
            s = f"{size / 1_073_741_824:.2f} GB"
        lines.append(f"  {s:<12}  {fp}")
    return "\n".join(lines)


def rename_file(src: str, dest: str) -> str:
    src_p, err = _safe_path(src)
    if err:
        return err
    dest_p, err = _safe_path(dest)
    if err:
        return err

    if not src_p.exists():
        return f"Source not found: {src_p}"

    # If dest is an existing directory, move inside it
    if dest_p.is_dir():
        dest_p = dest_p / src_p.name

    if dest_p.exists():
        return f"Destination already exists: {dest_p}. Choose a different name."

    try:
        src_p.rename(dest_p)
        return f"Renamed: {src_p} → {dest_p}"
    except OSError as e:
        return f"Error: {e}"


def delete_file(path: str, recursive: bool = False) -> str:
    p, err = _safe_path(path)
    if err:
        return err

    if not p.exists():
        return f"Not found: {p}"

    try:
        if p.is_file() or p.is_symlink():
            p.unlink()
            return f"Deleted file: {p}"
        elif p.is_dir():
            if recursive:
                shutil.rmtree(p)
                return f"Deleted directory (recursive): {p}"
            else:
                p.rmdir()  # only works if empty
                return f"Deleted empty directory: {p}"
    except OSError as e:
        if "not empty" in str(e).lower():
            return f"Directory is not empty: {p}. Use recursive=true to delete it and all contents."
        return f"Error: {e}"


def create_directory(path: str) -> str:
    p, err = _safe_path(path)
    if err:
        return err

    if p.exists():
        return f"Already exists: {p} ({'directory' if p.is_dir() else 'file'})"

    try:
        p.mkdir(parents=True, exist_ok=True)
        return f"Created directory: {p}"
    except OSError as e:
        return f"Error: {e}"


def copy_file(src: str, dest: str, recursive: bool = False) -> str:
    src_p, err = _safe_path(src)
    if err:
        return err
    dest_p, err = _safe_path(dest)
    if err:
        return err

    if not src_p.exists():
        return f"Source not found: {src_p}"

    # If dest is an existing directory, copy inside it
    if dest_p.is_dir():
        dest_p = dest_p / src_p.name

    if dest_p.exists():
        return f"Destination already exists: {dest_p}. Choose a different name."

    try:
        if src_p.is_dir():
            if not recursive:
                return f"{src_p} is a directory. Set recursive=true to copy it."
            shutil.copytree(src_p, dest_p)
            return f"Copied directory: {src_p} → {dest_p}"
        else:
            shutil.copy2(src_p, dest_p)
            return f"Copied: {src_p} → {dest_p}"
    except OSError as e:
        return f"Error: {e}"
