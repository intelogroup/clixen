"""
Agent task worker — single poll loop for all background work.

Two dispatch paths:
1. Immediate task queue (job_queue) — fire-and-forget tasks enqueued by the UI or LLM
2. Scheduled workflow instances (workflow_store) — persistent automations with cron/interval scheduling

Usage:
    cd tools-harness
    python -m jobs.worker [--poll-interval 30]
"""
from __future__ import annotations

import argparse
import json
import sqlite3
import sys
import time
from datetime import datetime
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from dotenv import load_dotenv
load_dotenv(Path(__file__).parent.parent / ".env")

from clients.router import TASK_ROUTING
from jobs import arbiter, job_queue
from store import dbclose, event_log, workflow_store

from log_config import CURRENT_RUN_ID, setup_logging

_log = setup_logging("task_worker", log_file="task_worker.log")

_TASK_MODULES: dict[str, str] = {
    "agent_message": "jobs.agent_message_job",
    "morning_briefing": "jobs.morning_briefing_job",
    "tech_brief": "jobs.tech_brief_job",
}

# Task-specific timeouts (in seconds)
_TASK_TIMEOUTS: dict[str, int] = {
    "agent_message": 120,          # 120 seconds for orchestrator to process
    "morning_briefing": 120,       # 120 seconds for briefing
    "tech_brief": 120,             # 120 seconds for tech brief
}


# ── Phase 1: handler registry setup ─────────────────────────────────────────

def _setup_handler_registry() -> None:
    from jobs import handler_registry
    from jobs.handlers import (
        briefing_morning,
        daily_digest,
        email_attachment_archive,
        email_attachment_watch,
        email_daily_summary,
        email_watch_sender,
        golden_queries,
        health_check,
        inbox_monitor_attachments,
        memory_verify,
        reddit_intel,
        science_scout,
        study_usmle_daily,
        subagent_audit,
        tech_brief,
        user_automation,
        world_monitor,
    )
    handler_registry.register("briefing.morning", briefing_morning.handle)
    handler_registry.register("daily_digest", daily_digest.handle)
    handler_registry.register("email.attachment_archive", email_attachment_archive.handle)
    handler_registry.register("email.attachment_watch", email_attachment_watch.handle)
    handler_registry.register("email.daily_summary", email_daily_summary.handle)
    handler_registry.register("email.watch_sender", email_watch_sender.handle)
    handler_registry.register("golden.queries", golden_queries.handle)
    handler_registry.register("health_check", health_check.handle)
    handler_registry.register("inbox.monitor_attachments", inbox_monitor_attachments.handle)
    handler_registry.register("memory_verify", memory_verify.handle)
    handler_registry.register("reddit_intel", reddit_intel.handle)
    handler_registry.register("science_scout", science_scout.handle)
    handler_registry.register("study.usmle_daily", study_usmle_daily.handle)
    handler_registry.register("subagent_audit", subagent_audit.handle)
    handler_registry.register("tech_brief", tech_brief.handle)
    handler_registry.register("user.automation", user_automation.handle)
    handler_registry.register("world_monitor", world_monitor.handle)
    _log.info("registered %d handler(s)", len(handler_registry.registered_ids()))

    


# ── Timeout handler for task execution ──────────────────────────────────────

_EPOCH = datetime(1970, 1, 1)


# ── Pre-flight: health check before running any work ────────────────────────

_HEALTH_DB_PATH = Path(__file__).parent / "health.db"


def _check_health(max_age_seconds: int = 360) -> bool:
    """Check if health_writer has pinged within max_age_seconds.

    Returns True if healthy, False if stale or missing (caller should
    still run — don't block work on health, just report).
    """
    import sqlite3
    from datetime import datetime, timezone
    try:
        if not _HEALTH_DB_PATH.exists():
            _log.warning("health.db missing — health_writer may not be running")
            return False
        conn = dbclose.connect(str(_HEALTH_DB_PATH))
        try:
            conn.execute("PRAGMA journal_mode=WAL")
            row = conn.execute("SELECT last_ping FROM health WHERE id = 1").fetchone()
        finally:
            conn.close()
        if row is None:
            return False
        ping = datetime.fromisoformat(row[0])
        age = (datetime.now(timezone.utc) - ping).total_seconds()
        return age < max_age_seconds
    except Exception as exc:
        _log.error("health check failed: %s", exc)
        return False


def _send_alert(message: str) -> None:
    """Send Telegram alert for system-level issues."""
    try:
        from tools.telegram_send import send_telegram
        result = send_telegram(message)
        if result.startswith("[telegram error]"):
            _log.warning("alert send failed: %s", result)
        else:
            _log.info("alert sent: %.80s", result)
    except Exception as exc:
        _log.warning("failed to send alert: %s", exc)
        _log.exception("alert traceback")


# ── Phase 2: job_queue dispatch (immediate tasks) ───────────────────────────

