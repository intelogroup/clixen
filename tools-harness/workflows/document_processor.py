"""
Document Processor — 7-step pipeline.
1. Watch directory for new files
2. Detect file type (image, PDF, text)
3. If image: OCR
4. If PDF: extract text
5. AI summarize content
6. iMessage result to user
7. Log + notification
"""

import os

from workflows.pipeline import (
    FileWatchStep, OcrStep, AiSummarizeStep,
    iMessageSendStep, LogAppendStep, NotificationStep,
    run_pipeline, PipelineStep,
)

WATCHER_SCHEMA = {
    "type": "function",
    "function": {
        "name": "create_document_processor",
        "description": (
            "Create a 7-step document processor: watches a directory for new "
            "files, OCRs images, extracts PDF text, AI-summarizes, sends "
            "result via iMessage, logs everything."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "imessage_recipient": {
                    "type": "string",
                    "description": "iMessage recipient for summaries",
                },
                "watch_directory": {
                    "type": "string",
                    "description": "Directory to watch for new files",
                },
                "file_pattern": {
                    "type": "string",
                    "description": "File glob pattern, default '*'",
                    "default": "*",
                },
                "poll_interval_seconds": {
                    "type": "integer",
                    "description": "Poll interval, default 60 (1 min)",
                    "default": 60,
                },
            },
            "required": ["imessage_recipient", "watch_directory"],
        },
    },
}


def create_document_processor(
    imessage_recipient: str,
    watch_directory: str,
    file_pattern: str = "*",
    poll_interval_seconds: int = 60,
) -> str:
    from tools.create_watcher import create_watcher
    action_config = {
        "imessage_recipient": imessage_recipient,
        "watch_directory": watch_directory,
        "file_pattern": file_pattern,
    }
    return create_watcher(
        name=f"Doc Processor: {watch_directory}",
        trigger_type="schedule",
        trigger_config={"path": watch_directory},
        action_type="tool_call",
        action_config={
            "tool_name": "run_pipeline",
            "tool_args": {"pipeline": "document_processor", **action_config},
            "notify_via": "notification",
        },
        schedule={"interval_seconds": poll_interval_seconds},
    )


class DetectFileTypeStep(PipelineStep):
    def __init__(self, file_key: str, output_key: str = "file_type"):
        self.file_key = file_key
        self.output_key = output_key

    def run(self, ctx: dict) -> dict:
        files = ctx.get(self.file_key, [])
        if not files:
            return {"ok": False, "error": "no files to process"}
        f = files[0]
        ext = os.path.splitext(f)[1].lower()
        if ext in (".png", ".jpg", ".jpeg", ".gif", ".bmp", ".webp"):
            ftype = "image"
        elif ext in (".pdf",):
            ftype = "pdf"
        elif ext in (".txt", ".md", ".csv", ".json", ".yaml", ".yml", ".xml"):
            ftype = "text"
        else:
            ftype = "unknown"
        ctx[self.output_key] = ftype
        ctx["current_file"] = f
        return {"ok": True, "output": ftype}


class ReadTextFileStep(PipelineStep):
    def __init__(self, file_key: str, output_key: str = "file_text"):
        self.file_key = file_key
        self.output_key = output_key

    def run(self, ctx: dict) -> dict:
        f = ctx.get(self.file_key, "")
        if isinstance(f, list):
            f = f[0] if f else ""
        if not f or not os.path.isfile(f):
            return {"ok": False, "error": f"file not found: {f}"}
        try:
            with open(f, "r", errors="replace") as fh:
                text = fh.read(5000)
            ctx[self.output_key] = text
            return {"ok": True, "output": f"{len(text)} chars read"}
        except Exception as e:
            return {"ok": False, "error": str(e)}


def execute(imessage_recipient: str, watch_directory: str,
            file_pattern: str = "*") -> dict:
    steps = [
        FileWatchStep(directory=watch_directory, pattern=file_pattern,
                      output_key="new_files", max_files=1),

        DetectFileTypeStep(file_key="new_files"),
    ]
    return run_pipeline(steps)


def process_file(ctx: dict, imessage_recipient: str) -> dict:
    """Continue pipeline after file detection."""
    ftype = ctx.get("file_type", "")

    if ftype == "image":
        step = OcrStep(file_key="current_file", output_key="file_text")
        step.run(ctx)

    elif ftype == "pdf":
        import subprocess
        f = ctx.get("current_file", "")
        try:
            result = subprocess.run(
                ["python3", "-c", "import sys; from pdfminer.high_level import extract_text; print(extract_text(sys.argv[1]))", f],
                capture_output=True, text=True, timeout=30,
            )
            ctx["file_text"] = result.stdout[:5000] or result.stderr[:500]
        except Exception as e:
            ctx["file_text"] = f"[pdf error] {e}"

    elif ftype == "text":
        step = ReadTextFileStep(file_key="current_file", output_key="file_text")
        step.run(ctx)

    else:
        ctx["file_text"] = f"Unknown file type: {ftype}"

    summary_step = AiSummarizeStep(
        input_key="file_text",
        output_key="doc_summary",
        instruction="Summarize this document's content. What are the key points?",
    )
    summary_step.run(ctx)

    imsg = iMessageSendStep(
        recipient=imessage_recipient,
        message="New doc processed: {doc_summary}",
    )
    imsg.run(ctx)

    LogAppendStep(
        log_file="document_processor.log",
        entry="Processed {current_file}: {ftype} -> summarized",
    ).run(ctx)

    NotificationStep(
        message="Document processed: {current_file}",
        level="info",
    ).run(ctx)

    return {"context": ctx, "done": True}


if __name__ == "__main__":
    import sys
    im = sys.argv[1] if len(sys.argv) > 1 else input("iMessage recipient: ")
    watch = sys.argv[2] if len(sys.argv) > 2 else input("Watch directory: ")
    r1 = execute(im, watch)
    if r1["context"].get("new_files"):
        r2 = process_file(r1["context"], im)
        for s in r2.get("steps", r1["steps"]):
            pass
        print(f"Processed: {r1['context'].get('current_file')}")
    else:
        print("No new files")
