"""
Git tools — structured git operations for agentic coding.

Tools:
  git_status       — working tree status + staged/unstaged summary
  git_diff         — show unstaged or staged diff
  git_log          — recent commits (oneline)
  git_add          — stage files
  git_commit       — commit staged changes with message
  git_checkout     — switch branch or create new one
  git_new_worktree — create an isolated worktree for safe experimentation
"""

import subprocess
from pathlib import Path

_HOME = str(Path.home())


def _git(args: list[str], cwd: str, timeout: int = 30) -> str:
    try:
        r = subprocess.run(
            ["git"] + args,
            capture_output=True, text=True,
            cwd=str(Path(cwd).expanduser()),
            timeout=timeout,
        )
        out = r.stdout.strip()
        err = r.stderr.strip()
        parts = []
        if out:
            parts.append(out)
        if err:
            parts.append(f"[stderr] {err}")
        if r.returncode != 0 and not parts:
            parts.append(f"[exit {r.returncode}]")
        return "\n".join(parts) or "[no output]"
    except subprocess.TimeoutExpired:
        return f"[error] git timed out after {timeout}s"
    except FileNotFoundError:
        return "[error] git not found in PATH"
    except Exception as e:
        return f"[error] {e}"


# ---------------------------------------------------------------------------
# Schemas
# ---------------------------------------------------------------------------

GIT_STATUS_SCHEMA = {
    "type": "function",
    "function": {
        "name": "git_status",
        "description": (
            "Show the current git working tree status — staged, unstaged, "
            "untracked files, and current branch."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "path": {
                    "type": "string",
                    "description": "Path to the git repository (default: home dir)",
                    "default": _HOME,
                }
            },
            "required": [],
        },
    },
}

GIT_DIFF_SCHEMA = {
    "type": "function",
    "function": {
        "name": "git_diff",
        "description": "Show the git diff for unstaged changes, or staged changes if staged=true.",
        "parameters": {
            "type": "object",
            "properties": {
                "path": {
                    "type": "string",
                    "description": "Path to the git repository",
                    "default": _HOME,
                },
                "staged": {
                    "type": "boolean",
                    "description": "Show staged (--cached) diff instead of unstaged",
                    "default": False,
                },
                "file": {
                    "type": "string",
                    "description": "Limit diff to a specific file path (optional)",
                },
            },
            "required": [],
        },
    },
}

GIT_LOG_SCHEMA = {
    "type": "function",
    "function": {
        "name": "git_log",
        "description": "Show recent git commits in one-line format.",
        "parameters": {
            "type": "object",
            "properties": {
                "path": {
                    "type": "string",
                    "description": "Path to the git repository",
                    "default": _HOME,
                },
                "n": {
                    "type": "integer",
                    "description": "Number of commits to show (default 15)",
                    "default": 15,
                },
            },
            "required": [],
        },
    },
}

GIT_ADD_SCHEMA = {
    "type": "function",
    "function": {
        "name": "git_add",
        "description": "Stage files for the next commit. Pass '.' to stage all changes.",
        "parameters": {
            "type": "object",
            "properties": {
                "path": {
                    "type": "string",
                    "description": "Path to the git repository",
                    "default": _HOME,
                },
                "files": {
                    "type": "string",
                    "description": "File(s) to stage, space-separated. '.' stages everything.",
                    "default": ".",
                },
            },
            "required": [],
        },
    },
}

GIT_COMMIT_SCHEMA = {
    "type": "function",
    "function": {
        "name": "git_commit",
        "description": "Commit staged changes with a message.",
        "parameters": {
            "type": "object",
            "properties": {
                "path": {
                    "type": "string",
                    "description": "Path to the git repository",
                    "default": _HOME,
                },
                "message": {
                    "type": "string",
                    "description": "Commit message",
                },
            },
            "required": ["message"],
        },
    },
}

GIT_CHECKOUT_SCHEMA = {
    "type": "function",
    "function": {
        "name": "git_checkout",
        "description": "Switch to a branch, or create and switch to a new branch.",
        "parameters": {
            "type": "object",
            "properties": {
                "path": {
                    "type": "string",
                    "description": "Path to the git repository",
                    "default": _HOME,
                },
                "branch": {
                    "type": "string",
                    "description": "Branch name to switch to or create",
                },
                "create": {
                    "type": "boolean",
                    "description": "Create the branch if it doesn't exist (-b flag)",
                    "default": False,
                },
            },
            "required": ["branch"],
        },
    },
}

GIT_WORKTREE_SCHEMA = {
    "type": "function",
    "function": {
        "name": "git_new_worktree",
        "description": (
            "Create an isolated git worktree — a separate working directory "
            "linked to the same repo. Use this to safely experiment, write code, "
            "or test changes without touching the main working tree. "
            "The worktree gets its own branch automatically."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "repo_path": {
                    "type": "string",
                    "description": "Path to the main git repository",
                },
                "worktree_path": {
                    "type": "string",
                    "description": "Where to create the worktree directory",
                },
                "branch": {
                    "type": "string",
                    "description": "New branch name for the worktree (created automatically)",
                },
            },
            "required": ["repo_path", "worktree_path", "branch"],
        },
    },
}


# ---------------------------------------------------------------------------
# Executors
# ---------------------------------------------------------------------------

def git_status(path: str = _HOME) -> str:
    branch = _git(["rev-parse", "--abbrev-ref", "HEAD"], cwd=path)
    status = _git(["status", "--short"], cwd=path)
    return f"Branch: {branch}\n\n{status or '(clean)'}"


def git_diff(path: str = _HOME, staged: bool = False, file: str = "") -> str:
    args = ["diff"]
    if staged:
        args.append("--cached")
    if file:
        args += ["--", file]
    out = _git(args, cwd=path)
    # Cap large diffs
    if len(out) > 6000:
        out = out[:5800] + f"\n...(truncated, {len(out)} chars total)"
    return out or "(no changes)"


def git_log(path: str = _HOME, n: int = 15) -> str:
    return _git(["log", f"-{min(n, 50)}", "--oneline", "--decorate"], cwd=path)


def git_add(path: str = _HOME, files: str = ".") -> str:
    return _git(["add"] + files.split(), cwd=path)


def git_commit(message: str, path: str = _HOME) -> str:
    return _git(["commit", "-m", message], cwd=path)


def git_checkout(branch: str, path: str = _HOME, create: bool = False) -> str:
    args = ["checkout"]
    if create:
        args.append("-b")
    args.append(branch)
    return _git(args, cwd=path)


def git_new_worktree(repo_path: str, worktree_path: str, branch: str) -> str:
    wt = Path(worktree_path).expanduser()
    if wt.exists():
        return f"[error] Worktree path already exists: {wt}"
    result = _git(
        ["worktree", "add", "-b", branch, str(wt)],
        cwd=repo_path,
    )
    if "Preparing worktree" in result or not result.startswith("[error]"):
        return f"Worktree created at {wt} on branch '{branch}'\n{result}"
    return result
