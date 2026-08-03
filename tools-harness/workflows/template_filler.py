"""
Template Filler — 8-step file-heavy pipeline.
1. Read template file from disk
2. Fetch data from URL (JSON/CSV)
3. AI transform/fill template with data
4. Save filled output as new file
5. Copy to distribution folder
6. iMessage with file path
7. Log
8. Notification
"""

import os
from datetime import datetime

from workflows.pipeline import (
    ReadFileStep, WebScrapeStep, AiGenerateStep,
    WriteFileStep, CopyFileStep, MkdirStep,
    iMessageSendStep, LogAppendStep, NotificationStep,
    run_pipeline, PipelineStep,
)

WATCHER_SCHEMA = {
    "type": "function",
    "function": {
        "name": "create_template_filler",
        "description": (
            "Create an 8-step template filler: reads a template file, fetches "
            "data from a URL, AI-fills the template with the data, saves "
            "output, copies to dist folder, sends iMessage notification."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "imessage_recipient": {
                    "type": "string",
                    "description": "iMessage recipient with file path",
                },
                "template_path": {
                    "type": "string",
                    "description": "Path to template file (txt/html/md/svg)",
                },
                "data_url": {
                    "type": "string",
                    "description": "URL to fetch fill data from (JSON/CSV)",
                },
                "output_dir": {
                    "type": "string",
                    "description": "Output directory, default ~/Downloads/filled",
                    "default": "~/Downloads/filled",
                },
                "instruction": {
                    "type": "string",
                    "description": "AI instruction for filling, e.g. 'Replace {{name}} with actual name'",
                    "default": "Fill the template with the fetched data. Replace placeholders like {{name}} with real values.",
                },
            },
            "required": ["imessage_recipient", "template_path", "data_url"],
        },
    },
}


def create_template_filler(
    imessage_recipient: str,
    template_path: str,
    data_url: str,
    output_dir: str = "~/Downloads/filled",
    instruction: str = None,
) -> str:
    from tools.create_watcher import create_watcher
    return create_watcher(
        name="Template Filler",
        trigger_type="webhook",
        trigger_config={"secret": "template-fill"},
        action_type="tool_call",
        action_config={
            "tool_name": "run_pipeline",
            "tool_args": {
                "pipeline": "template_filler",
                "imessage_recipient": imessage_recipient,
                "template_path": template_path,
                "data_url": data_url,
                "output_dir": output_dir,
                "instruction": instruction or "Fill template with fetched data. Replace {{name}}-style placeholders.",
            },
            "notify_via": "notification",
        },
    )


class DataFetchStep(PipelineStep):
    """Fetch JSON/CSV/text data from URL."""

    def __init__(self, url: str, output_key: str = "fetched_data"):
        self.url = url
        self.output_key = output_key

    def run(self, ctx: dict) -> dict:
        import httpx
        try:
            resp = httpx.get(self.url, timeout=20)
            resp.raise_for_status()
            text = resp.text[:8000]
            ctx[self.output_key] = text
            return {"ok": True, "output": f"fetched {len(text)} chars from {self.url[:50]}"}
        except Exception as e:
            return {"ok": False, "error": str(e)}


class ExtractJsonStep(PipelineStep):
    """Try to parse JSON from fetched data, store as dict in context."""

    def __init__(self, input_key: str, output_key: str = "parsed_data"):
        self.input_key = input_key
        self.output_key = output_key

    def run(self, ctx: dict) -> dict:
        import json
        raw = ctx.get(self.input_key, "")
        try:
            data = json.loads(raw)
            ctx[self.output_key] = data
            return {"ok": True, "output": "JSON parsed: {} keys".format(
                len(data) if isinstance(data, dict) else len(data))}
        except json.JSONDecodeError:
            ctx[self.output_key] = {"raw": raw[:1000]}
            return {"ok": True, "output": "not JSON, stored as raw"}


class SaveOutputStep(PipelineStep):
    """Save filled content to output file. Auto-detect extension."""

    def __init__(self, content_key: str, output_dir: str,
                 template_path: str, output_key: str = "output_file_path"):
        self.content_key = content_key
        self.output_dir = output_dir
        self.template_path = template_path
        self.output_key = output_key

    def run(self, ctx: dict) -> dict:
        content = ctx.get(self.content_key, "")
        ts = datetime.now().strftime("%Y%m%d_%H%M%S")
        ext = os.path.splitext(self.template_path)[1] or ".txt"
        out_dir = os.path.expanduser(self.output_dir)
        os.makedirs(out_dir, exist_ok=True)
        out_path = os.path.join(out_dir, f"filled_{ts}{ext}")
        with open(out_path, "w") as f:
            f.write(content)
        ctx[self.output_key] = out_path
        return {"ok": True, "output": f"saved: {out_path}"}


def execute(imessage_recipient: str, template_path: str,
            data_url: str, output_dir: str = "~/Downloads/filled",
            instruction: str = None) -> dict:
    inst = instruction or "Fill template with fetched data. Replace {{name}}-style placeholders."
    out_dir = os.path.expanduser(output_dir)
    dist_dir = os.path.join(out_dir, "dist")

    steps = [
        # 1. Read template file
        ReadFileStep(path=os.path.expanduser(template_path), output_key="template_content"),

        # 2. Fetch data from URL
        DataFetchStep(url=data_url, output_key="fetched_data"),

        # 3. Parse JSON
        ExtractJsonStep(input_key="fetched_data", output_key="parsed_data"),

        # 4. AI: fill template with data
        AiGenerateStep(
            prompt="Template:\n{template_content}\n\nData:\n{fetched_data}\n\nInstruction: {}.\nReturn ONLY the filled template, no explanation.".format(inst),
            output_key="filled_output",
            max_tokens=2000,
        ),

        # 5. Save filled file
        MkdirStep(path=out_dir),
        SaveOutputStep(
            content_key="filled_output",
            output_dir=output_dir,
            template_path=template_path,
        ),

        # 6. Copy to dist
        MkdirStep(path=dist_dir),
        CopyFileStep(
            src_key="output_file_path",
            dest=os.path.join(dist_dir, os.path.basename(template_path)),
        ),

        # 7. iMessage
        iMessageSendStep(
            recipient=imessage_recipient,
            message="Template filled: {output_file_path}",
        ),

        # 8. Log
        LogAppendStep(
            log_file="template_filler.log",
            entry="Filled {} with {} -> {output_file_path}".format(template_path, data_url),
        ),
        NotificationStep(message="Template filled and saved", level="info"),
    ]
    return run_pipeline(steps)


if __name__ == "__main__":
    import sys
    im = sys.argv[1] if len(sys.argv) > 1 else input("iMessage recipient: ")
    tpl = sys.argv[2] if len(sys.argv) > 2 else input("Template path: ")
    url = sys.argv[3] if len(sys.argv) > 3 else input("Data URL: ")
    result = execute(im, tpl, url)
    for s in result["steps"]:
        r = s["result"]
        status = "ok" if r.get("ok") else "FAIL"
        print(f"  {s['step']}: {status}")
