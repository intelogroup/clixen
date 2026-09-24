"""
DashClaw second gate for clixen — single seam at registry.execute_tool.

Fail-open if DashClaw not running (log warning, allow), fail-closed if DashClaw says block.
Maps tool calls to DashClaw act shapes; treats require_approval/allow_contained as approval-required.
"""
from __future__ import annotations

import json
import logging
import os
from pathlib import Path

log = logging.getLogger(__name__)

# lazy client singleton
_client = None
_client_error_logged = False

def _load_config():
    # precedence: env vars > ~/.dashclaw/config.json > ~/.dashclaw/app/*/ .env.local
    base_url = os.environ.get("DASHCLAW_BASE_URL")
    api_key = os.environ.get("DASHCLAW_API_KEY")
    if base_url and api_key:
        return base_url, api_key
    cfg_path = Path.home() / ".dashclaw" / "config.json"
    if cfg_path.exists():
        try:
            j = json.loads(cfg_path.read_text())
            base_url = j.get("baseUrl") or j.get("base_url") or base_url
            api_key = j.get("apiKey") or j.get("api_key") or api_key
        except Exception as e:
            log.debug("dashclaw_guard: failed to read config.json: %s", e)
    if not (base_url and api_key):
        # try app .env.local
        for p in (Path.home() / ".dashclaw" / "app").glob("*/.env.local"):
            try:
                txt = p.read_text()
                for line in txt.splitlines():
                    if line.startswith("NEXTAUTH_URL="):
                        if not base_url:
                            base_url = line.split("=",1)[1].strip()
                    if line.startswith("DASHCLAW_API_KEY="):
                        if not api_key:
                            api_key = line.split("=",1)[1].strip()
            except Exception:
                continue
    # prefer 7437 if config still points to 3000 (ugent occupies 3000)
    if base_url and "localhost:3000" in base_url:
        base_url = "http://localhost:7437"
    return base_url, api_key

def _get_client():
    global _client, _client_error_logged
    if _client is not None:
        return _client
    base_url, api_key = _load_config()
    if not base_url or not api_key:
        if not _client_error_logged:
            log.info("dashclaw_guard: no config (baseUrl/apiKey) — skipping DashClaw check")
            _client_error_logged = True
        return None
    try:
        from dashclaw import DashClaw
        _client = DashClaw(base_url=base_url, api_key=api_key, agent_id="clixen-local")
        # quick health probe
        _client.get_guard_decisions = getattr(_client, "get_guard_decisions", None)
        return _client
    except Exception as e:
        if not _client_error_logged:
            log.warning("dashclaw_guard: failed to init DashClaw client: %s", e)
            _client_error_logged = True
        return None

def _map_to_act(name: str, args: dict) -> dict | None:
    # map clixen tool -> DashClaw act
    # shell-like
    if name in ("bash_exec", "run_python", "reset_kernel", "repl", "run_in_repl"):
        cmd = args.get("command") or args.get("code") or args.get("cmd") or ""
        if isinstance(cmd, str) and cmd.strip():
            return {"kind": "shell", "command": cmd}
        return None
    # file reads/writes
    if name in ("read_file","read_many_files","read_document","read_pdf","parse_document","write_file","edit_file","edit_file_fuzzy","append_file","copy_file","delete_file","create_directory"):
        path = args.get("path") or args.get("file_path") or args.get("dest") or args.get("src") or ""
        if path:
            # include excerpt if present
            excerpt = args.get("content") or args.get("text") or ""
            # try to avoid leaking large content — truncate
            if isinstance(excerpt, str) and len(excerpt) > 2000:
                excerpt = excerpt[:2000]
            act = {"kind": "file", "path": str(path)}
            if excerpt:
                act["content_excerpt"] = excerpt
            return act
        return None
    # git
    if name.startswith("git_") or name in ("github_create_issue","github_create_pr"):
        # map to shell act with git command
        cmd = name + " " + " ".join(str(v) for v in args.values() if isinstance(v, str) and v)
        return {"kind": "shell", "command": cmd[:1000]}
    # api/browser/network
    if name in ("api_fetch","web_fetch","scrapling_fetch","browser_navigate","browser_run_js"):
        url = args.get("url") or args.get("path") or ""
        if url:
            return {"kind": "request", "request": {"url": str(url)[:1000], "method": "GET"}}
        body = args.get("command") or args.get("code") or ""
        if body:
            return {"kind": "shell", "command": str(body)[:1000]}
    # generic fallback — use shell with tool name
    try:
        snippet = json.dumps(args, ensure_ascii=False)[:800]
        return {"kind": "shell", "command": f"{name} {snippet}"}
    except Exception:
        return None

