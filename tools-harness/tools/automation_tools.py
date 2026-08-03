"""
Automation management tools — create, list, pause, resume, delete, trigger workflow instances.
These tools let the LLM agent manage persistent background automations via the workflow store.
"""
from __future__ import annotations
import json
import os
import secrets
from store import workflow_store, trace_store


def _pipelines_checked_this_turn(*tool_names: str) -> bool:
    """True if one of `tool_names` already ran earlier in this orchestrator turn.

    2026-07-17: converts the prompt-only "call list_automations/
    list_available_pipelines FIRST" rule into a code-level nudge — a model
    that skips the earlier lookup was previously relying entirely on reading
    and retaining a paragraph among many; this checks the actual trace instead
    of trusting that. No run context (e.g. called outside the orchestrator
    loop) fails open — don't block a caller we can't verify against.
    """
    from log_config import CURRENT_RUN_ID
    run_id = CURRENT_RUN_ID.get()
    if not run_id:
        return True
    trace = trace_store.get_trace(run_id) or []
    called = {e.get("tool") for e in trace}
    return bool(called & set(tool_names))

PUBLIC_BASE_URL = os.environ.get("PUBLIC_BASE_URL", "http://localhost:9234")

# ── Integration Templates ─────────────────────────────────────────────────────

INTEGRATION_TEMPLATES = {
    "github_star_watch": {
        "description": "Watch for new GitHub repo stars, notify on Telegram",
        "trigger_type": "webhook",
        "action_type": "telegram",
        "trigger_config": {"webhook_hmac_algorithm": "sha256"},
        "action_config": {
            "message": "New GitHub star! \u2b50 {sender} starred {repo}",
        },
    },
    "github_issue_watch": {
        "description": "Forward new GitHub issues to Telegram",
        "trigger_type": "webhook",
        "action_type": "telegram",
        "trigger_config": {"webhook_hmac_algorithm": "sha256"},
        "action_config": {
            "message": "New issue: {issue_title} \u2014 {issue_url}",
        },
    },
    "stripe_payment_alert": {
        "description": "Alert on successful Stripe payments",
        "trigger_type": "webhook",
        "action_type": "telegram",
        "trigger_config": {"webhook_hmac_algorithm": "stripe"},
        "action_config": {
            "message": "Payment received: ${amount} from {customer_email}",
        },
    },
    "scheduled_daily_notification": {
        "description": "Daily notification at a set time",
        "trigger_type": "schedule",
        "action_type": "notification",
        "schedule": {"cron": "0 9 * * *"},
        "action_config": {"message": "Daily reminder"},
    },
}

# ── Schemas ──────────────────────────────────────────────────────────────────

