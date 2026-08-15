"""
Shell tools — lets the agent execute commands and write/edit files on this machine.

Four tools:
  bash_exec    — run any shell command, returns stdout + stderr
  write_file   — create or overwrite a file
  edit_file    — find-and-replace patch (like Claude Code's Edit tool)
  append_file  — append content to the end of a file
"""

import subprocess
import os
import contextvars
from pathlib import Path

_HOME = str(Path.home())
_SHELL_WORKSPACE = contextvars.ContextVar("_SHELL_WORKSPACE", default=None)


def set_shell_workspace(path: str | None) -> None:
    _SHELL_WORKSPACE.set(str(Path(path).expanduser().resolve()) if path else None)

# ---------------------------------------------------------------------------
# Schemas
# ---------------------------------------------------------------------------

BASH_EXEC_SCHEMA = {
    "type": "function",
    "function": {
        "name": "bash_exec",
        "description": (
            "Run a shell command on this computer and return its output. "
            "Use for: running scripts, installing packages, git operations, "
            "checking processes, compiling, testing, anything a terminal can do. "
            "Commands run in a bash shell. Timeout is 60 seconds. "
            "Output over 8000 chars is truncated in the reply, but the full output is "
            "saved to a /tmp file whose path is given — use read_file on it to see the rest. "
            "Set sandbox=true to run inside a macOS Seatbelt jail (no network, "
            "filesystem writes confined to cwd/tmp) for untrusted or exploratory "
            "commands — e.g. running code fetched from the web, or a command whose "
            "effect you're not fully sure of. Leave it off for normal dev work "
            "(git, launchctl, brew, anything that needs real host/network access)."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "command": {
                    "type": "string",
                    "description": "The shell command to run",
                },
                "cwd": {
                    "type": "string",
                    "description": "Working directory (default: home directory)",
                    "default": _HOME,
                },
                "timeout": {
                    "type": "integer",
                    "description": "Timeout in seconds (default 60, max 300)",
                    "default": 60,
                },
                "sandbox": {
                    "type": "boolean",
                    "description": (
                        "Run inside a Seatbelt sandbox: no network access, writes "
                        "confined to cwd + /tmp. Default false."
                    ),
                    "default": False,
                },
            },
            "required": ["command"],
        },
    },
}

# ponytail: macOS-only (sandbox-exec is a Darwin syscall wrapper, no Linux equivalent
# here). Native Seatbelt over Docker — zero image pull, near-zero cold-start vs a
# container, no extra dependency. Add a Linux path (bwrap/firejail) if this harness
# ever needs to run off-Mac.
_SEATBELT_PROFILE_TMPL = """(version 1)
(deny default)
(allow process-fork)
(allow process-exec)
(allow file-read*)
(allow file-write* (subpath "{cwd}"))
(allow file-write* (subpath "/tmp"))
(allow file-write* (subpath "/private/tmp"))
(allow file-write* (subpath "/private/var/folders"))
(allow file-write-data (literal "/dev/null"))
(allow file-write-data (literal "/dev/tty"))
(allow sysctl-read)
(allow mach-lookup)
(allow signal (target self))
"""
# Deliberately no (allow network*) clause — deny-default blocks all outbound/inbound
# network, same intent as Docker's --network=none.


class SandboxUnavailableError(Exception):
    pass


def _sandbox_wrap(command: str, cwd_path: Path) -> list[str]:
    import tempfile
    import platform
    import shutil
    # ponytail: fail closed, never fall through to an unconfined run — a broken/missing
    # sandbox must look like an error, not a quietly-unsandboxed command.
    if platform.system() != "Darwin":
        raise SandboxUnavailableError(f"sandbox=true needs macOS Seatbelt, host is {platform.system()}")
    if not shutil.which("sandbox-exec"):
        raise SandboxUnavailableError("sandbox=true needs sandbox-exec, not found on PATH")
    profile = _SEATBELT_PROFILE_TMPL.format(cwd=str(cwd_path))
    fd, profile_path = tempfile.mkstemp(suffix=".sb", prefix="clixen_sandbox_")
    with os.fdopen(fd, "w") as f:
        f.write(profile)
    wrapped = f'source ~/.zshenv 2>/dev/null; source ~/.zprofile 2>/dev/null; {command}'
    return ["sandbox-exec", "-f", profile_path, "/bin/zsh", "-c", wrapped], profile_path

