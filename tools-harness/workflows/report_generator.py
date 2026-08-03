"""
Report Generator — 9-step file-heavy pipeline.
1. Scrape source URL for data
2. AI write report from data
3. Save report as .md file
4. Convert .md to .html
5. Convert .html to .pdf (wkhtmltopdf)
6. Copy PDF to archive
7. iMessage with file paths
8. Email PDF as notification
9. Log + notification
"""

import os
from datetime import datetime

from workflows.pipeline import (
    WebScrapeStep, AiGenerateStep, WriteFileStep, CopyFileStep,
    iMessageSendStep, EmailSendStep, LogAppendStep, NotificationStep,
    MkdirStep, run_pipeline, PipelineStep,
)

WATCHER_SCHEMA = {
    "type": "function",
    "function": {
        "name": "create_report_generator",
        "description": (
            "Create a 9-step report generator: scrapes a URL for source data, "
            "AI writes a report, saves .md + converts to .html + .pdf, "
            "sends iMessage with paths, emails PDF, logs everything."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "imessage_recipient": {
                    "type": "string",
                    "description": "iMessage recipient with report paths",
                },
                "email_recipient": {
                    "type": "string",
                    "description": "Email PDF recipient",
                },
                "source_url": {
                    "type": "string",
                    "description": "URL to scrape for report data",
                },
                "topic": {
                    "type": "string",
                    "description": "Report topic, default 'summary of findings'",
                    "default": "summary of findings",
                },
                "output_dir": {
                    "type": "string",
                    "description": "Output directory, default ~/Downloads/reports",
                    "default": "~/Downloads/reports",
                },
            },
            "required": ["imessage_recipient", "email_recipient", "source_url"],
        },
    },
}


def create_report_generator(
    imessage_recipient: str,
    email_recipient: str,
    source_url: str,
    topic: str = "summary of findings",
    output_dir: str = "~/Downloads/reports",
) -> str:
    from tools.create_watcher import create_watcher
    return create_watcher(
        name="Report Generator",
        trigger_type="webhook",
        trigger_config={"secret": "report-gen"},
        action_type="tool_call",
        action_config={
            "tool_name": "run_pipeline",
            "tool_args": {
                "pipeline": "report_generator",
                "imessage_recipient": imessage_recipient,
                "email_recipient": email_recipient,
                "source_url": source_url,
                "topic": topic,
                "output_dir": output_dir,
            },
            "notify_via": "notification",
        },
    )


class MdToHtmlStep(PipelineStep):
    """Convert markdown string to HTML."""

    def __init__(self, md_key: str, output_key: str = "html_output"):
        self.md_key = md_key
        self.output_key = output_key

    def run(self, ctx: dict) -> dict:
        md = ctx.get(self.md_key, "")
        try:
            import markdown
            html = markdown.markdown(md, extensions=["fenced_code", "tables"])
            ctx[self.output_key] = html
            return {"ok": True, "output": f"HTML: {len(html)} chars"}
        except Exception as e:
            return {"ok": False, "error": str(e)}


class HtmlToPdfStep(PipelineStep):
    """Convert HTML string to PDF via wkhtmltopdf."""

    def __init__(self, html_key: str, output_path: str, output_key: str = "pdf_path"):
        self.html_key = html_key
        self.output_path = output_path
        self.output_key = output_key

    def run(self, ctx: dict) -> dict:
        import tempfile
        import subprocess
        html = ctx.get(self.html_key, "")
        tmp = tempfile.NamedTemporaryFile(suffix=".html", delete=False, mode="w")
        tmp.write(html)
        tmp_path = tmp.name
        tmp.close()
        try:
            subprocess.run(
                ["wkhtmltopdf", "--enable-local-file-access", tmp_path, self.output_path],
                check=True, capture_output=True, timeout=60,
            )
            ctx[self.output_key] = self.output_path
            return {"ok": True, "output": f"PDF saved: {self.output_path}"}
        except FileNotFoundError:
            return {"ok": False, "error": "wkhtmltopdf not installed (brew install wkhtmltopdf)"}
        except Exception as e:
            return {"ok": False, "error": str(e)}


def execute(imessage_recipient: str, email_recipient: str,
            source_url: str, topic: str = "summary of findings",
            output_dir: str = "~/Downloads/reports") -> dict:
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    slug = "report_" + ts
    out_dir = os.path.expanduser(output_dir)
    md_path = os.path.join(out_dir, slug + ".md")
    html_path = os.path.join(out_dir, slug + ".html")
    pdf_path = os.path.join(out_dir, slug + ".pdf")
    archive_dir = os.path.join(out_dir, "archive")

    steps = [
        # 1. Scrape source
        WebScrapeStep(url=source_url, output_key="source_data"),

        # 2. AI write report
        AiGenerateStep(
            prompt="Write a structured report on {}. Use the data below.\n\n{}".format(
                topic, "{source_data}"),
            output_key="report_text",
            max_tokens=2000,
        ),

        # 3. Save .md
        MkdirStep(path=out_dir),
        WriteFileStep(path=md_path, content_key="report_text"),

        # 4. Convert .md -> .html
        MdToHtmlStep(md_key="report_text"),

        # 5. Save .html and convert to .pdf
        WriteFileStep(path=html_path, content_key="html_output"),
        HtmlToPdfStep(html_key="html_output", output_path=pdf_path),

        # 6. Copy PDF to archive
        MkdirStep(path=archive_dir),
        CopyFileStep(src=pdf_path, dest=os.path.join(archive_dir, slug + ".pdf")),

        # 7. iMessage with paths
        iMessageSendStep(
            recipient=imessage_recipient,
            message="Report ready!\n.md: {}\n.html: {}\n.pdf: {}".format(md_path, html_path, pdf_path),
        ),

        # 8. Email PDF notification
        EmailSendStep(
            to=email_recipient,
            subject="Report: {}".format(topic),
            body="Report generated from {}\n\nFiles:\n{}\n{}\n{}".format(source_url, md_path, html_path, pdf_path),
        ),

        # 9. Log + notification
        LogAppendStep(log_file="report_generator.log", entry="Report: {} ({})".format(slug, source_url)),
        NotificationStep(message="Report generated: {}".format(slug), level="info"),
    ]
    return run_pipeline(steps)


if __name__ == "__main__":
    import sys
    im = sys.argv[1] if len(sys.argv) > 1 else input("iMessage recipient: ")
    em = sys.argv[2] if len(sys.argv) > 2 else input("Email recipient: ")
    url = sys.argv[3] if len(sys.argv) > 3 else input("Source URL: ")
    result = execute(im, em, url)
    for s in result["steps"]:
        r = s["result"]
        status = "ok" if r.get("ok") else "FAIL"
        print(f"  {s['step']}: {status}")
