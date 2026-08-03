"""Resize, crop, rotate, or convert image files (PNG/JPG/SVG/etc.).

Wraps two already-installed local binaries — no new dependencies:
- ImageMagick (`magick`)  — raster ops (resize/crop/rotate/convert)
- rsvg-convert (librsvg)  — SVG input, since ImageMagick's SVG delegate is
  unreliable across installs; rsvg-convert is the same tool the diagram_render
  PNG-export fix uses, so behavior stays consistent between the two tools.

The agent picks an operation; this tool shells out and returns the output
path (or an error string, never raises).
"""

from __future__ import annotations

import os
import subprocess


EDIT_IMAGE_SCHEMA = {
    "type": "function",
    "function": {
        "name": "edit_image",
        "description": (
            "Resize, crop, rotate, or convert an image file (PNG/JPG/SVG/etc.) to "
            "another format. Wraps ImageMagick (raster) and rsvg-convert (SVG input). "
            "Returns the output file path."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "path": {
                    "type": "string",
                    "description": "Path to the input image file.",
                },
                "operation": {
                    "type": "string",
                    "enum": ["resize", "crop", "rotate", "convert"],
                    "description": "resize needs width/height. crop needs crop_width/"
                                    "crop_height (crop_x/crop_y default 0). rotate needs "
                                    "degrees. convert just re-encodes to output_path's extension.",
                },
                "output_path": {
                    "type": "string",
                    "description": "Where to write the result. Defaults to overwriting 'path' "
                                    "(required for convert, since the extension picks the format).",
                },
                "width": {"type": "integer", "description": "resize: target width in px."},
                "height": {"type": "integer", "description": "resize: target height in px "
                           "(omit either width or height to preserve aspect ratio)."},
                "crop_width": {"type": "integer", "description": "crop: box width in px."},
                "crop_height": {"type": "integer", "description": "crop: box height in px."},
                "crop_x": {"type": "integer", "description": "crop: box left offset in px (default 0)."},
                "crop_y": {"type": "integer", "description": "crop: box top offset in px (default 0)."},
                "degrees": {"type": "number", "description": "rotate: degrees clockwise."},
            },
            "required": ["path", "operation"],
        },
    },
}


def _run(cmd: list[str]) -> None:
    subprocess.run(cmd, check=True, capture_output=True, text=True)


def _rasterize_svg(src: str, out_hint: str) -> str:
    """rsvg-convert an SVG to a temp PNG for ops ImageMagick would otherwise
    have to render the SVG for itself (crop/rotate) — caller must unlink it."""
    tmp = out_hint + ".rasterized.png"
    _run(["rsvg-convert", src, "-o", tmp])
    return tmp


def edit_image_executor(args: dict) -> str:
    path = args.get("path", "")
    if not path.strip():
        return "[edit_image] error: 'path' is required"
    src = os.path.abspath(os.path.expanduser(path))
    if not os.path.exists(src):
        return f"[edit_image] error: input file not found: {src}"

    operation = (args.get("operation") or "").lower()
    if operation not in ("resize", "crop", "rotate", "convert"):
        return f"[edit_image] error: unsupported operation '{operation}' (use resize/crop/rotate/convert)"

    out = os.path.abspath(os.path.expanduser(args.get("output_path") or src))
    os.makedirs(os.path.dirname(out) or ".", exist_ok=True)
    is_svg = src.lower().endswith(".svg")
    raster_tmp = None

    try:
        if operation == "resize":
            width, height = args.get("width"), args.get("height")
            if not width and not height:
                return "[edit_image] error: resize needs 'width' and/or 'height'"
            if is_svg:
                cmd = ["rsvg-convert", src, "-o", out]
                if width:
                    cmd += ["-w", str(int(width))]
                if height:
                    cmd += ["-h", str(int(height))]
                _run(cmd)
            else:
                geometry = f"{int(width) if width else ''}x{int(height) if height else ''}"
                _run(["magick", src, "-resize", geometry, out])

        elif operation == "crop":
            crop_width, crop_height = args.get("crop_width"), args.get("crop_height")
            if not crop_width or not crop_height:
                return "[edit_image] error: crop needs 'crop_width' and 'crop_height'"
            crop_x, crop_y = int(args.get("crop_x", 0)), int(args.get("crop_y", 0))
            raster_src = src
            if is_svg:
                raster_tmp = raster_src = _rasterize_svg(src, out)
            geometry = f"{int(crop_width)}x{int(crop_height)}+{crop_x}+{crop_y}"
            _run(["magick", raster_src, "-crop", geometry, "+repage", out])

        elif operation == "rotate":
            degrees = args.get("degrees")
            if degrees is None:
                return "[edit_image] error: rotate needs 'degrees'"
            raster_src = src
            if is_svg:
                raster_tmp = raster_src = _rasterize_svg(src, out)
            _run(["magick", raster_src, "-rotate", str(degrees), out])

        else:  # convert
            if is_svg:
                out_ext = (os.path.splitext(out)[1].lstrip(".") or "png").lower()
                if out_ext == "svg":
                    return "[edit_image] error: SVG-to-SVG convert is a no-op — use copy_file instead"
                _run(["rsvg-convert", "-f", out_ext, src, "-o", out])
            else:
                _run(["magick", src, out])

    except FileNotFoundError as e:
        return (f"[edit_image] error: required tool not installed "
                f"(brew install imagemagick librsvg). {e}")
    except subprocess.CalledProcessError as e:
        err = e.stderr if isinstance(e.stderr, str) else (e.stderr or b"").decode("utf-8", "replace")
        return f"[edit_image] {operation} failed: {err[:800]}"
    finally:
        if raster_tmp and os.path.exists(raster_tmp):
            os.unlink(raster_tmp)

    if not os.path.exists(out) or os.path.getsize(out) == 0:
        return f"[edit_image] error: output not produced at {out}"
    return out
