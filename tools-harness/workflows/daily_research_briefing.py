"""
Daily Research Briefing — 8-step pipeline.
1. Web scrape 3 topic URLs
2. AI summarize each
3. AI compile unified digest
4. iMessage teaser (2-line)
5. Telegram full digest
6. Email detailed report
7. Log to file
8. In-app notification
"""

from workflows.pipeline import (
    WebScrapeStep, AiSummarizeStep, AiGenerateStep,
    iMessageSendStep, TelegramSendStep, EmailSendStep,
    LogAppendStep, NotificationStep, run_pipeline,
)

WATCHER_SCHEMA = {
    "type": "function",
    "function": {
        "name": "create_daily_research_briefing",
        "description": (
            "Create an 8-step research briefing: scrapes 3 topic URLs, "
            "AI-summarizes each, compiles a unified digest, sends iMessage "
            "teaser + Telegram full + email detailed report + log."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "imessage_recipient": {
                    "type": "string",
                    "description": "iMessage recipient for teaser",
                },
                "email_recipient": {
                    "type": "string",
                    "description": "Email recipient for detailed report",
                },
                "topic_urls": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "3-5 URLs to scrape and summarize",
                },
                "hour": {
                    "type": "integer",
                    "description": "Hour to run (0-23), default 8",
                    "default": 8,
                },
                "telegram_chat_id": {
                    "type": "string",
                    "description": "Telegram chat ID for full digest, default None",
                },
            },
            "required": ["imessage_recipient", "email_recipient", "topic_urls"],
        },
    },
}


def create_daily_research_briefing(
    imessage_recipient: str,
    email_recipient: str,
    topic_urls: list[str],
    hour: int = 8,
    telegram_chat_id: str = None,
) -> str:
    from tools.create_watcher import create_watcher
    action_config = {
        "imessage_recipient": imessage_recipient,
        "email_recipient": email_recipient,
        "topic_urls": topic_urls,
    }
    return create_watcher(
        name=f"Research Briefing ({hour}:00)",
        trigger_type="schedule",
        trigger_config={"hour": hour, "minute": 0},
        action_type="tool_call",
        action_config={
            "tool_name": "run_pipeline",
            "tool_args": {"pipeline": "daily_research_briefing", **action_config},
            "notify_via": "notification",
        },
        schedule={"interval_seconds": 3600},
    )


def execute(imessage_recipient: str, email_recipient: str,
            topic_urls: list[str]) -> dict:
    steps = []
    # Steps 1-2: scrape + summarize each URL
    summary_keys = []
    for i, url in enumerate(topic_urls[:5]):
        sk = f"scrape_{i}"
        ak = f"summary_{i}"
        summary_keys.append(ak)
        steps.append(WebScrapeStep(url=url, output_key=sk))
        steps.append(AiSummarizeStep(
            input_key=sk, output_key=ak,
            instruction="Summarize key findings, stats, and takeaways.",
        ))

    # Step 3: compile all summaries into unified digest via CustomStep
    class _CompileDigestStep(PipelineStep):
        def __init__(self, summary_keys: list[str]):
            super().__init__()
            self.summary_keys = summary_keys
        def run(self, ctx: dict) -> dict:
            parts = []
            for k in self.summary_keys:
                v = ctx.get(k, "")
                if v:
                    parts.append(v)
            if not parts:
                return {"ok": False, "error": "no summaries to compile"}
            prompt = (
                "Compile these topic summaries into a unified research digest. "
                "Group related themes. Start with an overview paragraph.\n\n"
                + "\n---\n".join(parts)
            )
            from workflows.pipeline import _call_ai
            text = _call_ai(prompt, model=None, max_tokens=800)
            ctx["unified_digest"] = text
            return {"ok": True, "output": text[:80]}
    steps.append(_CompileDigestStep(summary_keys=summary_keys))

    # Step 4: iMessage teaser
    steps.append(iMessageSendStep(
        recipient=imessage_recipient,
        message="Research digest ready — full report sent to email.",
    ))

    # Step 5: Telegram full digest
    steps.append(TelegramSendStep(
        message="{unified_digest}",
    ))

    # Step 6: Email detailed report
    steps.append(EmailSendStep(
        to=email_recipient,
        subject="Daily Research Briefing",
        body_key="unified_digest",
    ))

    # Step 7: Log
    steps.append(LogAppendStep(
        log_file="research_briefing.log",
        entry="{unified_digest}",
    ))

    # Step 8: Notification
    steps.append(NotificationStep(
        message="Research briefing complete: {} topics".format(len(topic_urls)),
    ))

    return run_pipeline(steps)


if __name__ == "__main__":
    import sys
    im = sys.argv[1] if len(sys.argv) > 1 else input("iMessage recipient: ")
    em = sys.argv[2] if len(sys.argv) > 2 else input("Email recipient: ")
    urls = sys.argv[3].split(",") if len(sys.argv) > 3 else [
        "https://news.ycombinator.com",
        "https://arxiv.org",
    ]
    result = execute(im, em, urls)
    for s in result["steps"]:
        r = s["result"]
        status = "ok" if r.get("ok") else "FAIL"
        print(f"  {s['step']}: {status}")
