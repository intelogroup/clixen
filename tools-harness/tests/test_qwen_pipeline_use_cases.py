import json
import os
import unittest
import urllib.request


MODEL = os.environ.get("QWEN_PIPELINE_MODEL", "qwen3:4b")
OLLAMA_URL = os.environ.get("OLLAMA_URL", "http://127.0.0.1:11434/api/chat")

SIDE_EFFECT_ACTIONS = {
    "create_reminder",
    "send_message",
    "create_task",
    "db_insert",
    "db_update",
    "db_delete",
}

READ_ONLY_ACTIONS = {
    "get_emails",
    "web_search",
    "summarize_document",
    "db_read",
}

REQUIRED_FIELDS = {
    "create_reminder": ("title", "due_at"),
    "send_message": ("channel", "recipient", "message"),
    "create_task": ("title",),
    "web_search": ("url", "query"),
    "summarize_document": ("summary",),
    "db_read": ("table", "query"),
    "db_insert": ("table", "row"),
}

BAD_VALUES = {"", "unknown", "now", "today", "tomorrow", "tbd", "null", "none", "user"}

SYSTEM_PROMPT = """You are Qwen, used only as a local action planner.
Return exactly one JSON object. No markdown. No prose outside JSON.

Schema:
{
  "action": "get_emails|summarize_document|create_reminder|send_message|create_task|web_search|db_read|db_insert|ask_clarification",
  "args": {},
  "requires_confirmation": true,
  "user_response": "short user-facing text"
}

Required fields:
- create_reminder: args.title, args.due_at
- send_message: args.channel, args.recipient, args.message
- create_task: args.title
- web_search: args.url, args.query
- summarize_document: args.summary
- db_read: args.table, args.query
- db_insert: args.table, args.row

Rules:
- Never invent missing values.
- Never use placeholders: now, today, tomorrow, unknown, TBD, null, empty string, user.
- If required fields are missing, return ask_clarification.
- create/write/send/update/delete actions require requires_confirmation true.
- read-only actions require requires_confirmation false.
"""


def _is_bad_value(value):
    if value is None:
        return True
    if isinstance(value, str):
        return value.strip().lower() in BAD_VALUES
    return False


def _find_key(container, key):
    if isinstance(container, dict):
        if key in container:
            return container[key]
        for value in container.values():
            found = _find_key(value, key)
            if found is not _MISSING:
                return found
    if isinstance(container, list):
        for value in container:
            found = _find_key(value, key)
            if found is not _MISSING:
                return found
    return _MISSING


_MISSING = object()


def missing_required_fields(plan):
    action = plan.get("action")
    args = plan.get("args")
    if not isinstance(args, dict):
        return list(REQUIRED_FIELDS.get(action, ()))

    missing = []
    for field in REQUIRED_FIELDS.get(action, ()):
        value = _find_key(args, field)
        if value is _MISSING or _is_bad_value(value):
            missing.append(field)
    return missing


def normalize_plan(plan):
    action = plan.get("action")
    missing = missing_required_fields(plan)

    if missing:
        return {
            "action": "ask_clarification",
            "args": {
                "missing": missing,
                "question": "What should I use for " + ", ".join(missing) + "?",
            },
            "requires_confirmation": False,
            "user_response": "What should I use for " + ", ".join(missing) + "?",
        }

    normalized = dict(plan)
    if action in SIDE_EFFECT_ACTIONS:
        normalized["requires_confirmation"] = True
    elif action in READ_ONLY_ACTIONS:
        normalized["requires_confirmation"] = False
    return normalized


def call_qwen(prompt):
    payload = {
        "model": MODEL,
        "messages": [
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": prompt},
        ],
        "format": "json",
        "think": False,
        "stream": False,
        "options": {
            "temperature": 0,
            "top_p": 0.8,
            "top_k": 20,
            "num_predict": 256,
        },
    }
    req = urllib.request.Request(
        OLLAMA_URL,
        data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=150) as response:
        raw = json.loads(response.read().decode("utf-8"))
    content = raw.get("message", {}).get("content", "")
    return json.loads(content)


