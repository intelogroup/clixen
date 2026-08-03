"""
SVG Poster Generator — 8-step file-heavy pipeline.
1. AI generate SVG from prompt
2. Write SVG to file
3. Convert SVG to PNG
4. Create thumbnail (smaller PNG)
5. Copy to output folder
6. iMessage with file paths
7. Log
8. Notification
"""

import os
from datetime import datetime

from workflows.pipeline import (
    SvgGenerateStep, WriteFileStep, SvgToPngStep, ImageMagickStep,
    iMessageSendStep, LogAppendStep, NotificationStep,
    MkdirStep, run_pipeline,
)

WATCHER_SCHEMA = {
    "type": "function",
    "function": {
        "name": "create_svg_poster_generator",
        "description": (
            "Create an 8-step SVG poster generator: AI generates an SVG poster "
            "from a prompt, saves SVG + PNG, creates thumbnail, copies to output "
            "folder, sends paths via iMessage, logs everything."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "imessage_recipient": {
                    "type": "string",
                    "description": "iMessage recipient with file paths",
                },
                "prompt": {
                    "type": "string",
                    "description": "Visual description for the poster design",
                },
                "output_dir": {
                    "type": "string",
                    "description": "Output directory, default ~/Downloads/posters",
                    "default": "~/Downloads/posters",
                },
                "width": {
                    "type": "integer",
                    "description": "SVG width, default 800",
                    "default": 800,
                },
                "height": {
                    "type": "integer",
                    "description": "SVG height, default 600",
                    "default": 600,
                },
            },
            "required": ["imessage_recipient", "prompt"],
        },
    },
}


def create_svg_poster_generator(
    imessage_recipient: str,
    prompt: str,
    output_dir: str = "~/Downloads/posters",
    width: int = 800,
    height: int = 600,
) -> str:
    from tools.create_watcher import create_watcher
    return create_watcher(
        name="SVG Poster Generator",
        trigger_type="webhook",
        trigger_config={"secret": "poster-gen"},
        action_type="tool_call",
        action_config={
            "tool_name": "run_pipeline",
            "tool_args": {
                "pipeline": "svg_poster_generator",
                "imessage_recipient": imessage_recipient,
                "prompt": prompt,
                "output_dir": output_dir,
                "width": width,
                "height": height,
            },
            "notify_via": "notification",
        },
    )


def execute(imessage_recipient: str, prompt: str,
            output_dir: str = "~/Downloads/posters",
            width: int = 800, height: int = 600) -> dict:
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    out_dir = os.path.expanduser(output_dir)
    slug = "poster_" + ts
    svg_path = os.path.join(out_dir, slug + ".svg")
    png_path = os.path.join(out_dir, slug + ".png")
    thumb_path = os.path.join(out_dir, slug + "_thumb.png")

    steps = [
        # 1. AI generate SVG
        SvgGenerateStep(prompt=prompt, output_key="svg_code", width=width, height=height),

        # 2. Make output dir + write SVG
        MkdirStep(path=out_dir),
        WriteFileStep(path=svg_path, content_key="svg_code"),

        # 3. Convert SVG to PNG
        SvgToPngStep(svg_path_key="write_file_path", output_path=png_path, output_key="png_path"),

        # 4. Create thumbnail
        ImageMagickStep(
            src_key="png_path",
            operation="thumbnail",
            arg="200x150",
            output_path=thumb_path,
            output_key="thumb_path",
        ),

        # 5. Copy to output was already done inline

        # 6. iMessage with paths
        iMessageSendStep(
            recipient=imessage_recipient,
            message="Poster ready!\nSVG: {}\nPNG: {}\nThumb: {}".format(svg_path, png_path, thumb_path),
        ),

        # 7. Log
        LogAppendStep(
            log_file="svg_poster.log",
            entry="Poster: {} ({}x{})".format(slug, width, height),
        ),

        # 8. Notification
        NotificationStep(
            message="Poster generated: {}".format(svg_path),
            level="info",
        ),
    ]
    return run_pipeline(steps)


if __name__ == "__main__":
    import sys
    im = sys.argv[1] if len(sys.argv) > 1 else input("iMessage recipient: ")
    p = sys.argv[2] if len(sys.argv) > 2 else input("Poster prompt: ")
    result = execute(im, p)
    for s in result["steps"]:
        r = s["result"]
        status = "ok" if r.get("ok") else "FAIL"
        print(f"  {s['step']}: {status}  {str(r.get('output', r.get('error', '')))[:60]}")