WRITE_FILE_SCHEMA = {
    "type": "function",
    "function": {
        "name": "write_file",
        "description": (
            "Create a new file or completely overwrite an existing one. "
            "Use for creating scripts, configs, new source files, or any text content. "
            "Parent directories are created automatically."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "path": {
                    "type": "string",
                    "description": "Absolute or ~ path to write to",
                },
                "content": {
                    "type": "string",
                    "description": "Full content to write to the file",
                },
            },
            "required": ["path", "content"],
        },
    },
}

APPEND_FILE_SCHEMA = {
    "type": "function",
    "function": {
        "name": "append_file",
        "description": (
            "Append content to the end of an existing file. "
            "Use when you need to add new sections, entries, or lines at the end "
            "without touching the rest of the file. "
            "Automatically adds a newline separator if the file doesn't end with one."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "path": {
                    "type": "string",
                    "description": "Absolute or ~ path to the file to append to",
                },
                "content": {
                    "type": "string",
                    "description": "Content to append at the end of the file",
                },
            },
            "required": ["path", "content"],
        },
    },
}

DOWNLOAD_URL_SCHEMA = {
    "type": "function",
    "function": {
        "name": "download_url",
        "description": (
            "Download a file from a URL (PDF, image, any binary or text file) to a "
            "path inside the workspace. Use this instead of bash_exec/curl/wget, "
            "which are blocked for security — this is the only way to fetch a URL's "
            "raw content to disk. After downloading, use read_document or read_file "
            "to read it."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "url": {
                    "type": "string",
                    "description": "URL to download",
                },
                "dest_path": {
                    "type": "string",
                    "description": "Absolute or ~ path inside the workspace to save to",
                },
            },
            "required": ["url", "dest_path"],
        },
    },
}

EDIT_FILE_SCHEMA = {
    "type": "function",
    "function": {
        "name": "edit_file",
        "description": (
            "Edit an existing file by replacing an exact string with a new string. "
            "old_str must match the file exactly (including indentation and whitespace). "
            "Use read_file first to see the exact content, then edit. "
            "Fails clearly if old_str is not found or is ambiguous."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "path": {
                    "type": "string",
                    "description": "Path to the file to edit",
                },
                "old_str": {
                    "type": "string",
                    "description": "The exact string to find and replace (must be unique in the file)",
                },
                "new_str": {
                    "type": "string",
                    "description": "The string to replace it with",
                },
            },
            "required": ["path", "old_str", "new_str"],
        },
    },
}

# ---------------------------------------------------------------------------
# Executors
# ---------------------------------------------------------------------------


def bash_exec(command: str, cwd: str | None = None, timeout: int = 60, sandbox: bool = False) -> str:
    from tools.tool_policy import validate_command
    from tools.path_policy import validate_path
    try:
        validate_command(command)
        effective_cwd = cwd or _SHELL_WORKSPACE.get() or _HOME
        validate_path(effective_cwd, write=False)
    except Exception as e:
        return f"[error] Security validation failed: {e}"

    cwd_path = Path(effective_cwd).expanduser().resolve()
    if not cwd_path.exists():
        cwd_path = Path(_HOME)

    timeout = min(int(timeout), 300)

    profile_path = None
    try:
        if sandbox:
            argv, profile_path = _sandbox_wrap(command, cwd_path)
            result = subprocess.run(
                argv,
                capture_output=True,
                text=True,
                cwd=str(cwd_path),
                timeout=timeout,
                env={**os.environ, "HOME": _HOME},
            )
        else:
            # Run as an interactive login shell so PATH, nvm, conda, pyenv etc. are available
            wrapped = f'source ~/.zshenv 2>/dev/null; source ~/.zprofile 2>/dev/null; {command}'
            result = subprocess.run(
                wrapped,
                shell=True,
                executable="/bin/zsh",
                capture_output=True,
                text=True,
                cwd=str(cwd_path),
                timeout=timeout,
                env={**os.environ, "HOME": _HOME},
            )
        stdout = result.stdout.strip()
        stderr = result.stderr.strip()
        exit_code = result.returncode

        parts = []
        if sandbox:
            parts.append("[sandboxed: no network, writes confined to cwd/tmp]")
        if stdout:
            parts.append(stdout)
        if stderr:
            parts.append(f"[stderr]\n{stderr}")
        parts.append(f"[exit code: {exit_code}]")

        output = "\n".join(parts)
        # Cap output to avoid flooding context. Full output isn't discarded — spilled to
        # a tmp file so the model can read_file the rest instead of losing the tail.
        if len(output) > 8000:
            import tempfile
            # dir="/tmp" (not the /var/folders default) — that's what path_policy's
            # TMP_DIRS allowlist covers, so read_file can actually reach the spill file.
            fd, spill_path = tempfile.mkstemp(suffix=".txt", prefix="clixen_bash_output_", dir="/tmp")
            with os.fdopen(fd, "w") as f:
                f.write(output)
            output = (
                output[:7800]
                + f"\n... (truncated, {len(output)} chars total — full output saved to {spill_path}, use read_file to see the rest)"
            )
        return output

    except subprocess.TimeoutExpired:
        return f"[error] Command timed out after {timeout}s"
    except Exception as e:
        return f"[error] {e}"
    finally:
        if profile_path:
            try:
                os.unlink(profile_path)
            except OSError:
                pass