def _execute_job(job: dict) -> None:
    task_name = job["task_name"]
    job_id = job["id"]
    params = json.loads(job["params_json"])

    if task_name not in TASK_ROUTING:
        job_queue.checkpoint(job_id, "failed")
        job_queue.mark_failed(job_id, f"Unknown task: {task_name!r}")
        event_log.record_event("job", "failed", {"job_id": job_id, "task_name": task_name, "error": f"Unknown task: {task_name!r}"})
        _log.error("job %s: unknown task %r, marked failed (not retried)", job_id[:8], task_name)
        return

    _log.info("executing job %s task=%s", job_id[:8], task_name)
    job_queue.checkpoint(job_id, "started")
    event_log.record_event("job", "started", {"job_id": job_id, "task_name": task_name})

    try:
        if task_name in _TASK_MODULES:
            timeout_duration = _TASK_TIMEOUTS.get(task_name, 90)
            _log.info("running task %s with %ds timeout", task_name, timeout_duration)

            ex = ThreadPoolExecutor(max_workers=1)
            module_path = _TASK_MODULES[task_name]
            parts = module_path.rsplit(".", 1)
            mod = __import__(module_path, fromlist=[parts[-1]])
            fut = ex.submit(mod.run_as_job, params, job_id)
            try:
                fut.result(timeout=timeout_duration)
            except TimeoutError:
                _log.error("job %s timed out after %d seconds", job_id[:8], timeout_duration)
                job_queue.checkpoint(job_id, "failed")
                job_queue.mark_failed(job_id, f"Task timeout after {timeout_duration}s")
                event_log.record_event("job", "failed", {
                    "job_id": job_id, "task_name": task_name,
                    "error": f"timeout after {timeout_duration}s",
                })
                return
            finally:
                ex.shutdown(wait=False)

        else:
            import harness
            import httpx as _httpx

            config = TASK_ROUTING[task_name]
            model = config["model"]
            fallback = config.get("fallback_model")

            # Quick probe: is the primary model responsive?
            if fallback:
                try:
                    _httpx.post(
                        "http://127.0.0.1:11434/api/generate",
                        json={"model": model, "prompt": ".", "stream": False,
                              "options": {"num_predict": 1, "num_ctx": 2048}},
                        timeout=3.0,
                    )
                except _httpx.TimeoutException:
                    _log.info("fallback %s -> %s for %s (primary busy)", model, fallback, task_name)
                    model = fallback

            query = _build_query(task_name, params)
            harness.run(
                query=query,
                model=model,
                tools=config["tools"],
                max_rounds=config["max_rounds"],
                round_timeout=60,
            )

        job_queue.mark_done(job_id)
        event_log.record_event("job", "done", {"job_id": job_id, "task_name": task_name})
        _log.info("job %s done", job_id[:8])

    except Exception as e:
        _log.exception("job %s failed with exception: %s", job_id[:8], str(e))
        job_queue.checkpoint(job_id, "failed")
        job_queue.mark_failed(job_id, str(e))
        event_log.record_event("job", "failed", {"job_id": job_id, "task_name": task_name, "error": str(e)})


def _build_query(task_name: str, params: dict) -> str:
    if task_name == "doc_summarizer":
        path = params.get("file_path", "")
        return f"Summarize the document at {path} and send the summary to Telegram."
    if task_name == "price_tracker":
        item = params.get("item", "")
        source = params.get("source", "")
        return f"Track the price of {item!r} on {source} and alert me to Telegram if it drops."
    if task_name == "image_upscale":
        path = params.get("input_path", "")
        return f"Upscale the image at {path} to 2x resolution and save it."
    return f"Execute task {task_name!r} with params {params}"


# ── Phase 3: workflow dispatch (scheduled/persistent tasks) ──────────────────

def _notify_workflow_failure(instance: dict, error: str) -> None:
    task_name = instance.get("task_name") or instance.get("automation_id", "workflow")
    automation_id = instance.get("automation_id", "")
    message = f"{task_name} failed: {error}"
    workflow_store.add_notification(
        level="error",
        message=message,
        source=f"workflow.{automation_id}",
    )
    arbiter.enqueue(f"workflow.{automation_id}", message)


def _execute_workflow_action(instance: dict) -> None:
    workflow_id = instance["id"]
    token = CURRENT_RUN_ID.set(workflow_id[:8])
    try:
        _dispatch_workflow(instance)
    finally:
        CURRENT_RUN_ID.reset(token)


