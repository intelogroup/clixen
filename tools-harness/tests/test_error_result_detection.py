"""is_error_result: prefix-anchored tool-result error detection.

Replaces the old substring heuristic ("error"/"failed" anywhere in the result)
that false-positived on legitimate content and drove spurious model escalation.
"""
import pytest

from tools.registry import is_error_result


@pytest.mark.parametrize("result", [
    "[error] tool execution failed: boom",
    "[tool error] list_emails failed: quota",
    "[gmail error] list_emails failed: HttpError 429",
    "[browser error] element not found",
    "[tasks error] auth expired",
    "[calendar error] not authenticated",
    "[blocked] bash_exec rejected: denied pattern",
    "[invalid arguments] send_email rejected: 'to' is a required property",
    "Error: no credentials configured",
    "Unknown tool: frobnicate",
    "  [error] leading whitespace still detected",
    "[uber error] could not read trip list",
])
def test_error_results_detected(result):
    assert is_error_result(result) is True


@pytest.mark.parametrize("result", [
    "No results found.",
    "No emails found.",
    "No automations found.",
    "Your booking failed-payment reminder email says the card was declined.",
    "Summary: 3 emails about failed deliveries this week.",
    "The word error appears mid-sentence and is not a failure.",
    "Here is your answer.\n\n[subagent intent=email status=ok tools=list_emails errors=0]",
    "",
    "Telegram message sent (message_id=42)",
])
def test_normal_results_not_detected(result):
    assert is_error_result(result) is False