CREATE_AUTOMATION_SCHEMA = {
    "type": "function",
    "function": {
        "name": "create_automation",
        "description": (
            "Create a persistent background automation that runs on a schedule or trigger. "
            "Use this when the user wants to watch for emails, get daily briefings, set up "
            "recurring notifications, or any other background task. "
            "Ask for clarifying details (trigger address, interval, action target) before calling. "
            "Prefer this tool over create_workflow for anything matching trigger_type/action_type "
            "below — create_workflow is only for scheduling an already-registered internal handler "
            "pipeline by name, not for building a new email/telegram/webhook automation. "
            "Before calling: check list_automations (all statuses, including paused) for an "
            "existing automation that already covers the request — this tool will flag likely "
            "duplicates in its response as a non-blocking FYI, but that check happens after "
            "creation, so it's still better to look first and prefer resume_automation/"
            "update_automation over creating a near-duplicate."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "name": {
                    "type": "string",
                    "description": "Short human-readable label for this automation, e.g. 'Watch emails from John'",
                },
                "trigger_type": {
                    "type": "string",
                    "enum": ["email", "schedule", "webhook", "google_sheet", "file_change"],
                    "description": (
                        "What triggers the automation. For 'webhook': fires when an external "
                        "service POSTs to a generated URL, not on a schedule — the response "
                        "includes the URL to give that service; also retrievable later via "
                        "get_automation's 'webhook_url' field."
                    ),
                },
                "template": {
                    "type": "string",
                    "description": (
                        "Name of a predefined integration template. When set, pre-fills "
                        "many fields from the template — explicit args override template "
                        "values. Use get_integration_templates to list available templates. "
                        "Templates include: github_star_watch, github_issue_watch, "
                        "stripe_payment_alert, scheduled_daily_notification."
                    ),
                },
                "trigger_config": {
                    "type": "object",
                    "description": (
                        "Trigger-specific config. "
                        "For email: {'senders': ['john@example.com'], 'query': 'is:unread'}. "
                        "For schedule: omit (use schedule param instead). "
                        "For google_sheet: {'spreadsheet_id': '...', 'watch_column': 'A'}."
                    ),
                },
                "action_type": {
                    "type": "string",
                    "enum": ["telegram", "notification", "http_webhook", "email", "tool_call", "imessage", "workflow"],
                    "description": "What action to take when triggered",
                },
                "action_config": {
                    "type": "object",
                    "description": (
                        "Action-specific config. "
                        "For tool_call: {'tool_name': '<a browser-tagged tool, e.g. "
                        "get_my_doordash_orders, get_my_uber_trips, estimate_uber_ride>', 'tool_args': {...} (optional, "
                        "passed through to the tool), 'notify_via': 'telegram'|'notification'|'email' "
                        "(default 'telegram'), 'recipient': 'user@example.com' (required if "
                        "notify_via='email')} — runs the tool and forwards its raw result through "
                        "the chosen delivery channel. Only browser-tagged tools are allowed; anything "
                        "else is rejected before it runs. Use this to check an external-app connector "
                        "(DoorDash orders/cart, Uber trips, etc.) on a schedule or webhook. "
                        "For imessage, either a plain send — {'recipient': '+15551234567', "
                        "'message': 'text'} (recipient optional, falls back to IMESSAGE_DEFAULT_TARGET "
                        "env var) — or a rule-gated watch+auto-reply: {'watch_contact': "
                        "'+15551234567', 'reply_message': 'Yes I can!', 'trigger_phrases': "
                        "['can you help', 'wondering if you can help']} — REQUIRED for watch_contact "
                        "mode (creation is rejected without it): a list of phrases/substrings, "
                        "case-insensitive, ANY of which must appear in a message for it to get any "
                        "reply at all. Ask the user what a real request from this contact actually "
                        "looks like (their exact wording, e.g. \"can you help\") — without this, every "
                        "single new message gets a reply, including cancellations, thank-yous, and "
                        "other noise that happens to share the same date/time/location fields as a "
                        "real request. Plus any of: "
                        "'blackout_days': ['Monday', 'Wednesday'] (skip the ENTIRE message on these "
                        "weekdays, no reply sent at all), 'skip_keywords': ['today', 'tomorrow'] "
                        "(skip a message/assignment segment containing any of these words, case-"
                        "insensitive substring), 'blacklist_locations': ['Burlington', 'Peabody'] "
                        "(skip a segment mentioning any of these, case-insensitive substring), "
                        "'earliest_start_hour': 8 (skip if a HH:MM AM/PM time is found in the segment "
                        "and its hour is before this), 'check_calendar': true + "
                        "'calendar_buffer_minutes': 60 (skip if a parsed time falls within that many "
                        "minutes of an existing Google Calendar event — requires calendar auth; an "
                        "unverifiable check is treated as a conflict, fails safe, does not reply). "
                        "Incoming messages are split into per-assignment segments on blank lines or "
                        "numbered-list markers (e.g. '1. ...', '2. ...') and each segment is evaluated "
                        "and replied to independently — a multi-assignment message can accept some and "
                        "skip others. Everything except trigger_phrases is optional. "
                        "For telegram, either a static ping — {'message': 'New email from {sender}'} — "
                        "or a real deduped watcher: add {'query': '<gmail search query>', "
                        "'senders': ['a@x.com'] (optional, ANDed with query), 'max_results': 10 "
                        "(optional)} and it polls Gmail, tracks seen message IDs, and only sends "
                        "'message' (with {sender}/{subject} filled in) for genuinely new matches — "
                        "use this instead of a static message whenever the request is 'notify me "
                        "when a NEW email ... arrives', not just 'send me a message on a schedule'. "
                        "For email, either a plain send — {'recipient': 'user@example.com', "
                        "'subject': 'Daily brief', 'body': '...'} — or one of two real generators: "
                        "a news digest with {'recipient': ..., 'subject': ..., 'topics': 'AI, robotics' "
                        "(triggers a Tavily/Exa news search), 'days': 2 (optional), 'max_results': 8 "
                        "(optional)}, or a PubMed watch with {'recipient': ..., 'subject': ..., "
                        "'query': '<pubmed search terms ONLY — e.g. \"CRISPR AND gene editing\">', "
                        "'days': 1 (optional, restricts to last N days), 'max_results': 10 (optional)} "
                        "— both dedupe against what was already sent, so only new items go out each "
                        "run. IMPORTANT for the PubMed query: do NOT add a date-range clause "
                        "(no [dp], [pdat], or any {{...}}-style placeholder) inside 'query' yourself — "
                        "there is no template substitution, so a literal '{{date}}'-shaped string gets "
                        "sent to PubMed as-is and matches nothing, silently breaking the automation "
                        "forever (confirmed live 2026-07-11). The 'days' field alone handles date "
                        "filtering server-side — 'query' should be topic/keyword terms only. "
                        "For notification: {'message': 'text to show in the app'}. "
                        "For http_webhook: {'url': 'https://...', 'payload': {...} (optional JSON body, "
                        "always sent as POST)}. "
                        "For workflow (chain multiple actions in one automation — use this instead of "
                        "creating several separate automations when the request is 'do A, then B', "
                        "'check X and if new, notify me', or any multi-step sequence): "
                        "{'steps': [{'name': '<short id, e.g. \"check\">', 'action': "
                        "'tool_call'|'telegram'|'email'|'notification'|'http_webhook'|'imessage'|'approval'|"
                        "'llm_extract', "
                        "'params': {...same shape as that action_type's own action_config above}, "
                        "'failure_mode': 'stop'|'rollback' (optional, default 'stop' — 'rollback' re-raises "
                        "so the whole workflow run fails instead of just skipping the rest)}, ...]}. "
                        "Steps run in order by default; a later step's params can reference an earlier "
                        "step's output with '$stepname.result' (or '$stepname.error' on failure) anywhere "
                        "inside a string value — e.g. step 'check' with action 'tool_call' followed by step "
                        "'alert' with action 'telegram' and params {'message': 'Result: $check.result'}. "
                        "'tool_call' step params are {'tool': '<tool name>', ...any other keys are passed "
                        "as that tool's arguments}. Only '$stepname.field' substitution works — NOT "
                        "'{stepname.field}' or '{{stepname.field}}', neither of those substitute anything. "
                        "A 'tool_call' step targeting a destructive tool (write_file, delete_file, bash_exec, "
                        "git_commit, send_email, etc. — the same set Plan mode blocks) is REJECTED at run "
                        "time unless the workflow's top-level config sets 'capability_mode': 'execute' "
                        "(default ceiling is 'read_only'). Set it explicitly when the requested workflow "
                        "genuinely needs to write/send/execute — don't set it defensively for a read-only "
                        "workflow (e.g. one that only calls list_emails/read_file/tool_call to a browser tool). "
                        "\n\n"
                        "\n\n"
                        "'llm_extract' step (pull structured fields out of free text, e.g. a prior step's "
                        "raw message body) has params {'prompt': '...may reference $stepname.field...', "
                        "'schema': <JSON schema object the extracted output must match>, "
                        "'model': '<optional, default qwen3.5:4b — local ollama first, cloud fallback on failure>'}. Its result is the parsed JSON object, "
                        "and its own fields are then referenceable in a later step's 'condition' as "
                        "'<extract_step_name>.result.<field>'. "
                        "\n\n"
                        "'approval' step (draft-and-send — stage an action for human confirmation instead "
                        "of firing it automatically, e.g. before an email/telegram/imessage/webhook/tool_call "
                        "step the user should approve first) has params {'message': '<what to show the user>', "
                        "'expires_in_minutes': <optional, default 60>}. It genuinely PAUSES the whole "
                        "automation run right there — steps after it (including any step this one is meant "
                        "to gate) do NOT execute until a human calls approve_workflow(approval_id) or "
                        "reject_workflow(approval_id). Approve resumes the run from the next step "
                        "immediately; reject drops the rest of this run (the gated steps never happen) and "
                        "the automation continues on its normal schedule next time. list_pending_approvals "
                        "shows what's waiting. Put the 'approval' step immediately before whatever it should "
                        "gate — it does not name a target step, it just blocks in place. "
                        "\n\n"
                        "For conditional branching (accept/decline-style automations — 'watch X, and if it "
                        "matches criteria Y do A, otherwise do B'), add a 'condition' to a step: "
                        "{'condition': {'all'|'any': [{'field': '<stepname>.result.<subfield>', "
                        "'op': 'eq'|'neq'|'in'|'not_in'|'gte'|'lte'|'gt'|'lt'|'contains'|'calendar_free'|"
                        "'is_error'|'is_weekend'|'weekday_in'|'weekday_not_in', "
                        "'value': <for eq/neq/in/not_in/gte/lte/gt/lt/contains/weekday_in/weekday_not_in>, "
                        "'buffer_minutes': <optional, calendar_free only, default 60>}, ...]}, "
                        "'on_pass': '<step name to jump to if condition is true>', "
                        "'on_fail': '<step name to jump to if condition is false>'}. 'calendar_free' reads "
                        "the field as an ISO datetime string and checks the real calendar for conflicts — "
                        "no 'value' needed for it. 'is_error' checks whether an earlier step's result string "
                        "was an error (e.g. 'field': '<toolstep>.result', no 'value' needed) — use this to "
                        "branch on whether a prior 'tool_call' step actually succeeded; a failing tool_call "
                        "now also raises, so 'failure_mode': 'stop'/'rollback' work on it same as any other "
                        "step action (previously tool_call failures were silently swallowed into a normal-"
                        "looking result string — fixed 2026-07-16). 'is_weekend' (no 'value' needed) and "
                        "'weekday_in'/'weekday_not_in' (value = list of weekday names, e.g. ['Saturday', "
                        "'Sunday']) also read the field as an ISO datetime string and compute the day of week "
                        "deterministically in Python — use these instead of an extra 'llm_extract' step to "
                        "derive a day name, which is slower and less reliable for something this mechanical. "
                        "A step without 'condition' can instead set 'goto': "
                        "'<step name>' for an unconditional jump (skip ahead without gating). Either jump "
                        "target may be the literal string '__end__' to stop the workflow there. A step with "
                        "none of 'condition'/'on_pass'/'on_fail'/'goto' just falls through to the next step "
                        "in the list, same as before — this means a step you intend as a TERMINAL branch "
                        "(e.g. the 'accepted' outcome) still runs whatever step is declared after it unless "
                        "you give it 'goto': '__end__' explicitly; forgetting this is the single most common "
                        "mistake when authoring these workflows. Only binary pass/fail branching is supported "
                        "per step (chain multiple condition steps for a 3+ way split). Nested boolean logic IS "
                        "supported — an entry inside 'all'/'any' can itself be {'all'|'any': [...]} to express "
                        "'(A and B) or (C and D)' as a single condition, e.g. {'any': [{'all': [ruleA, ruleB]}, "
                        "{'all': [ruleC, ruleD]}]}."
                    ),
                },
                "schedule": {
                    "type": "object",
                    "description": (
                        "Schedule config. Use {'interval_seconds': N} for polling triggers "
                        "(email, google_sheet, file_change). Use {'cron': '0 8 * * *'} for "
                        "fixed-time schedules. Default for email: interval_seconds=60."
                    ),
                },
                "model": {
                    "type": "string",
                    "description": "LLM model to use for summarization (optional). Default: gemma4.",
                },
            },
            "required": ["name", "trigger_type", "action_type"],
        },
    },
}

