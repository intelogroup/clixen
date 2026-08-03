"""
Competitor Change Monitor — 8-step pipeline.
1. Scrape competitor URL
2. Compare with stored previous version
3. AI classify change significance (major/minor/none)
4. iMessage alert if major
5. Telegram all changes
6. Log to Google Sheet
7. Store new content for next diff
8. Notification
"""

from workflows.pipeline import (
    WebScrapeStep, DiffCompareStep, AiClassifyStep, ConditionStep,
    iMessageSendStep, TelegramSendStep, LogAppendStep, NotificationStep,
    SheetAppendStep, run_pipeline,
)

WATCHER_SCHEMA = {
    "type": "function",
    "function": {
        "name": "create_competitor_monitor",
        "description": (
            "Create an 8-step competitor change monitor: scrapes a URL daily, "
            "diffs against previous version, AI-classifies change significance, "
            "iMessage alerts on major changes, Telegram on all, logs to sheet."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "imessage_recipient": {
                    "type": "string",
                    "description": "iMessage recipient for major change alerts",
                },
                "target_url": {
                    "type": "string",
                    "description": "Competitor URL to monitor",
                },
                "spreadsheet_id": {
                    "type": "string",
                    "description": "Google Sheet ID to log changes",
                    "default": "",
                },
                "poll_interval_seconds": {
                    "type": "integer",
                    "description": "Poll interval, default 86400 (daily)",
                    "default": 86400,
                },
                "css_selector": {
                    "type": "string",
                    "description": "CSS selector to scope scrape, default body",
                    "default": "",
                },
            },
            "required": ["imessage_recipient", "target_url"],
        },
    },
}


def create_competitor_monitor(
    imessage_recipient: str,
    target_url: str,
    spreadsheet_id: str = "",
    poll_interval_seconds: int = 86400,
    css_selector: str = "",
) -> str:
    from tools.create_watcher import create_watcher
    action_config = {
        "imessage_recipient": imessage_recipient,
        "target_url": target_url,
        "spreadsheet_id": spreadsheet_id,
    }
    return create_watcher(
        name=f"Monitor: {target_url[:40]}",
        trigger_type="schedule",
        trigger_config={"url": target_url},
        action_type="tool_call",
        action_config={
            "tool_name": "run_pipeline",
            "tool_args": {"pipeline": "competitor_monitor", **action_config},
            "notify_via": "notification",
        },
        schedule={"interval_seconds": poll_interval_seconds},
    )


def execute(imessage_recipient: str, target_url: str,
            spreadsheet_id: str = "") -> dict:
    import hashlib

    store_key = "_prev_{}".format(hashlib.md5(target_url.encode()).hexdigest()[:8])

    steps = [
        WebScrapeStep(url=target_url, output_key="current_content"),

        DiffCompareStep(new_key="current_content", store_key=store_key,
                        output_key="diff_text"),

        AiClassifyStep(
            input_key="diff_text",
            output_key="change_significance",
            categories=["major", "minor", "no change"],
        ),

        ConditionStep(
            key="change_significance",
            match_values=["major"],
            if_match="iMessageSendStep",
        ),

        iMessageSendStep(
            recipient=imessage_recipient,
            message=(
                "Major change detected on {}\n{}".format(
                    target_url[:50], "{diff_text}"
                )[:2000],
            ),
        ),

        TelegramSendStep(
            message="Change on {}:\nSignificance: {change_significance}\n{diff_text}",
        ),

        LogAppendStep(
            log_file="competitor_monitor.log",
            entry="{}: {change_significance}".format(target_url),
        ),

        NotificationStep(
            message="Competitor monitor: {change_significance} change on {}".format(target_url[:30]),
        ),
    ]

    if spreadsheet_id:
        steps.append(
            SheetAppendStep(
                spreadsheet_id=spreadsheet_id,
                row_values_key="change_significance",
                sheet_name="Changes",
            )
        )

    return run_pipeline(steps)


if __name__ == "__main__":
    import sys
    im = sys.argv[1] if len(sys.argv) > 1 else input("iMessage recipient: ")
    url = sys.argv[2] if len(sys.argv) > 2 else input("Target URL: ")
    result = execute(im, url)
    for s in result["steps"]:
        r = s["result"]
        status = "ok" if r.get("ok") else "FAIL"
        print(f"  {s['step']}: {status}")
