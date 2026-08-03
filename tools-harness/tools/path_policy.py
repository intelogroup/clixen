"""
Path-based security boundary: is a given filesystem path allowed for read/write?
Split out of security_utils.py (which conflated this with tool-name/command
policy in one file, called inconsistently from different places) — this module
is the one place every fs/shell tool's path argument gets checked. See
tool_policy.py for the separate, unrelated concern of which tool *names*/CLI
binaries are allowed to run at all.
"""
import os
import re
import tempfile
from pathlib import Path

# ponytail: derived from file location, not a hardcoded repo name — the repo
# has already been renamed once (gemma4llama -> clixen) and broke this check.
WORKSPACE_ROOT = Path(__file__).resolve().parents[2]
# 2026-07-14: writes were fenced to WORKSPACE_ROOT only, so any local-agent
# task touching a sibling project (e.g. "update todo.md in ~/Developer/brainex")
# got rejected 3x across every path the model tried, burned its retry budget,
# and reported the misleading "I don't have file tools at all" instead of the
# real reason. DEV_ROOT widens writes to the whole dev-projects parent dir —
# still excludes the rest of the filesystem (Documents, ~/.ssh, etc. stay
# write-denied) and BLOCKED_PATTERNS still applies underneath it.
DEV_ROOT = Path(__file__).resolve().parents[3]
APP_DATA_DIR = (Path.home() / ".gemini/antigravity").resolve()
HOME = Path.home().resolve()
TMP_DIRS = [
    Path("/tmp").resolve(),
    Path("/var/tmp").resolve(),
    Path(tempfile.gettempdir()).resolve(),
]

# Explicitly blocked directory/file patterns
BLOCKED_PATTERNS = [
    r"\.ssh\b",
    r"\.aws\b",
    r"\.gitconfig\b",
    r"\.bash_history\b",
    r"\.zsh_history\b",
    r"\.bashrc\b",
    r"\.zshrc\b",
    r"\.zshenv\b",
    r"\.zprofile\b",
    r"\.profile\b",
    r"\.bash_profile\b",
    r"\.netrc\b",
    r"/etc/passwd\b",
    r"/etc/shadow\b",
    r"/etc/hosts\b",
    r"\.env\b",
    r"/Cookies\b",
    r"/Login Data\b",
    r"Library/Application Support/(Google/Chrome|BraveSoftware|Microsoft Edge|Firefox)\b",
    r"Library/Cookies\b",
    r"Library/Safari\b",
]


def is_safe_path(path_str: str, write: bool = False) -> bool:
    try:
        p = Path(path_str).expanduser().resolve()
    except Exception:
        try:
            p = Path(os.path.abspath(os.path.expanduser(path_str)))
        except Exception:
            return False

    p_str = str(p)

    # Check explicitly blocked patterns
    for pattern in BLOCKED_PATTERNS:
        if re.search(pattern, p_str, re.IGNORECASE):
            return False

    # Always allow if under workspace root
    if p_str == str(WORKSPACE_ROOT) or p_str.startswith(str(WORKSPACE_ROOT) + "/"):
        return True

    # Always allow if under app data directory
    if p_str == str(APP_DATA_DIR) or p_str.startswith(str(APP_DATA_DIR) + "/"):
        return True

    # Allow tmp dirs
    for tmp in TMP_DIRS:
        if p_str == str(tmp) or p_str.startswith(str(tmp) + "/"):
            return True

    # For write operations outside workspace/app-data/tmp, allow the rest of the
    # dev-projects directory (sibling repos) plus Downloads; everything else stays denied.
    if write:
        if p_str == str(DEV_ROOT) or p_str.startswith(str(DEV_ROOT) + "/"):
            return True
        downloads = HOME / "Downloads"
        if p_str == str(downloads) or p_str.startswith(str(downloads) + "/"):
            return True
        return False

    # For read operations, allow home directory but block hidden files/folders (except .config/g4l or .clixen)
    if p_str == str(HOME) or p_str.startswith(str(HOME) + "/"):
        try:
            rel = p.relative_to(HOME)
            parts = rel.parts
            if parts:
                first_part = parts[0]
                if first_part.startswith(".") and first_part not in (".config", ".clixen", ".gemini", ".git"):
                    return False
        except Exception:
            return False
        return True

    return False


def validate_path(path_str: str, write: bool = False) -> str:
    try:
        p = Path(path_str).expanduser().resolve()
        resolved_str = str(p)
    except Exception:
        resolved_str = os.path.abspath(os.path.expanduser(path_str))

    if not is_safe_path(resolved_str, write=write):
        op = "Write" if write else "Read"
        raise PermissionError(f"{op} access denied to path: {path_str} (resolved: {resolved_str})")
    return resolved_str