LIST_AUTOMATIONS_SCHEMA = {
    "type": "function",
    "function": {
        "name": "list_automations",
        "description": "List all background automations with their status, schedule, last run, and next run.",
        "parameters": {
            "type": "object",
            "properties": {
                "status": {
                    "type": "string",
                    "enum": ["active", "paused"],
                    "description": "Filter by status. Omit entirely for all automations.",
                },
                "trigger_type": {
                    "type": "string",
                    "enum": ["email", "schedule", "webhook", "google_sheet", "file_change"],
                    "description": "Filter by trigger type. Omit for all trigger types.",
                },
                "action_type": {
                    "type": "string",
                    "enum": ["telegram", "notification", "http_webhook", "email", "tool_call", "imessage", "workflow"],
                    "description": "Filter by action type. Omit for all action types.",
                },
            },
        },
    },
}

PAUSE_AUTOMATION_SCHEMA = {
    "type": "function",
    "function": {
        "name": "pause_automation",
        "description": "Pause a running automation by its ID. Use list_automations to find the ID.",
        "parameters": {
            "type": "object",
            "properties": {
                "automation_id": {"type": "string", "description": "The workflow instance ID"},
            },
            "required": ["automation_id"],
        },
    },
}

RESUME_AUTOMATION_SCHEMA = {
    "type": "function",
    "function": {
        "name": "resume_automation",
        "description": "Resume a paused automation.",
        "parameters": {
            "type": "object",
            "properties": {
                "automation_id": {"type": "string", "description": "The workflow instance ID"},
            },
            "required": ["automation_id"],
        },
    },
}

DELETE_AUTOMATION_SCHEMA = {
    "type": "function",
    "function": {
        "name": "delete_automation",
        "description": (
            "Soft-delete an automation: sets status='deleted' and stops it from running. "
            "The row, config, and dedupe_key are NOT removed and it can theoretically be "
            "revived by direct DB access — don't describe this as permanent, irreversible, "
            "or 'wiped'. Use delete_workflow_permanently instead when the user explicitly "
            "asks for a permanent/hard/non-soft delete."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "automation_id": {"type": "string", "description": "The workflow instance ID"},
            },
            "required": ["automation_id"],
        },
    },
}

