"""
File Organizer — 6-step file-heavy pipeline.
1. Watch folder for new files
2. Classify each file by extension/type
3. Create category subfolders
4. Move files into categorized folders
5. iMessage summary of what moved where
6. Log
"""

import os

from workflows.pipeline import (
    FileWatchStep, iMessageSendStep, LogAppendStep,
    run_pipeline, PipelineStep,
)

WATCHER_SCHEMA = {
    "type": "function",
    "function": {
        "name": "create_file_organizer",
        "description": (
            "Create a 6-step file organizer: watches a folder for new files, "
            "classifies by extension, creates category subfolders, moves each "
            "file, sends iMessage summary."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "imessage_recipient": {
                    "type": "string",
                    "description": "iMessage recipient for summary",
                },
                "watch_directory": {
                    "type": "string",
                    "description": "Directory to organize",
                    "default": "~/Downloads",
                },
                "file_pattern": {
                    "type": "string",
                    "description": "Glob pattern, default '*'",
                    "default": "*",
                },
                "category_map": {
                    "type": "object",
                    "description": "Extension->folder mapping as JSON, e.g. '{\"pdf\":\"documents\",\"png\":\"images\"}'",
                    "default": {},
                },
                "poll_interval_seconds": {
                    "type": "integer",
                    "description": "Poll interval, default 120",
                    "default": 120,
                },
            },
            "required": ["imessage_recipient"],
        },
    },
}

DEFAULT_CATEGORIES = {
    "pdf": "documents", "doc": "documents", "docx": "documents", "txt": "documents",
    "png": "images", "jpg": "images", "jpeg": "images", "gif": "images", "webp": "images", "svg": "images",
    "mp4": "video", "mov": "video", "avi": "video", "mkv": "video",
    "mp3": "audio", "wav": "audio", "flac": "audio",
    "zip": "archives", "tar": "archives", "gz": "archives", "rar": "archives", "7z": "archives",
    "py": "code", "js": "code", "ts": "code", "html": "code", "css": "code", "json": "code", "yaml": "code",
    "csv": "data", "xlsx": "data", "xls": "data",
    "dmg": "installers", "pkg": "installers", "app": "installers",
    "torrent": "torrents",
}


def create_file_organizer(
    imessage_recipient: str,
    watch_directory: str = "~/Downloads",
    file_pattern: str = "*",
    category_map: dict = None,
    poll_interval_seconds: int = 120,
) -> str:
    from tools.create_watcher import create_watcher
    return create_watcher(
        name=f"File Organizer: {watch_directory}",
        trigger_type="schedule",
        trigger_config={"path": watch_directory},
        action_type="tool_call",
        action_config={
            "tool_name": "run_pipeline",
            "tool_args": {
                "pipeline": "file_organizer",
                "imessage_recipient": imessage_recipient,
                "watch_directory": watch_directory,
                "file_pattern": file_pattern,
                "category_map": category_map or {},
            },
            "notify_via": "notification",
        },
        schedule={"interval_seconds": poll_interval_seconds},
    )


class ClassifyAndMoveFilesStep(PipelineStep):
    """Classify each new file by extension and move to category folder."""

    def __init__(self, files_key: str, watch_directory: str,
                 category_map: dict = None,
                 output_key: str = "organizer_summary"):
        self.files_key = files_key
        self.watch_directory = watch_directory
        self.category_map = category_map or {}
        self.output_key = output_key

    def run(self, ctx: dict) -> dict:
        import shutil
        watch = os.path.expanduser(self.watch_directory)
        new_files = ctx.get(self.files_key, [])
        cats = {**DEFAULT_CATEGORIES, **self.category_map}
        moved = []

        for f in new_files:
            ext = os.path.splitext(f)[1].lower().lstrip(".")
            cat = cats.get(ext, "other")
            dest_dir = os.path.join(watch, cat)
            os.makedirs(dest_dir, exist_ok=True)
            dest = os.path.join(dest_dir, os.path.basename(f))
            try:
                shutil.move(f, dest)
                moved.append((os.path.basename(f), cat))
            except Exception as e:
                moved.append((os.path.basename(f), f"error: {e}"))

        summary = "\n".join(f"  {name} -> {cat}" for name, cat in moved)
        ctx[self.output_key] = summary if summary else "no files moved"
        ctx["_moved_count"] = len(moved)
        return {"ok": True, "output": f"{len(moved)} files organized"}


def execute(imessage_recipient: str,
            watch_directory: str = "~/Downloads",
            file_pattern: str = "*",
            category_map: dict = None) -> dict:
    steps = [
        FileWatchStep(directory=watch_directory, pattern=file_pattern,
                      output_key="new_files", max_files=20),

        ClassifyAndMoveFilesStep(
            files_key="new_files",
            watch_directory=watch_directory,
            category_map=category_map or {},
        ),

        iMessageSendStep(
            recipient=imessage_recipient,
            message="Files organized:\n{organizer_summary}",
        ),

        LogAppendStep(
            log_file="file_organizer.log",
            entry="Organized {} files in {}".format("{_moved_count}", watch_directory),
        ),
    ]

    result = run_pipeline(steps)
    moved = result["context"].get("_moved_count", 0)
    if moved:
        log2 = LogAppendStep(
            log_file="file_organizer.log",
            entry="Organized {} files in {}".format(moved, watch_directory),
        )
        log2.run(result["context"])
    return result


if __name__ == "__main__":
    import sys
    im = sys.argv[1] if len(sys.argv) > 1 else input("iMessage recipient: ")
    watch = sys.argv[2] if len(sys.argv) > 2 else input("Watch directory (~/Downloads): ") or "~/Downloads"
    result = execute(im, watch)
    for s in result["steps"]:
        r = s["result"]
        status = "ok" if r.get("ok") else "FAIL"
        print(f"  {s['step']}: {status}")
