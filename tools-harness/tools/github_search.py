"""GitHub search via the `gh` CLI — repos or code.

Useful as a web-search alternative for tech/code questions, and reachable in
network environments (e.g. mainland China) where SearXNG/DuckDuckGo/Tavily may
be blocked or unreliable, since it hits GitHub's API directly through an
already-authenticated `gh` CLI session rather than scraping a search engine.
"""
import json
import subprocess

GITHUB_SEARCH_SCHEMA = {
    "type": "function",
    "function": {
        "name": "github_search",
        "description": (
            "Search GitHub repositories or code via the `gh` CLI. Good fallback "
            "for tech/code questions when other web search backends are blocked "
            "or unreachable (e.g. from mainland China) — hits GitHub's API "
            "directly, not a scraped search engine."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "Search query."},
                "kind": {
                    "type": "string",
                    "enum": ["repos", "code"],
                    "description": "Search repositories or code. Default: repos.",
                },
                "language": {
                    "type": "string",
                    "description": "Optional language filter (code search only).",
                },
                "limit": {
                    "type": "integer",
                    "description": "Max results, 1-30. Default 10.",
                },
            },
            "required": ["query"],
        },
    },
}


def github_search_executor(args: dict) -> str:
    query = (args.get("query") or "").strip()
    if not query:
        return "[error] github_search requires a query"

    kind = args.get("kind", "repos")
    if kind not in ("repos", "code"):
        return f"[error] unsupported kind {kind!r} — use 'repos' or 'code'"

    try:
        limit = int(args.get("limit", 10))
    except (TypeError, ValueError):
        limit = 10
    limit = max(1, min(limit, 30))

    cmd = ["gh", "search", kind, query, "--limit", str(limit)]
    if kind == "code":
        lang = args.get("language")
        if lang:
            cmd += ["--language", lang]
        cmd += ["--json", "repository,path,url"]
    else:
        cmd += ["--json", "fullName,description,url,stargazersCount,language"]

    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=20)
    except FileNotFoundError:
        return "[error] gh CLI not installed. Install: brew install gh"
    except subprocess.TimeoutExpired:
        return "[error] gh search timed out after 20s"

    if result.returncode != 0:
        return f"[error] gh search failed: {result.stderr.strip()[:300]}"

    try:
        data = json.loads(result.stdout)
    except json.JSONDecodeError:
        return (result.stdout or "").strip()[:2000] or "[no results]"

    if not data:
        return "No results found."

    lines = []
    if kind == "code":
        for item in data:
            repo = (item.get("repository") or {}).get("nameWithOwner", "?")
            path = item.get("path", "?")
            url = item.get("url", "")
            lines.append(f"- {repo}/{path}\n  {url}")
    else:
        for item in data:
            name = item.get("fullName", "?")
            desc = (item.get("description") or "").strip()
            stars = item.get("stargazersCount", 0)
            lang = item.get("language") or ""
            url = item.get("url", "")
            tail = f", {lang}" if lang else ""
            lines.append(f"- {name} ({stars}★{tail})\n  {desc}\n  {url}")

    return "\n".join(lines)


GITHUB_LIST_PRS_SCHEMA = {
    "type": "function",
    "function": {
        "name": "github_list_prs",
        "description": "List pull requests in a GitHub repo via the `gh` CLI. Read-only.",
        "parameters": {
            "type": "object",
            "properties": {
                "repo": {"type": "string", "description": "owner/repo. Defaults to the repo in the current directory."},
                "state": {"type": "string", "enum": ["open", "closed", "merged", "all"], "description": "Default: open."},
                "limit": {"type": "integer", "description": "Max results, 1-30. Default 10."},
            },
            "required": [],
        },
    },
}

GITHUB_CREATE_ISSUE_SCHEMA = {
    "type": "function",
    "function": {
        "name": "github_create_issue",
        "description": "Create a GitHub issue via the `gh` CLI. Visible to others — requires human confirmation before it fires.",
        "parameters": {
            "type": "object",
            "properties": {
                "title": {"type": "string", "description": "Issue title."},
                "body": {"type": "string", "description": "Issue body/description."},
                "repo": {"type": "string", "description": "owner/repo. Defaults to the repo in the current directory."},
            },
            "required": ["title"],
        },
    },
}

GITHUB_CREATE_PR_SCHEMA = {
    "type": "function",
    "function": {
        "name": "github_create_pr",
        "description": "Create a GitHub pull request from the current branch via the `gh` CLI. Visible to others — requires human confirmation before it fires.",
        "parameters": {
            "type": "object",
            "properties": {
                "title": {"type": "string", "description": "PR title."},
                "body": {"type": "string", "description": "PR body/description."},
                "base": {"type": "string", "description": "Base branch to merge into. Defaults to the repo's default branch."},
                "repo": {"type": "string", "description": "owner/repo. Defaults to the repo in the current directory."},
            },
            "required": ["title"],
        },
    },
}


def _gh_run(cmd: list[str], timeout: int = 20) -> subprocess.CompletedProcess:
    return subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)


def github_list_prs_executor(args: dict) -> str:
    cmd = ["gh", "pr", "list", "--state", args.get("state", "open"),
           "--limit", str(max(1, min(int(args.get("limit", 10) or 10), 30))),
           "--json", "number,title,url,headRefName,state"]
    if args.get("repo"):
        cmd += ["--repo", args["repo"]]
    try:
        result = _gh_run(cmd)
    except FileNotFoundError:
        return "[error] gh CLI not installed. Install: brew install gh"
    except subprocess.TimeoutExpired:
        return "[error] gh pr list timed out after 20s"
    if result.returncode != 0:
        return f"[error] gh pr list failed: {result.stderr.strip()[:300]}"
    try:
        data = json.loads(result.stdout)
    except json.JSONDecodeError:
        return (result.stdout or "").strip()[:2000] or "[no results]"
    if not data:
        return "No pull requests found."
    return "\n".join(
        f"- #{p['number']} {p['title']} ({p['headRefName']}, {p['state']})\n  {p['url']}"
        for p in data
    )


def github_create_issue_executor(args: dict) -> str:
    title = (args.get("title") or "").strip()
    if not title:
        return "[error] github_create_issue requires a title"
    cmd = ["gh", "issue", "create", "--title", title, "--body", args.get("body") or ""]
    if args.get("repo"):
        cmd += ["--repo", args["repo"]]
    try:
        result = _gh_run(cmd, timeout=30)
    except FileNotFoundError:
        return "[error] gh CLI not installed. Install: brew install gh"
    except subprocess.TimeoutExpired:
        return "[error] gh issue create timed out after 30s"
    if result.returncode != 0:
        return f"[error] gh issue create failed: {result.stderr.strip()[:300]}"
    return result.stdout.strip() or "Issue created."


def github_create_pr_executor(args: dict) -> str:
    title = (args.get("title") or "").strip()
    if not title:
        return "[error] github_create_pr requires a title"
    cmd = ["gh", "pr", "create", "--title", title, "--body", args.get("body") or ""]
    if args.get("base"):
        cmd += ["--base", args["base"]]
    if args.get("repo"):
        cmd += ["--repo", args["repo"]]
    try:
        result = _gh_run(cmd, timeout=30)
    except FileNotFoundError:
        return "[error] gh CLI not installed. Install: brew install gh"
    except subprocess.TimeoutExpired:
        return "[error] gh pr create timed out after 30s"
    if result.returncode != 0:
        return f"[error] gh pr create failed: {result.stderr.strip()[:300]}"
    return result.stdout.strip() or "PR created."