TRIGGER_AUTOMATION_NOW_SCHEMA = {
    "type": "function",
    "function": {
        "name": "trigger_automation_now",
        "description": "Force an automation to run immediately, bypassing its normal schedule.",
        "parameters": {
            "type": "object",
            "properties": {
                "automation_id": {"type": "string", "description": "The workflow instance ID"},
            },
            "required": ["automation_id"],
        },
    },
}

DELETE_WORKFLOW_PERMANENTLY_SCHEMA = {
    "type": "function",
    "function": {
        "name": "delete_workflow_permanently",
        "description": (
            "Hard-delete a workflow instance in one shot — removes the row from the "
            "workflow store entirely (unlike delete_automation, which only soft-deletes "
            "by marking status='deleted' and leaves the row in place). Cannot be undone: "
            "the workflow_id/dedupe_key is freed, so a builtin automation reseeds fresh "
            "on the next restart instead of staying suppressed."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "workflow_id": {"type": "string", "description": "The workflow instance ID (uuid), not the automation_id/task name"},
            },
            "required": ["workflow_id"],
        },
    },
}

GET_AUTOMATION_SCHEMA = {
    "type": "function",
    "function": {
        "name": "get_automation",
        "description": "Get the full configuration of a single automation (schedule, config, status, last run, next run). Use list_automations first to find the ID.",
        "parameters": {
            "type": "object",
            "properties": {
                "workflow_id": {"type": "string", "description": "The workflow instance ID (uuid)"},
            },
            "required": ["workflow_id"],
        },
    },
}

GET_AUTOMATION_HISTORY_SCHEMA = {
    "type": "function",
    "function": {
        "name": "get_automation_history",
        "description": (
            "Get the last 10 run results for an automation (timestamp + success/error/detail "
            "per run) — use this to answer 'has this been failing?' or 'when did this last "
            "run successfully?' instead of get_automation, which only shows the single most "
            "recent result."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "workflow_id": {"type": "string", "description": "The workflow instance ID (uuid)"},
            },
            "required": ["workflow_id"],
        },
    },
}

UPDATE_AUTOMATION_SCHEMA = {
    "type": "function",
    "function": {
        "name": "update_automation",
        "description": (
            "Modify an existing automation in place — change its name, schedule, trigger, "
            "action, or config without recreating it. Preserves housekeeping fields (e.g. "
            "a watcher's seen_pmids history). Use get_automation first to see current values."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "workflow_id": {"type": "string", "description": "The workflow instance ID (uuid) to modify"},
                "name": {"type": "string", "description": "New human-readable label (task_name)"},
                "trigger_type": {
                    "type": "string",
                    "enum": ["email", "schedule", "webhook", "google_sheet", "file_change"],
                    "description": "New trigger type",
                },
                "trigger_config": {"type": "object", "description": "New trigger config (merged into existing config)"},
                "action_type": {
                    "type": "string",
                    "enum": ["telegram", "notification", "http_webhook", "email", "tool_call", "imessage", "workflow"],
                    "description": "New action type",
                },
                "action_config": {
                    "type": "object",
                    "description": (
                        "New action config (merged into existing config). Same shape as "
                        "create_automation's action_config — see that schema for the full set of "
                        "supported keys per action_type (e.g. telegram's query/senders/max_results "
                        "watcher mode, email's topics/query news-digest and PubMed-watch modes)."
                    ),
                },
                "schedule": {
                    "type": "object",
                    "description": "New schedule. {'interval_seconds': N} for polling, {'cron': '0 8 * * *'} for fixed-time.",
                },
                "model": {"type": "string", "description": "LLM model override (optional)"},
            },
            "required": ["workflow_id"],
        },
    },
}

# ── Executors ─────────────────────────────────────────────────────────────────

_STOPWORDS = {
    "the", "a", "an", "and", "or", "for", "of", "to", "in", "on", "at", "my",
    "me", "with", "watch", "send", "create", "new", "automation", "workflow",
}


def _find_similar_automations(trigger_type: str, action_type: str, name: str) -> list[dict]:
    """Non-blocking fuzzy check: any existing automation (any status) with the
    same trigger_type + action_type and at least one shared significant word
    in its name. Automation names are freeform (unlike create_workflow's
    automation_id, which is exact/deterministic), so this is a heads-up, not
    a hard block — false positives on a fuzzy word match shouldn't stop a
    legitimate second automation from being created.
    """
    words = {w for w in name.lower().split() if len(w) > 3 and w not in _STOPWORDS}
    if not words:
        return []
    existing = workflow_store.list_workflow_instances(trigger_type=trigger_type, limit=100)
    matches = []
    for inst in existing:
        if inst.get("action_type") != action_type:
            continue
        existing_words = {w for w in (inst.get("task_name") or "").lower().split() if len(w) > 3}
        if words & existing_words:
            matches.append(inst)
    return matches