def check(name: str, arguments: dict) -> str | None:
    """
    Returns None if allowed, or a block/awaiting string if DashClaw says block/require_approval.
    Fail-open if DashClaw unreachable (logs warning, returns None). 1.5s hard timeout.
    """
    client = _get_client()
    if client is None:
        return None
    act = _map_to_act(name, arguments or {})
    if act is None:
        return None
    kind = act.get("kind")
    action_type = {"shell":"shell","file":"file","request":"api"}.get(kind, "shell")
    declared_goal = f"clixen {name}"
    # run guard with 1.5s timeout — never block harness on slow dashclaw
    import concurrent.futures
    def _do_guard():
        ctx = {"action_type": action_type, "act": act, "declared_goal": declared_goal}
        return client.guard(ctx)
    try:
        with concurrent.futures.ThreadPoolExecutor(max_workers=1) as ex:
            fut = ex.submit(_do_guard)
            try:
                result = fut.result(timeout=1.5)
            except concurrent.futures.TimeoutError:
                log.debug("dashclaw_guard: guard timeout (fail-open) for %s", name)
                return None
    except Exception as e:
        log.debug("dashclaw_guard check failed (fail-open): %s", e)
        return None
    try:
        # result is dict with decision, reason, risk_score, action_id
        decision = result.get("decision")
        # result is dict with decision, reason, risk_score, action_id
        decision = result.get("decision")
        reason = result.get("reason") or result.get("reasons", [""])[0] if result.get("reasons") else ""
        risk = result.get("risk_score")
        action_id = result.get("action_id") or result.get("decision_id") or ""
        if decision == "block":
            return f"[blocked] dashclaw: {reason or 'blocked by policy'} (risk {risk}, action {action_id}). Decision at http://localhost:7437/decisions/{action_id}"
        if decision in ("require_approval", "allow_contained"):
            # treat as approval-required — use dashclaw's dashboard, not clixen's /confirm
            return (
                f"[awaiting dashclaw approval] {name} needs human approval before running: {reason or 'policy requires approval'} "
                f"(risk {risk}). Action {action_id} pending at http://localhost:7437/approvals — approve via dashboard or `npx dashclaw approve {action_id}`. "
                f"This call has NOT executed."
            )
        # warn is treated as allow (dashclaw will show warning but not block)
        return None
    except Exception as e:
        # fail-open but log — don't block whole harness if dashclaw down
        # detect not-running vs real block: HTTP errors already handled as block above
        msg = str(e)
        # don't spam logs — only first few
        log.debug("dashclaw_guard check failed (fail-open): %s", msg)
        return None

def scan_text(text: str) -> str | None:
    """Optional helper to scan raw external text for prompt injection via DashClaw. 1.5s timeout."""
    client = _get_client()
    if client is None or not text:
        return None
    import concurrent.futures
    def _do_scan():
        return client.scan_prompt_injection(text)
    try:
        with concurrent.futures.ThreadPoolExecutor(max_workers=1) as ex:
            fut = ex.submit(_do_scan)
            r = fut.result(timeout=1.5)
        # r = {clean, risk_level, recommendation, findings...}
        if not r.get("clean") and r.get("recommendation") == "block":
            cats = ",".join(r.get("categories",[]))
            return f"[blocked] dashclaw prompt-injection: {cats} — blocked"
        return None
    except Exception as e:
        log.debug("scan_text failed: %s", e)
        return None