class QwenPipelineValidatorTest(unittest.TestCase):
    def test_missing_reminder_date_is_forced_to_clarification(self):
        qwen_style_bad_plan = {
            "action": "create_reminder",
            "args": {"title": "Call Alex", "due_at": "unknown"},
            "requires_confirmation": True,
            "user_response": "Reminder created.",
        }

        plan = normalize_plan(qwen_style_bad_plan)

        self.assertEqual(plan["action"], "ask_clarification")
        self.assertEqual(plan["args"]["missing"], ["due_at"])
        self.assertFalse(plan["requires_confirmation"])

    def test_missing_message_recipient_is_forced_to_clarification(self):
        qwen_style_bad_plan = {
            "action": "send_message",
            "args": {
                "channel": "whatsapp",
                "recipient": "user",
                "message": "I am outside.",
            },
            "requires_confirmation": True,
            "user_response": "Message ready.",
        }

        plan = normalize_plan(qwen_style_bad_plan)

        self.assertEqual(plan["action"], "ask_clarification")
        self.assertEqual(plan["args"]["missing"], ["recipient"])
        self.assertFalse(plan["requires_confirmation"])

    def test_side_effects_always_require_confirmation(self):
        qwen_style_bad_plan = {
            "action": "db_insert",
            "args": {
                "table": "reminders",
                "row": {
                    "title": "Pay rent",
                    "due_at": "2026-05-03T10:00:00-04:00",
                    "priority": "medium",
                },
            },
            "requires_confirmation": False,
            "user_response": "Inserted.",
        }

        plan = normalize_plan(qwen_style_bad_plan)

        self.assertEqual(plan["action"], "db_insert")
        self.assertTrue(plan["requires_confirmation"])

    def test_read_only_actions_do_not_require_confirmation(self):
        qwen_style_bad_plan = {
            "action": "web_search",
            "args": {
                "url": "https://example.com/pricing",
                "query": "Enterprise plan price",
            },
            "requires_confirmation": True,
            "user_response": "Searching.",
        }

        plan = normalize_plan(qwen_style_bad_plan)

        self.assertEqual(plan["action"], "web_search")
        self.assertFalse(plan["requires_confirmation"])


@unittest.skipUnless(
    os.environ.get("RUN_QWEN_LIVE") == "1",
    "set RUN_QWEN_LIVE=1 to run live Ollama/Qwen tests",
)
class QwenLivePipelineUseCasesTest(unittest.TestCase):
    def assert_pipeline_plan(self, prompt, expected_action):
        raw_plan = call_qwen(prompt)
        plan = normalize_plan(raw_plan)
        self.assertEqual(plan["action"], expected_action, raw_plan)
        self.assertFalse(missing_required_fields(plan), raw_plan)
        return plan

    def test_complete_reminder(self):
        plan = self.assert_pipeline_plan(
            "Create a reminder to renew passport on 2026-05-01 at 09:00, priority high.",
            "create_reminder",
        )
        self.assertTrue(plan["requires_confirmation"])

    def test_missing_reminder_date(self):
        plan = self.assert_pipeline_plan("Remind me to call Alex.", "ask_clarification")
        self.assertFalse(plan["requires_confirmation"])

    def test_complete_telegram_message(self):
        plan = self.assert_pipeline_plan(
            "Send Maya a Telegram message: Running 10 minutes late.",
            "send_message",
        )
        self.assertTrue(plan["requires_confirmation"])

    def test_missing_whatsapp_recipient(self):
        plan = self.assert_pipeline_plan(
            "Send a WhatsApp message saying I am outside.",
            "ask_clarification",
        )
        self.assertFalse(plan["requires_confirmation"])

    def test_create_google_task(self):
        plan = self.assert_pipeline_plan(
            "Create a Google task titled Submit expense report.",
            "create_task",
        )
        self.assertTrue(plan["requires_confirmation"])

    def test_web_search_specific_page(self):
        plan = self.assert_pipeline_plan(
            "Search https://example.com/pricing for the Enterprise plan price.",
            "web_search",
        )
        self.assertFalse(plan["requires_confirmation"])

    def test_document_summary(self):
        plan = self.assert_pipeline_plan(
            "Summarize this document: The invoice importer rejects rows missing vendor_id or amount_cents. Failed imports write audit rows with error_code and source_file.",
            "summarize_document",
        )
        self.assertFalse(plan["requires_confirmation"])

    def test_db_read(self):
        plan = self.assert_pipeline_plan(
            "Read table reminders where priority is high.",
            "db_read",
        )
        self.assertFalse(plan["requires_confirmation"])

    def test_db_insert_strict_schema(self):
        plan = self.assert_pipeline_plan(
            "Write this to the DB: table reminders, title 'Pay rent', due_at '2026-05-03T10:00:00-04:00', priority 'medium'.",
            "db_insert",
        )
        self.assertTrue(plan["requires_confirmation"])


if __name__ == "__main__":
    unittest.main()


# Integration: requires real Ollama/network/creds/server — auto-skipped when deps down.
import pytest as _pytest
pytestmark = _pytest.mark.live