def create_automation(args: dict) -> str:
    if not _pipelines_checked_this_turn("list_automations", "list_available_pipelines"):
        return (
            "[guidance] Call list_automations (no status filter) first — it may show an existing "
            "active or paused automation that already covers this request. If none match, call "
            "create_automation again."
        )
    name = args.get("name", "Automation")
    trigger_type = args.get("trigger_type", "")
    trigger_config = args.get("trigger_config") or {}
    action_type = args.get("action_type", "")
    action_config = args.get("action_config") or {}
    schedule = args.get("schedule") or {}
    model = args.get("model", "")

    # Template mode — pre-fill config from template
    template_name = args.get("template") or trigger_config.pop("_template", None) or action_config.pop("_template", None)
    if template_name and template_name in INTEGRATION_TEMPLATES:
        tmpl = INTEGRATION_TEMPLATES[template_name]
        trigger_type = trigger_type or tmpl.get("trigger_type", trigger_type)
        action_type = action_type or tmpl.get("action_type", action_type)
        schedule = schedule or tmpl.get("schedule", schedule)
        trigger_config = {**tmpl.get("trigger_config", {}), **trigger_config}
        action_config = {**tmpl.get("action_config", {}), **action_config}

    # Default schedule for email/polling triggers
    if not schedule and trigger_type in ("email", "google_sheet", "file_change"):
        schedule = {"interval_seconds": 60}

    cron = (schedule.get("cron") or "").strip()
    if cron:
        cron_err = workflow_store.validate_cron(cron, schedule.get("timezone"))
        if cron_err:
            return f"Error: {cron_err}"

    workflow_store.init()
    similar = _find_similar_automations(trigger_type, action_type, name)

    # Merge trigger_config + action_config + model into config_json
    config = {**trigger_config, **action_config}
    if model and action_type == "workflow":
        config["model"] = model
    if trigger_type == "webhook":
        config["_webhook_secret"] = secrets.token_urlsafe(24)

    # ponytail: code-level gate, not just a prompt instruction — a prompt-only
    # "ask about this" rule was tested live (2026-07-17) and the orchestrator
    # built a watch_contact automation anyway without ever asking what
    # distinguishes a real request from noise (a cancellation notice, a
    # thank-you) sharing the same tracked fields. watch_contact mode replies
    # to every new message from that contact matching a template — without
    # `trigger_phrases`, EVERY message gets a reply, including ones that
    # aren't actually asking anything. Block here instead of silently
    # defaulting to "reply to everything" or (worse) borrowing another
    # automation's phrases (see jobs/handlers/user_automation.py's own
    # comment on the same incident: BCH's hardcoded phrase list used to leak
    # into every other watch_contact automation via the shared handler).
    if action_type == "imessage" and config.get("watch_contact") and not config.get("trigger_phrases"):
        return (
            "[needs clarification] This watch_contact automation has no `trigger_phrases` "
            "configured — without it, EVERY new message from this contact gets a reply, "
            "including ones that aren't actually a request (cancellations, thank-yous, "
            "status updates). Ask the user what phrase or pattern in a real message means "
            "\"this needs a reply\" (e.g. \"can you help\", \"enter yes or no\"), then retry "
            "create_automation with action_config.trigger_phrases set to that list."
        )

    instance = workflow_store.create_workflow_instance(
        automation_id="user.automation",
        task_name=name,
        trigger_type=trigger_type,
        action_type=action_type,
        schedule=schedule,
        config=config,
        status="active",
    )
    fyi = ""
    if similar:
        listed = ", ".join(f"{i['task_name']!r} ({i['status']}, id={i['id'][:8]}…)" for i in similar)
        fyi = (
            f"Note: found similar existing automation(s) with the same trigger/action type: "
            f"{listed}. Consider resuming/updating one of those instead of this new one if it "
            f"already covers the request.\n"
        )
    webhook_note = ""
    if trigger_type == "webhook":
        webhook_note = (
            f"\nWebhook URL (give this to the external service, POST to trigger): "
            f"{PUBLIC_BASE_URL}/webhooks/trigger/{instance['id']}?secret={config['_webhook_secret']}"
        )
    return (
        fyi +
        f"Automation created: id={instance['id'][:8]}…, "
        f"trigger={trigger_type}, action={action_type}, "
        f"schedule={json.dumps(schedule)}, status=active" +
        webhook_note
    )


def list_automations(args: dict) -> str:
    status = args.get("status", "")
    trigger_type = args.get("trigger_type", "")
    action_type = args.get("action_type", "")
    workflow_store.init()
    instances = workflow_store.list_workflow_instances(status=status, trigger_type=trigger_type)
    if action_type:
        instances = [i for i in instances if i.get("action_type") == action_type]
    if not instances:
        return "No automations found."
    lines = []
    for inst in instances:
        last = inst.get("last_run_at") or "never"
        nxt = inst.get("next_run_at") or "—"
        cfg = inst.get("config") or {}
        seen = cfg.get("seen_pmids")
        extra = f"  seen_pmids={len(seen)}" if seen else ""
        lines.append(
            f"• [{inst['status'].upper()}] {inst['task_name']}\n"
            f"  id={inst['id']}\n"
            f"  trigger={inst['trigger_type']} → action={inst['action_type']}\n"
            f"  schedule={json.dumps(inst.get('schedule') or {})}{extra}\n"
            f"  last_run={last}  next_run={nxt}"
        )
    return "\n\n".join(lines)


def pause_automation(args: dict) -> str:
    workflow_store.init()
    result = workflow_store.pause_workflow_instance(args["automation_id"])
    if not result:
        return f"Automation {args['automation_id']!r} not found."
    return f"Automation {result['task_name']!r} paused."


def resume_automation(args: dict) -> str:
    workflow_store.init()
    result = workflow_store.resume_workflow_instance(args["automation_id"])
    if not result:
        return f"Automation {args['automation_id']!r} not found."
    return f"Automation {result['task_name']!r} resumed. Next run: {result.get('next_run_at', '—')}"


def delete_automation(args: dict) -> str:
    workflow_store.init()
    result = workflow_store.update_workflow_instance(args["automation_id"], status="deleted")
    if not result:
        return f"Automation {args['automation_id']!r} not found."
    return f"Automation {result['task_name']!r} deleted."


def delete_workflow_permanently(args: dict) -> str:
    workflow_store.init()
    workflow_id = args["workflow_id"]
    instance = workflow_store.get_workflow_instance(workflow_id)
    if not instance:
        return f"Workflow {workflow_id!r} not found."
    task_name = instance["task_name"]
    workflow_store.delete_workflow_instance(workflow_id)
    return f"Workflow {task_name!r} (id={workflow_id[:8]}…) permanently deleted."


def trigger_automation_now(args: dict) -> str:
    workflow_store.init()
    automation_id = args["automation_id"]
    instance = workflow_store.get_workflow_instance(automation_id)
    if not instance:
        return f"Automation {automation_id!r} not found."
    # Dispatch synchronously in the current process (not via worker poll cycle)
    # so the latest handler code on disk is always used. The worker process may
    # have a stale module cache — forcing sync dispatch here avoids that.
    from datetime import datetime, timezone
    from jobs import handler_registry
    from jobs.handlers import user_automation as _ua
    handler_registry.register("user.automation", _ua.handle)
    result = handler_registry.dispatch(instance)
    now_iso = datetime.now(timezone.utc).isoformat()
    updates = {"last_run_at": now_iso, "last_result": result}
    # ponytail: if this automation is already due, the worker's next 10s poll
    # would independently re-claim + dispatch it again — bump next_run_at past
    # now (recurring schedules only; one-shot/webhook automations have no
    # schedule to advance) so this manual trigger doesn't double-fire.
    schedule = instance.get("schedule") or {}
    next_run_at = instance.get("next_run_at")
    if schedule and next_run_at and next_run_at <= now_iso:
        updates["next_run_at"] = workflow_store._compute_next_run(schedule)
    workflow_store.update_workflow_instance(automation_id, **updates)
    workflow_store.append_run_history(
        automation_id, result if isinstance(result, dict) else {"result": result}
    )
    return f"Automation {instance['task_name']!r} ran now: {result}"