def _dispatch_workflow(instance: dict) -> None:
    """Dispatch a due workflow instance to its registered automation handler."""
    from jobs import handler_registry

    workflow_id = instance["id"]
    automation_id = instance.get("automation_id", "")
    _log.info("executing workflow %s automation=%s", workflow_id[:8], automation_id)
    event_log.record_event("workflow", "started", {"workflow_id": workflow_id, "automation_id": automation_id})

    try:
        result = handler_registry.dispatch(instance)
        workflow_store.update_workflow_instance(workflow_id, last_result=result)
        workflow_store.append_run_history(workflow_id, result if isinstance(result, dict) else {"result": result})
        if isinstance(result, dict) and result.get("success") is False:
            _notify_workflow_failure(instance, result.get("error", "unknown error"))
            event_log.record_event("workflow", "failed", {"workflow_id": workflow_id, "automation_id": automation_id, "error": result.get("error", "unknown error")})
        else:
            event_log.record_event("workflow", "done", {"workflow_id": workflow_id, "automation_id": automation_id})
            msg = result.get("telegram_message") if isinstance(result, dict) else None
            if msg:
                arbiter.enqueue(f"workflow.{automation_id}", msg)
        _log.info("workflow %s automation=%s done", workflow_id[:8], automation_id)
    except KeyError as e:
        _log.error("workflow %s: %s", workflow_id[:8], e)
        _err_result = {"success": False, "error": str(e)}
        workflow_store.update_workflow_instance(workflow_id, last_result=_err_result)
        workflow_store.append_run_history(workflow_id, _err_result)
        _notify_workflow_failure(instance, str(e))
        event_log.record_event("workflow", "failed", {"workflow_id": workflow_id, "automation_id": automation_id, "error": str(e)})
    except Exception as e:
        _log.exception("workflow %s automation=%s failed: %s", workflow_id[:8], automation_id, str(e))
        _err_result = {"success": False, "error": str(e)}
        workflow_store.update_workflow_instance(workflow_id, last_result=_err_result)
        workflow_store.append_run_history(workflow_id, _err_result)
        _notify_workflow_failure(instance, str(e))
        event_log.record_event("workflow", "failed", {"workflow_id": workflow_id, "automation_id": automation_id, "error": str(e)})


def _expire_stale_approvals() -> None:
    """Auto-expire pending approvals past their expires_at."""
    from datetime import datetime, timezone
    now = datetime.now(timezone.utc).isoformat()
    for req in workflow_store.list_approval_requests(status="pending"):
        expires = req.get("expires_at")
        if expires and expires < now:
            workflow_store.resolve_approval_request(req["id"], "expired")
            _log.info("expired stale approval %s (kind=%s)", req["id"][:12], req["kind"])


def _poll_workflow_store() -> None:
    """Claim and dispatch all currently-due workflow instances, once."""
    for instance in workflow_store.claim_due_workflows(limit=10):
        try:
            _execute_workflow_action(instance)
        except Exception as e:
            _log.exception("unexpected error executing workflow %s: %s",
                           instance["id"][:8], str(e))


# ── Main event loop ───────────────────────────────────────────────────────────

def main() -> None:
    parser = argparse.ArgumentParser(description="Agent task worker")
    parser.add_argument("--poll-interval", type=int, default=10, help="Poll interval in seconds")
    args = parser.parse_args()

    _setup_handler_registry()
    _log.info("task worker started (poll_interval=%d seconds)", args.poll_interval)

    try:
        while True:
            try:
                # Pre-flight: check swarm health before running work
                if not _check_health(max_age_seconds=360):
                    _log.warning("health check stale or missing — system may be degraded")
                    arbiter.enqueue("system.health", "health check stale — health_writer may be down")

                # Poll job_queue — claim_next() claims one job at a time (no
                # batch/limit param), so loop up to 5 claims per cycle to match
                # the original batch size.
                claimed = []
                for _ in range(5):
                    job = job_queue.claim_next()
                    if job is None:
                        break
                    claimed.append(job)

                # _TASK_MODULES jobs get a per-task timeout via fut.result(timeout=)
                # with shutdown(wait=False); they run sequentially to keep the
                # number of in-flight module runs predictable. Everything else
                # (the harness.run() branch) is I/O-bound (LLM/network calls),
                # so it's safe to run concurrently.
                sequential_jobs = [j for j in claimed if j["task_name"] in _TASK_MODULES]
                concurrent_jobs = [j for j in claimed if j["task_name"] not in _TASK_MODULES]

                for job in sequential_jobs:
                    try:
                        _execute_job(job)
                    except Exception as e:
                        _log.exception("unexpected error executing job %s: %s", job["id"][:8], str(e))

                if concurrent_jobs:
                    # ponytail: fixed pool of 3 — I/O-bound jobs (LLM + network calls),
                    # keep it under OLLAMA_MAX_LOADED_MODELS=2 pressure and cloud rate
                    # limits rather than firing all of them at once.
                    with ThreadPoolExecutor(max_workers=3) as pool:
                        futures = {pool.submit(_execute_job, job): job for job in concurrent_jobs}
                        for future in as_completed(futures):
                            job = futures[future]
                            try:
                                future.result(timeout=300)
                            except TimeoutError:
                                _log.error("job %s timed out after 300s", job["id"][:8])
                                job_queue.mark_failed(job["id"], "concurrent timeout after 300s")
                            except Exception as e:
                                _log.exception("unexpected error executing job %s: %s", job["id"][:8], str(e))

                # Expire stale approval requests
                _expire_stale_approvals()

                # Poll workflow_store for scheduled/due automations
                _poll_workflow_store()

                # Flush arbiter batch (dedup + digest) after all work this cycle
                for msg in arbiter.flush():
                    _send_alert(msg)

                time.sleep(args.poll_interval)
            except Exception:
                _log.exception("worker cycle failed, continuing")

    except KeyboardInterrupt:
        _log.info("task worker interrupted")
        sys.exit(0)


if __name__ == "__main__":
    main()