def write_file(path: str, content: str) -> str:
    from tools.path_policy import validate_path
    from tools.fs_observation import check_write, observe
    try:
        path = validate_path(path, write=True)
    except Exception as e:
        return f"[error] Security validation failed: {e}"

    p = Path(path).expanduser().resolve()
    guard_err = check_write(p)
    if guard_err:
        return f"[error] {guard_err}"
    try:
        if p.exists():
            _save_undo_snapshot(p, p.read_text(encoding="utf-8", errors="replace"))
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(content, encoding="utf-8")
        observe(p)
        lines = content.count("\n") + 1
        return f"Written {lines} lines to {p}"
    except PermissionError:
        return f"[error] Permission denied: {p}"
    except Exception as e:
        return f"[error] {e}"


def append_file(path: str, content: str) -> str:
    from tools.path_policy import validate_path
    from tools.fs_observation import check_edit, observe
    try:
        path = validate_path(path, write=True)
    except Exception as e:
        return f"[error] Security validation failed: {e}"

    p = Path(path).expanduser().resolve()
    if not p.exists():
        return f"[error] File not found: {p}"
    guard_err = check_edit(p)
    if guard_err:
        return f"[error] {guard_err}"
    try:
        existing = p.read_text(encoding="utf-8", errors="replace")
        separator = "" if existing.endswith("\n") else "\n"
        _save_undo_snapshot(p, existing)
        p.write_text(existing + separator + content, encoding="utf-8")
        observe(p)
        lines = content.count("\n") + 1
        return f"Appended {lines} line(s) to {p}"
    except PermissionError:
        return f"[error] Permission denied: {p}"
    except Exception as e:
        return f"[error] {e}"


def _is_safe_download_host(url: str) -> bool:
    # ponytail: download_url is the sanctioned curl/wget replacement (those
    # binaries are denylisted in bash_exec) — without a host check it reopens
    # SSRF to internal services (ollama on 127.0.0.1:11434, cloud metadata
    # endpoints, etc). Resolve and reject loopback/private/link-local targets.
    import ipaddress
    import socket
    from urllib.parse import urlparse

    host = urlparse(url).hostname
    if not host:
        return False
    try:
        infos = socket.getaddrinfo(host, None)
    except socket.gaierror:
        return False
    for info in infos:
        ip = ipaddress.ip_address(info[4][0])
        if ip.is_loopback or ip.is_private or ip.is_link_local or ip.is_reserved or ip.is_multicast:
            return False
    return True