def get_automation(args: dict) -> str:
    workflow_store.init()
    inst = workflow_store.get_workflow_instance(args["workflow_id"])
    if not inst:
        return f"Automation {args['workflow_id']!r} not found."
    if inst.get("trigger_type") == "webhook":
        secret = (inst.get("config") or {}).get("_webhook_secret", "")
        inst = {**inst, "webhook_url": f"{PUBLIC_BASE_URL}/webhooks/trigger/{inst['id']}?secret={secret}"}
    return json.dumps(inst, indent=2, default=str)


def get_automation_history(args: dict) -> str:
    workflow_store.init()
    inst = workflow_store.get_workflow_instance(args["workflow_id"])
    if not inst:
        return f"Automation {args['workflow_id']!r} not found."
    history = inst.get("run_history") or []
    if not history:
        return f"No run history yet for {inst['task_name']!r}."
    return json.dumps(history, indent=2, default=str)


def update_automation(args: dict) -> str:
    workflow_id = args["workflow_id"]
    workflow_store.init()
    current = workflow_store.get_workflow_instance(workflow_id)
    if not current:
        return f"Automation {workflow_id!r} not found."

    # Merge config: trigger_config + action_config + model layered on top of
    # the existing config so watcher bookkeeping (e.g. seen_pmids) survives.
    config = dict(current.get("config") or {})
    config.update(args.get("trigger_config") or {})
    config.update(args.get("action_config") or {})
    new_action_type = args.get("action_type") or current.get("action_type")
    if args.get("model") and new_action_type == "workflow":
        config["model"] = args["model"]
    new_trigger_type = args.get("trigger_type") or current.get("trigger_type")
    if new_trigger_type == "webhook" and not config.get("_webhook_secret"):
        config["_webhook_secret"] = secrets.token_urlsafe(24)

    # Resolve schedule + recompute next fire time if schedule changed.
    schedule = args.get("schedule") or current.get("schedule") or {}
    next_run_at = None
    if args.get("schedule"):
        from store.workflow_store import _compute_next_run, _utcnow
        next_run_at = _compute_next_run(schedule, now=_utcnow())

    updates: dict = {"config": config, "schedule": schedule}
    if args.get("name"):
        # task_name lives on the row, not in config; patch via status-safe path
        from store import workflow_store as _ws
        with _ws._conn() as conn:
            conn.execute(
                "UPDATE workflow_instances SET task_name = ?, updated_at = ? WHERE id = ?",
                (args["name"], _ws._now(), workflow_id),
            )
    if args.get("trigger_type"):
        updates["trigger_type"] = args["trigger_type"]
    if args.get("action_type"):
        updates["action_type"] = args["action_type"]
    if next_run_at:
        updates["next_run_at"] = next_run_at

    result = workflow_store.update_workflow_instance(workflow_id, **updates)
    if not result:
        return f"Automation {workflow_id!r} update failed."
    webhook_note = ""
    if result.get("trigger_type") == "webhook":
        secret = (result.get("config") or {}).get("_webhook_secret", "")
        webhook_note = f"\nWebhook URL: {PUBLIC_BASE_URL}/webhooks/trigger/{result['id']}?secret={secret}"
    return (
        f"Automation {result['task_name']!r} updated: "
        f"trigger={result['trigger_type']} → action={result['action_type']}, "
        f"schedule={json.dumps(result.get('schedule') or {})}" +
        webhook_note
    )


# ── Approval Executors ─────────────────────────────────────────────────────────

def _list_pending_approvals(args: dict) -> str:
    workflow_store.init()
    pending = workflow_store.list_approval_requests(status="pending", limit=50)
    if not pending:
        return "No pending approvals."
    lines = []
    for a in pending:
        aid = a.get("id", "?")[:12]
        kind = a.get("kind", "?")
        wf = a.get("workflow_instance_id", "?")
        created = a.get("created_at", "?")[:19]
        payload = a.get("payload_json") or a.get("payload") or {}
        msg = payload.get("message", "")
        lines.append(f"• {aid}  kind={kind}  workflow={wf}\n  created={created}  message={msg}")
    return "\n\n".join(lines)


def _matching_paused_workflow(app: dict, aid: str) -> dict | None:
    """If this approval paused a workflow_instance (see jobs/handlers/user_automation.py's
    'approval' step) and it's still waiting on exactly this approval, return that
    workflow_instance. Returns None for a bare approval not tied to a workflow step,
    or one that already resumed/was rejected some other way."""
    wf_id = app.get("workflow_instance_id", "")
    if not wf_id:
        return None
    wf = workflow_store.get_workflow_instance(wf_id)
    if not wf:
        return None
    pause = (wf.get("config") or {}).get("_workflow_pause")
    if not pause or pause.get("approval_id") != aid:
        return None
    return wf


def _approve_workflow(args: dict) -> str:
    workflow_store.init()
    aid = args["approval_id"]
    app = workflow_store.get_approval_request(aid)
    if not app:
        return f"Approval request {aid!r} not found."
    if app.get("status") != "pending":
        return f"Approval request {aid!r} already {app['status']}."
    workflow_store.resolve_approval_request(aid, "approved")

    wf = _matching_paused_workflow(app, aid)
    if wf is None:
        return f"Approval {aid[:12]} approved."
    import datetime as _dt
    # Leave config (and its _workflow_pause) untouched here — handle() in
    # jobs/handlers/user_automation.py is what actually reads resume_step +
    # results and clears _workflow_pause, on its next invocation. This tool
    # only needs to get that invocation to happen: next_run_at=now gets the
    # worker to re-claim this instance on its next poll.
    workflow_store.update_workflow_instance(
        wf["id"], status="active",
        next_run_at=_dt.datetime.now(_dt.timezone.utc).isoformat(),
    )
    return f"Approval {aid[:12]} approved — resuming automation."


