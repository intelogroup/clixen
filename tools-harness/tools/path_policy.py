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
# 2026-10-25: DEV_ROOT (~/Developer) and a hardcoded ~/.gemini/antigravity were
# write roots here. Both are gone. DEV_ROOT existed because a local-agent task
# touching a sibling repo got rejected; the cost was that a *shipped* desktop app
# could write anywhere under the user's whole dev-projects folder, and the value
# `parents[3]` means something different once the package is inside an .app.
# Clixen is a visual-actions agent: it drives apps on screen and keeps its own
# files in tools/workspace.py. Anything else that needs to write outside the
# paths below has to be granted explicitly:
#     CLIXEN_EXTRA_WRITE_ROOTS=/abs/path:/another  (empty by default)
# 2026-08-04: canonical clixen-owned data home. NOT ~/Documents/~/Desktop —
# those are iCloud-synced by default and TCC grants for them are all-or-nothing
# (see THREAT_MODEL.md). Application Support is the conventional app-data
# location and is not synced. Vault/index/conversation data should live here.
# CLIXEN_HOME overrides it deliberately — NOT CLIXEN_DATA_DIR, which the desktop
# app uses for a different thing (the mounted vault's data folder).
CLIXEN_DATA_DIR = Path(
    os.environ.get("CLIXEN_HOME") or (Path.home() / "Library/Application Support/Clixen")
).expanduser().resolve()
# Removable volumes (USB) are TCC-guarded locations on macOS (apps need consent
# to access them) — legitimately private for clixen data. Allow them, but they
# are presence-dependent: a USB drive may not always be mounted.
REMOVABLE_VOLUMES_ROOT = Path("/Volumes").resolve()
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


def _extra_write_roots() -> list[Path]:
    """Explicitly granted write roots: CLIXEN_EXTRA_WRITE_ROOTS=/abs/a:/abs/b.

    Empty by default. The grant is resolved (so a symlink cannot smuggle a wider
    root in) and never inherited from the caller's cwd.
    """
    raw = os.environ.get("CLIXEN_EXTRA_WRITE_ROOTS", "")
    roots = []
    for part in raw.split(":"):
        part = part.strip()
        if not part:
            continue
        try:
            roots.append(Path(part).expanduser().resolve())
        except Exception:
            continue
    return roots


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

    # Clixen data home (Application Support/Clixen) — non-iCloud app data
    if p_str == str(CLIXEN_DATA_DIR) or p_str.startswith(str(CLIXEN_DATA_DIR) + "/"):
        return True

    # Removable volumes (USB) — TCC-guarded, legitimately private. Presence-
    # dependent: caller must handle "not mounted".
    if p_str == str(REMOVABLE_VOLUMES_ROOT) or p_str.startswith(str(REMOVABLE_VOLUMES_ROOT) + "/"):
        return True

    # Allow tmp dirs
    for tmp in TMP_DIRS:
        if p_str == str(tmp) or p_str.startswith(str(tmp) + "/"):
            return True

    # Writes outside workspace/app-data/tmp: Downloads (a place the user expects
    # a saved file to appear) plus any explicitly granted roots. Nothing else —
    # no implicit dev-projects grant, so a shipped app cannot write into a user's
    # Documents, Desktop or source folders by accident.
    if write:
        downloads = HOME / "Downloads"
        if p_str == str(downloads) or p_str.startswith(str(downloads) + "/"):
            return True
        for root in _extra_write_roots():
            if p_str == str(root) or p_str.startswith(str(root) + "/"):
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