def download_url(url: str, dest_path: str) -> str:
    from tools.path_policy import validate_path
    if not url.startswith(("http://", "https://")):
        return f"[error] Unsupported URL scheme: {url}"
    if not _is_safe_download_host(url):
        return f"[error] Download blocked: host not allowed: {url}"
    try:
        dest_path = validate_path(dest_path, write=True)
    except Exception as e:
        return f"[error] Security validation failed: {e}"

    p = Path(dest_path).expanduser().resolve()
    try:
        import requests
        p.parent.mkdir(parents=True, exist_ok=True)
        headers = {
            "User-Agent": (
                "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36"
            ),
        }
        with requests.get(url, headers=headers, timeout=30, stream=True) as r:
            r.raise_for_status()
            with open(p, "wb") as f:
                for chunk in r.iter_content(chunk_size=65536):
                    f.write(chunk)
        size = p.stat().st_size
        return f"Downloaded {size} bytes to {p}"
    except requests.RequestException as e:
        return f"[error] Download failed: {e}"
    except PermissionError:
        return f"[error] Permission denied: {p}"
    except Exception as e:
        return f"[error] {e}"


def edit_file(path: str, old_str: str, new_str: str) -> str:
    from tools.path_policy import validate_path
    from tools.fs_observation import check_edit, observe
    try:
        path = validate_path(path, write=True)
    except Exception as e:
        return f"[error] Security validation failed: {e}"

    p = Path(path).expanduser().resolve()
    if not p.exists():
        return f"[error] File not found: {p}"
    guard_err = check_edit(p)
    if guard_err:
        return f"[error] {guard_err}"
    try:
        content = p.read_text(encoding="utf-8", errors="replace")
    except PermissionError:
        return f"[error] Permission denied: {p}"

    count = content.count(old_str)
    if count == 0:
        # Help the model debug: show a snippet of the file
        preview = content[:500].replace("\n", "↵")
        return (
            f"[error] old_str not found in {p}.\n"
            f"File starts with: {preview}\n"
            "Tip: use read_file to see exact content before editing."
        )
    if count > 1:
        return (
            f"[error] old_str found {count} times in {p} — too ambiguous. "
            "Make old_str longer/more specific so it matches exactly once."
        )

    new_content = content.replace(old_str, new_str, 1)
    _save_undo_snapshot(p, content)
    diff = _generate_diff(content, new_content, str(p))
    p.write_text(new_content, encoding="utf-8")
    observe(p)
    return f"Edited {p} (replaced 1 occurrence).\nDiff:\n{diff}"


# ponytail: single-slot undo (last write per path only), not a full history —
# add a stack if callers ever need to undo more than one step back.
_UNDO_SNAPSHOTS: dict[str, str] = {}


def _save_undo_snapshot(resolved_path: Path, prior_content: str) -> None:
    _UNDO_SNAPSHOTS[str(resolved_path)] = prior_content


UNDO_LAST_EDIT_SCHEMA = {
    "type": "function",
    "function": {
        "name": "undo_last_edit",
        "description": (
            "Revert a file to its content just before the last edit_file/edit_file_fuzzy/write_file "
            "call in this session. Only one step of history is kept per file — calling it twice in a "
            "row for the same file does nothing the second time."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "path": {"type": "string", "description": "Path to the file to revert"},
            },
            "required": ["path"],
        },
    },
}


def undo_last_edit(path: str) -> str:
    from tools.path_policy import validate_path
    try:
        path = validate_path(path, write=True)
    except Exception as e:
        return f"[error] Security validation failed: {e}"

    p = Path(path).expanduser().resolve()
    prior = _UNDO_SNAPSHOTS.pop(str(p), None)
    if prior is None:
        return f"[error] No undo snapshot available for {p} (either never edited this session, or already undone)."
    p.write_text(prior, encoding="utf-8")
    from tools.fs_observation import observe
    observe(p)
    return f"Reverted {p} to its state before the last edit."


def _generate_diff(old: str, new: str, path: str) -> str:
    """Generate a simple unified diff preview string."""
    import difflib
    old_lines = old.splitlines(keepends=True)
    new_lines = new.splitlines(keepends=True)
    diff = list(difflib.unified_diff(old_lines, new_lines, fromfile=path, tofile=path, n=3))
    return "".join(diff)