def _reject_workflow(args: dict) -> str:
    workflow_store.init()
    aid = args["approval_id"]
    app = workflow_store.get_approval_request(aid)
    if not app:
        return f"Approval request {aid!r} not found."
    if app.get("status") != "pending":
        return f"Approval request {aid!r} already {app['status']}."
    workflow_store.resolve_approval_request(aid, "rejected")

    wf = _matching_paused_workflow(app, aid)
    if wf is None:
        return f"Approval {aid[:12]} rejected."
    # Unlike approve, drop _workflow_pause here directly — handle() should
    # never resume this run for a rejected approval. Reactivate for the
    # workflow's normal next scheduled run (next_run_at already holds that
    # from before the run paused); don't force an immediate re-run.
    cfg = {k: v for k, v in (wf.get("config") or {}).items() if k != "_workflow_pause"}
    workflow_store.update_workflow_instance(wf["id"], config=cfg, status="active")
    return f"Approval {aid[:12]} rejected — automation resumes on its normal schedule, gated steps skipped."


# ── Stats / Monitoring Executors ──────────────────────────────────────────────


def get_automation_stats(args: dict) -> str:
    workflow_store.init()
    all_wfs = workflow_store.list_workflow_instances(limit=9999)

    total = len(all_wfs)
    by_status: dict[str, int] = {}
    by_action: dict[str, int] = {}
    by_trigger: dict[str, int] = {}
    failures: list[dict] = []
    total_seen = 0

    for w in all_wfs:
        s = w.get("status", "unknown")
        by_status[s] = by_status.get(s, 0) + 1
        a = w.get("action_type", "unknown")
        by_action[a] = by_action.get(a, 0) + 1
        t = w.get("trigger_type", "unknown")
        by_trigger[t] = by_trigger.get(t, 0) + 1

        last = w.get("last_result") or {}
        raw = json.dumps(last).lower()
        if "error" in raw:
            failures.append(w)

        cfg = w.get("config") or {}
        total_seen += len(cfg.get("seen_ids") or [])
        total_seen += len(cfg.get("seen_pmids") or [])
        total_seen += len(cfg.get("seen_hashes") or [])
        total_seen += 1 if cfg.get("seen_rowid") else 0

    status_line = ", ".join(f"{k}={v}" for k, v in sorted(by_status.items()))
    action_line = ", ".join(f"{k}={v}" for k, v in sorted(by_action.items()))
    trigger_line = ", ".join(f"{k}={v}" for k, v in sorted(by_trigger.items()))

    # longest-idle: last_run_at furthest in past or None
    from datetime import datetime, timezone
    now = datetime.now(timezone.utc)
    idle_sorted = sorted(
        all_wfs,
        key=lambda w: (
            w.get("last_run_at") or "2000-01-01",
            w.get("task_name", ""),
        ),
    )
    idle_top = idle_sorted[:3]
    idle_lines = []
    for w in idle_top:
        lr = w.get("last_run_at") or "never"
        idle_lines.append(f"  {w['task_name']!r} last_run={lr}")

    return (
        f"Automation Stats ({total} total)\n"
        f"By status: {status_line}\n"
        f"By action: {action_line}\n"
        f"By trigger: {trigger_line}\n"
        f"Failures (last_result contains 'error'): {len(failures)}\n"
        f"Total dedup tokens tracked: {total_seen}\n"
        f"Longest-idle:\n" + "\n".join(idle_lines)
    )


def get_workflow_failures(args: dict) -> str:
    max_results = args.get("max_results", 5)
    workflow_store.init()
    all_wfs = workflow_store.list_workflow_instances(limit=9999)

    failures = []
    for w in all_wfs:
        last = w.get("last_result") or {}
        raw = json.dumps(last).lower()
        if "error" in raw:
            err_msg = json.dumps(last, default=str)
            failures.append((w["task_name"], w.get("last_run_at") or "never", err_msg))

    if not failures:
        return "No workflows with errors found."

    total = len(failures)
    failures = failures[:max_results]
    lines = [f"Workflow failures ({total} total, showing {len(failures)}):"]
    for name, lr, err in failures:
        lines.append(f"• {name!r}  last_run={lr}")
        lines.append(f"  error={err[:300]}")
    return "\n".join(lines)


def get_workflow_logs(args: dict) -> str:
    workflow_store.init()
    workflow_id = args.get("workflow_id")
    if not workflow_id:
        return "Error: get_workflow_logs requires 'workflow_id'."
    inst = workflow_store.get_workflow_instance(workflow_id)
    if not inst:
        return f"Workflow {workflow_id!r} not found."
    history = inst.get("run_history") or []
    if not history:
        return f"No run history yet for {inst['task_name']!r}."

    lines = [f"Run history for {inst['task_name']!r} ({len(history)} entries):"]
    for entry in history:
        ts = entry.get("ts", "?")
        detail = {k: v for k, v in entry.items() if k != "ts"}
        lines.append(f"  [{ts}] {json.dumps(detail, default=str)}")

    if len(history) < 5:
        lines.append(f"Note: only {len(history)} entries — not enough data for trend.")
    return "\n".join(lines)


# ── Integration Templates ────────────────────────────────────────────────────

GET_INTEGRATION_TEMPLATES_SCHEMA = {
    "type": "function",
    "function": {
        "name": "get_integration_templates",
        "description": "List available integration templates for webhooks and automations. Returns formatted descriptions of all built-in templates (GitHub star watch, GitHub issue watch, Stripe payment alerts, scheduled notifications) that can be used with create_automation's 'template' parameter.",
        "parameters": {"type": "object", "properties": {}},
    },
}


def get_integration_templates(args: dict) -> str:
    lines = ["Available integration templates:"]
    for name, tmpl in INTEGRATION_TEMPLATES.items():
        cfg_parts = []
        if tmpl.get("trigger_config", {}).get("webhook_hmac_algorithm"):
            cfg_parts.append(f"hmac={tmpl['trigger_config']['webhook_hmac_algorithm']}")
        extra = f"  [{', '.join(cfg_parts)}]" if cfg_parts else ""
        lines.append(
            f"\n  {name}\n"
            f"    {tmpl['description']}\n"
            f"    trigger={tmpl['trigger_type']}  action={tmpl['action_type']}{extra}"
        )
    return "\n".join(lines)


