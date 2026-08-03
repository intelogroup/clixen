"""Render text-described diagrams to image files using open-source engines.

Supports three OSS diagram-as-code engines (no paywall, no JS runtime needed
for output — images embed directly into PDFs/PPTX):
- d2       (Terrastruct, MPL-2.0; uses the free `dagre` layout, not the paid Tala)
- dot      (Graphviz, EPL)
- plantuml (GPL core)

The agent writes the diagram DSL; this tool shells out to the installed CLI and
returns the output image path (or an error string, never raises).
"""

from __future__ import annotations

import os
import shutil
import subprocess
import tempfile


def _which(cmd: str) -> str | None:
    return shutil.which(cmd)


RENDER_DIAGRAM_SCHEMA = {
    "type": "function",
    "function": {
        "name": "render_diagram",
        "description": (
            "Render a diagram-as-code source (D2 / Graphviz DOT / PlantUML) into an "
            "SVG or PNG image file. Open-source engines only (d2, dot, plantuml) — no "
            "paywall, output embeds into PDFs/PPTX. Returns the output file path."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "code": {
                    "type": "string",
                    "description": "Diagram source in the chosen engine's DSL.",
                },
                "engine": {
                    "type": "string",
                    "enum": ["d2", "dot", "plantuml"],
                    "description": "Rendering engine. Default 'd2'.",
                },
                "image_format": {
                    "type": "string",
                    "enum": ["svg", "png"],
                    "description": "Output image format. Default 'svg'.",
                },
                "output_path": {
                    "type": "string",
                    "description": (
                        "Where to write the image. Defaults to a temp file "
                        "(render_diagram.<fmt> in the system temp dir)."
                    ),
                },
            },
            "required": ["code"],
        },
    },
}


def _out_path(output_path: str, image_format: str) -> str:
    if output_path:
        return os.path.expanduser(output_path)
    return os.path.join(tempfile.gettempdir(), f"render_diagram.{image_format}")


def render_diagram_executor(args: dict) -> str:
    code = args.get("code", "")
    if not code.strip():
        return "[render_diagram] error: 'code' is required and non-empty"
    engine = (args.get("engine") or "d2").lower()
    image_format = (args.get("image_format") or "svg").lower()
    if image_format not in ("svg", "png"):
        return f"[render_diagram] error: unsupported image_format '{image_format}' (use svg/png)"
    if engine not in ("d2", "dot", "plantuml"):
        return f"[render_diagram] error: unsupported engine '{engine}' (use d2/dot/plantuml)"

    out = _out_path(args.get("output_path", ""), image_format)
    out = os.path.abspath(out)
    os.makedirs(os.path.dirname(out) or ".", exist_ok=True)

    try:
        if engine == "d2":
            # d2 infers format from the output extension. SVG uses d2's own
            # renderer (free dagre layout); PNG shells out to a
            # Playwright-managed headless Chrome to screenshot the SVG, and
            # Playwright's browser-download CDNs (playwright.azureedge.net
            # and its mirrors) have been 404ing — not fixable from our side.
            # Route PNG through SVG + a local SVG->PNG converter
            # (rsvg-convert, falling back to ImageMagick) instead, sidestepping
            # Playwright entirely.
            with tempfile.NamedTemporaryFile("w", suffix=".d2", delete=False) as f:
                f.write(code)
                src = f.name
            try:
                if image_format == "png":
                    svg_tmp = out + ".svg.tmp"
                    subprocess.run(["d2", src, svg_tmp], check=True, capture_output=True, text=True)
                    try:
                        if _which("rsvg-convert"):
                            subprocess.run(
                                ["rsvg-convert", svg_tmp, "-o", out],
                                check=True, capture_output=True, text=True,
                            )
                        elif _which("magick"):
                            subprocess.run(
                                ["magick", svg_tmp, out], check=True, capture_output=True, text=True,
                            )
                        else:
                            return ("[render_diagram] error: d2 PNG export needs rsvg-convert or "
                                    "ImageMagick (brew install librsvg), Playwright's browser "
                                    "download is unavailable")
                    finally:
                        if os.path.exists(svg_tmp):
                            os.unlink(svg_tmp)
                else:
                    subprocess.run(["d2", src, out], check=True, capture_output=True, text=True)
            finally:
                os.unlink(src)

        elif engine == "dot":
            with tempfile.NamedTemporaryFile("w", suffix=".dot", delete=False) as f:
                f.write(code)
                src = f.name
            try:
                # dot -T<fmt> in.dot -o out.fmt
                subprocess.run(
                    ["dot", f"-T{image_format}", src, "-o", out],
                    check=True, capture_output=True, text=True,
                )
            finally:
                os.unlink(src)

        else:  # plantuml: -pipe reads stdin, writes stdout (-t<fmt> selects format)
            res = subprocess.run(
                ["plantuml", f"-t{image_format}", "-pipe"],
                input=code.encode("utf-8"), capture_output=True, text=False,
            )
            if res.returncode != 0:
                return f"[render_diagram] plantuml error: {res.stderr.decode('utf-8', 'replace')[:500]}"
            with open(out, "wb") as f:
                f.write(res.stdout)

    except FileNotFoundError as e:
        return (f"[render_diagram] error: engine '{engine}' not installed "
                f"(brew install d2 graphviz plantuml). {e}")
    except subprocess.CalledProcessError as e:
        err = e.stderr.decode("utf-8", "replace") if isinstance(e.stderr, bytes) else (e.stderr or "")
        return f"[render_diagram] {engine} failed: {err[:800]}"

    if not os.path.exists(out) or os.path.getsize(out) == 0:
        return f"[render_diagram] error: output not produced at {out}"
    return out