EDIT_FILE_FUZZY_SCHEMA = {
    "type": "function",
    "function": {
        "name": "edit_file_fuzzy",
        "description": (
            "Edit a file by fuzzy-matching old_str. "
            "Uses difflib.SequenceMatcher to find the closest match even with minor whitespace/formatting differences. "
            "Shows a diff preview before applying. "
            "If the match is too ambiguous (similarity < 0.85), fails with guidance. "
            "Use when edit_file's exact match fails due to subtle whitespace differences."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "path": {
                    "type": "string",
                    "description": "Path to the file to edit",
                },
                "old_str": {
                    "type": "string",
                    "description": "The string to find and replace (fuzzy-matched)",
                },
                "new_str": {
                    "type": "string",
                    "description": "The string to replace it with",
                },
            },
            "required": ["path", "old_str", "new_str"],
        },
    },
}


def edit_file_fuzzy(path: str, old_str: str, new_str: str) -> str:
    from tools.path_policy import validate_path
    from tools.fs_observation import check_edit, observe
    import difflib
    try:
        path = validate_path(path, write=True)
    except Exception as e:
        return f"[error] Security validation failed: {e}"

    p = Path(path).expanduser().resolve()
    if not p.exists():
        return f"[error] File not found: {p}"
    guard_err = check_edit(p)
    if guard_err:
        return f"[error] {guard_err}"
    try:
        content = p.read_text(encoding="utf-8", errors="replace")
    except PermissionError:
        return f"[error] Permission denied: {p}"

    # Exact match first (fast path)
    count = content.count(old_str)
    if count == 1:
        new_content = content.replace(old_str, new_str, 1)
        _save_undo_snapshot(p, content)
        diff = _generate_diff(content, new_content, str(p))
        p.write_text(new_content, encoding="utf-8")
        observe(p)
        return f"Edited {p} (replaced 1 occurrence).\nDiff:\n{diff}"

    if count > 1:
        return (
            f"[error] old_str found {count} times in {p} — too ambiguous. "
            "Make old_str longer/more specific so it matches exactly once."
        )

    # Fuzzy match: find best match in the file
    lines = content.splitlines(keepends=True)
    best_ratio = 0.0
    best_idx = -1
    old_lines = old_str.splitlines(keepends=True)
    old_blob = old_str.strip()

    # Try multi-line match first
    for i in range(len(lines) - len(old_lines) + 1):
        chunk = "".join(lines[i:i + len(old_lines)])
        ratio = difflib.SequenceMatcher(None, chunk.strip(), old_blob).ratio()
        if ratio > best_ratio:
            best_ratio = ratio
            best_idx = i
            if best_idx >= 0 and len(old_lines) == 1:
                break

    # If no multi-line match, try single-line
    if best_ratio < 0.7 and len(old_lines) > 1:
        old_single = old_str.strip()
        for i, line in enumerate(lines):
            ratio = difflib.SequenceMatcher(None, line.strip(), old_single).ratio()
            if ratio > best_ratio:
                best_ratio = ratio
                best_idx = i
                if best_ratio > 0.9:
                    break

    if best_ratio < 0.85 or best_idx < 0:
        near_miss = ""
        if best_idx >= 0:
            matched_lines_guess = len(old_lines) if len(old_lines) > 1 else 1
            candidate = "".join(lines[best_idx:best_idx + matched_lines_guess]).strip()
            candidate_diff = "\n".join(
                difflib.unified_diff(
                    old_blob.splitlines(), candidate.splitlines(),
                    fromfile="old_str", tofile="closest_match_in_file", lineterm="",
                )
            )
            near_miss = f"Closest match (line {best_idx + 1}):\n{candidate_diff}\n"
        return (
            f"[error] Fuzzy match failed. Best match ratio: {best_ratio:.2f} (need >= 0.85).\n"
            f"{near_miss}"
            f"File starts with: {content[:300].replace(chr(10), '↵')}\n"
            "Tip: use read_file to see exact content, then use edit_file with the exact text."
        )

    # Apply replacement at best_idx
    matched_lines = len(old_lines) if len(old_lines) > 1 else 1
    before = "".join(lines[:best_idx])
    after = "".join(lines[best_idx + matched_lines:])
    new_content = before + new_str + ("\n" if not new_str.endswith("\n") else "") + after

    _save_undo_snapshot(p, content)
    diff = _generate_diff(content, new_content, str(p))
    p.write_text(new_content, encoding="utf-8")
    observe(p)
    return (
        f"Fuzzy-edited {p} (matched with similarity {best_ratio:.2f}).\n"
        f"Diff:\n{diff}"
    )
