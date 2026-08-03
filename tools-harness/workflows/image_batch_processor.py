"""
Image Batch Processor — 7-step file-heavy pipeline.
1. Watch input folder for new images
2. List files + filter by extension
3. Copy each to processing dir
4. Resize each (batch)
5. Convert format (PNG/JPG/WebP)
6. iMessage summary with file list
7. Log + notification
"""

import os

from workflows.pipeline import (
    FileWatchStep, MkdirStep,
    iMessageSendStep, LogAppendStep, NotificationStep,
    run_pipeline, PipelineStep,
)

WATCHER_SCHEMA = {
    "type": "function",
    "function": {
        "name": "create_image_batch_processor",
        "description": (
            "Create a 7-step image batch processor: watches a folder for new "
            "images, resizes each, converts format, copies to output, "
            "sends iMessage summary."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "imessage_recipient": {
                    "type": "string",
                    "description": "iMessage recipient for summary",
                },
                "input_dir": {
                    "type": "string",
                    "description": "Input directory to watch",
                    "default": "~/Downloads/images_in",
                },
                "output_dir": {
                    "type": "string",
                    "description": "Output directory for processed images",
                    "default": "~/Downloads/images_out",
                },
                "resize_arg": {
                    "type": "string",
                    "description": "ImageMagick resize arg, default '800x600>'",
                    "default": "800x600>",
                },
                "output_format": {
                    "type": "string",
                    "description": "Target format: png, jpg, webp (default: keep original)",
                    "default": "",
                },
                "file_pattern": {
                    "type": "string",
                    "description": "Glob pattern, default '*.{png,jpg,jpeg,webp}'",
                    "default": "*.{png,jpg,jpeg,webp}",
                },
            },
            "required": ["imessage_recipient"],
        },
    },
}


def create_image_batch_processor(
    imessage_recipient: str,
    input_dir: str = "~/Downloads/images_in",
    output_dir: str = "~/Downloads/images_out",
    resize_arg: str = "800x600>",
    output_format: str = "",
    file_pattern: str = "*.{png,jpg,jpeg,webp}",
) -> str:
    from tools.create_watcher import create_watcher
    return create_watcher(
        name="Image Batch Processor",
        trigger_type="schedule",
        trigger_config={"path": input_dir, "interval_seconds": 60},
        action_type="tool_call",
        action_config={
            "tool_name": "run_pipeline",
            "tool_args": {
                "pipeline": "image_batch_processor",
                "imessage_recipient": imessage_recipient,
                "input_dir": input_dir,
                "output_dir": output_dir,
                "resize_arg": resize_arg,
                "output_format": output_format,
                "file_pattern": file_pattern,
            },
            "notify_via": "notification",
        },
        schedule={"interval_seconds": 60},
    )


class BatchProcessImagesStep(PipelineStep):
    """Discover new images, process each through magick."""

    def __init__(self, input_key: str, output_dir: str,
                 resize_arg: str, output_format: str,
                 output_key: str = "processed_images"):
        self.input_key = input_key
        self.output_dir = output_dir
        self.resize_arg = resize_arg
        self.output_format = output_format
        self.output_key = output_key

    def run(self, ctx: dict) -> dict:
        import subprocess
        import os
        new_files = ctx.get(self.input_key, [])
        out_dir = os.path.expanduser(self.output_dir)
        os.makedirs(out_dir, exist_ok=True)

        results = []
        for f in new_files:
            base = os.path.basename(f)
            name, ext = os.path.splitext(base)
            out_ext = self.output_format if self.output_format else ext.lstrip(".")
            out_path = os.path.join(out_dir, f"{name}_processed.{out_ext}")
            try:
                subprocess.run(["magick", f, "-resize", self.resize_arg, out_path],
                               check=True, capture_output=True, timeout=30)
                results.append(out_path)
            except Exception as e:
                results.append(f"{f}: error {e}")

        ctx[self.output_key] = results
        return {"ok": True, "output": f"{len(results)} images processed"}


def execute(imessage_recipient: str, input_dir: str = "~/Downloads/images_in",
            output_dir: str = "~/Downloads/images_out",
            resize_arg: str = "800x600>",
            output_format: str = "",
            file_pattern: str = "*.{png,jpg,jpeg,webp}") -> dict:
    steps = [
        FileWatchStep(directory=input_dir, pattern=file_pattern,
                      output_key="new_images", max_files=20),

        MkdirStep(path=output_dir),

        BatchProcessImagesStep(
            input_key="new_images",
            output_dir=output_dir,
            resize_arg=resize_arg,
            output_format=output_format,
        ),

        iMessageSendStep(
            recipient=imessage_recipient,
            message="Batch processing done: {processed_images}",
        ),

        LogAppendStep(
            log_file="image_batch.log",
            entry="Batch processed {} images -> {}".format("{total}", output_dir),
        ),

        NotificationStep(
            message="Image batch processed",
            level="info",
        ),
    ]

    result = run_pipeline(steps)
    # Re-run log with actual count
    processed = result["context"].get("processed_images", [])
    if processed:
        log = LogAppendStep(
            log_file="image_batch.log",
            entry="Batch processed {} images -> {}".format(len(processed), output_dir),
        )
        log.run(result["context"])
    return result


if __name__ == "__main__":
    im = input("iMessage recipient: ")
    result = execute(im)
    for s in result["steps"]:
        r = s["result"]
        status = "ok" if r.get("ok") else "FAIL"
        print(f"  {s['step']}: {status}")