# ── Approval Schemas ─────────────────────────────────────────────────────────────

LIST_PENDING_APPROVALS_SCHEMA = {
    "type": "function",
    "function": {
        "name": "list_pending_approvals",
        "description": "List all pending approval requests — automations paused at an 'approval' "
        "workflow step, waiting on approve_workflow/reject_workflow before their gated steps run.",
        "parameters": {"type": "object", "properties": {}},
    },
}

APPROVE_WORKFLOW_SCHEMA = {
    "type": "function",
    "function": {
        "name": "approve_workflow",
        "description": "Approve a pending workflow approval request by ID — resumes the paused "
        "automation run immediately, executing the step(s) the 'approval' step was gating.",
        "parameters": {
            "type": "object",
            "properties": {
                "approval_id": {"type": "string", "description": "Approval request ID"},
            },
            "required": ["approval_id"],
        },
    },
}

REJECT_WORKFLOW_SCHEMA = {
    "type": "function",
    "function": {
        "name": "reject_workflow",
        "description": "Reject a pending workflow approval request by ID — the gated step(s) never "
        "run for this automation run; the automation continues on its normal schedule next time.",
        "parameters": {
            "type": "object",
            "properties": {
                "approval_id": {"type": "string", "description": "Approval request ID"},
            },
            "required": ["approval_id"],
        },
    },
}

# ── Stats / Monitoring Schemas ────────────────────────────────────────────────

GET_AUTOMATION_STATS_SCHEMA = {
    "type": "function",
    "function": {
        "name": "get_automation_stats",
        "description": "Get aggregate stats for all workflow instances: counts by status/action/trigger, failure count, longest-idle workflows, total dedup tokens tracked.",
        "parameters": {
            "type": "object",
            "properties": {},
        },
    },
}

GET_WORKFLOW_FAILURES_SCHEMA = {
    "type": "function",
    "function": {
        "name": "get_workflow_failures",
        "description": "List workflows whose last run had an error.",
        "parameters": {
            "type": "object",
            "properties": {
                "max_results": {
                    "type": "integer",
                    "description": "Max failures to show (default 5)",
                },
            },
        },
    },
}

GET_WORKFLOW_LOGS_SCHEMA = {
    "type": "function",
    "function": {
        "name": "get_workflow_logs",
        "description": "Get run history for a specific workflow.",
        "parameters": {
            "type": "object",
            "properties": {
                "workflow_id": {
                    "type": "string",
                    "description": "Workflow instance ID",
                },
            },
            "required": ["workflow_id"],
        },
    },
}

QUERY_SUBAGENT_FINDINGS_SCHEMA = {
    "type": "function",
    "function": {
        "name": "query_subagent_findings",
        "description": (
            "Read the actual saved findings/insights a background subagent has accumulated "
            "(reddit_intel's market insights, science_scout's tracked claims) — not just its "
            "last run summary. Use for 'what has reddit_intel found on X' / 'what breakthroughs "
            "has science_scout been tracking' type questions."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "subagent": {
                    "type": "string",
                    "enum": ["reddit_intel", "science_scout"],
                    "description": "Which subagent's findings to read",
                },
                "limit": {"type": "integer", "description": "Max findings to return (default 20)"},
            },
            "required": ["subagent"],
        },
    },
}


def query_subagent_findings(args: dict) -> str:
    subagent = args.get("subagent", "")
    limit = int(args.get("limit") or 20)
    if subagent == "reddit_intel":
        from store import reddit_intel_store as store
        rows = store.list_active_insights(limit=limit)
    elif subagent == "science_scout":
        from store import science_scout_store as store
        rows = store.list_active_claims(limit=limit)
    else:
        return f"Unknown subagent {subagent!r}. Use 'reddit_intel' or 'science_scout'."
    if not rows:
        return f"No active findings for {subagent}."
    return json.dumps(rows, indent=2, default=str)


AUTOMATION_TOOLS_SCHEMAS = [
    CREATE_AUTOMATION_SCHEMA,
    LIST_AUTOMATIONS_SCHEMA,
    GET_AUTOMATION_SCHEMA,
    GET_AUTOMATION_HISTORY_SCHEMA,
    UPDATE_AUTOMATION_SCHEMA,
    PAUSE_AUTOMATION_SCHEMA,
    RESUME_AUTOMATION_SCHEMA,
    DELETE_AUTOMATION_SCHEMA,
    TRIGGER_AUTOMATION_NOW_SCHEMA,
    DELETE_WORKFLOW_PERMANENTLY_SCHEMA,
    GET_AUTOMATION_STATS_SCHEMA,
    GET_WORKFLOW_FAILURES_SCHEMA,
    GET_WORKFLOW_LOGS_SCHEMA,
    GET_INTEGRATION_TEMPLATES_SCHEMA,
    LIST_PENDING_APPROVALS_SCHEMA,
    APPROVE_WORKFLOW_SCHEMA,
    REJECT_WORKFLOW_SCHEMA,
    QUERY_SUBAGENT_FINDINGS_SCHEMA,
]

AUTOMATION_EXECUTORS = {
    "create_automation":      create_automation,
    "list_automations":       list_automations,
    "get_automation":         get_automation,
    "get_automation_history": get_automation_history,
    "update_automation":      update_automation,
    "pause_automation":       pause_automation,
    "resume_automation":      resume_automation,
    "delete_automation":      delete_automation,
    "trigger_automation_now": trigger_automation_now,
    "delete_workflow_permanently": delete_workflow_permanently,
    "get_automation_stats":   get_automation_stats,
    "get_workflow_failures":  get_workflow_failures,
    "get_workflow_logs":      get_workflow_logs,
    "get_integration_templates": get_integration_templates,
    "list_pending_approvals": _list_pending_approvals,
    "approve_workflow":       _approve_workflow,
    "reject_workflow":        _reject_workflow,
    "query_subagent_findings": query_subagent_findings,
}

# Public aliases for direct import
list_pending_approvals = _list_pending_approvals
approve_workflow = _approve_workflow
reject_workflow = _reject_workflow
