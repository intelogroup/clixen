"""Make the worker-comfyui handler return 3D outputs, not just images.

The stock handler walks each node's output and collects only the "images" key;
anything else is logged as an unhandled output key and dropped. Hy3D21ExportMesh
publishes its GLB under "3d", using the same {filename, subfolder, type} shape
that the image branch already knows how to fetch from /view. So rather than
forking a 900-line handler, fold "3d" entries into "images" just before that
loop reads them, and let the existing download-and-encode path do the work.

The GLB comes back as a base64 entry in the response's "images" list. That name
is inherited from the handler and is not worth renaming here.

Run at image build time. Fails loudly if upstream changes the anchor line, so a
handler rewrite surfaces as a failed build rather than an endpoint that silently
returns nothing.
"""

import sys

HANDLER = "/comfyui/handler.py"
ANCHOR = '        for node_id, node_output in outputs.items():\n'
INJECT = (
    "            # Hunyuan3D publishes its mesh under '3d'; reuse the image path.\n"
    "            if node_output.get(\"3d\"):\n"
    "                node_output.setdefault(\"images\", []).extend(node_output[\"3d\"])\n"
)

src = open(HANDLER).read()

if INJECT in src:
    print("patch_handler: already applied")
    sys.exit(0)

if src.count(ANCHOR) != 1:
    sys.exit(
        f"patch_handler: expected exactly 1 anchor in {HANDLER}, "
        f"found {src.count(ANCHOR)} — upstream handler changed, re-derive the patch"
    )

open(HANDLER, "w").write(src.replace(ANCHOR, ANCHOR + INJECT))
print("patch_handler: ok")
