#!/usr/bin/env python3
"""Blender-side renderer for the Vox-style effect stack.

Run headless from vox_cli.py:

    blender -b -P render_blender.py -- <spec.json> <out_dir>

Builds a 3D scene (paper bg + text), then applies the analog effect stack in the
compositor, mirroring the Chris Moran techniques:

  * warm off-white paper, never pure white
  * halftone letterpress dots (multiply)
  * roughened text edges (alpha eroded by noise via ColorRamp threshold)
  * film grain (screen)
  * chromatic aberration (Lens Distortion dispersion)
  * vignette (radial multiply map)
  * optional camera-lens blur via Defocus blur-map (heavy; off by default)

Textures (halftone, grain, vignette, blur map) are generated procedurally with
numpy, seeded from the spec for determinism. Renders PNG frames at the spec
frame rate (default 12fps = "cutting on twos").
"""

import json
import os
import sys

import bpy
import numpy as np


def parse_args() -> tuple[str, str]:
    argv = sys.argv
    argv = argv[argv.index("--") + 1:] if "--" in argv else []
    if len(argv) < 2:
        raise SystemExit("usage: blender -b -P render_blender.py -- <spec.json> <out_dir>")
    return argv[0], argv[1]


def make_image(name: str, rgb: np.ndarray) -> bpy.types.Image:
    """Create an in-memory Image from a float [0,1] HxWx3 array."""
    h, w, _ = rgb.shape
    img = bpy.data.images.new(name, width=w, height=h)
    flat = np.zeros((h, w, 4), dtype=np.float32)
    flat[:, :, :3] = rgb[::-1]  # Blender stores pixels bottom-up
    flat[:, :, 3] = 1.0
    img.pixels[:] = flat.ravel().tolist()
    return img


def build_scene(spec: dict) -> None:
    scene = bpy.context.scene
    w, h, fps = spec["width"], spec["height"], spec["fps"]

    scene.render.engine = "BLENDER_EEVEE_NEXT" if hasattr(bpy.types, "EEVEE_NEXT") else "BLENDER_EEVEE"
    scene.render.resolution_x = w
    scene.render.resolution_y = h
    scene.render.fps = fps
    scene.render.image_settings.file_format = "PNG"
    scene.render.use_file_extension = True
    scene.frame_start = 1
    scene.frame_end = spec.get("duration_frames", fps * 2)

    # flat ambient world so text/paper render uniformly without lights
    world = bpy.data.worlds["World"]
    world.use_nodes = True
    bg = world.node_tree.nodes["Background"]
    bg.inputs[0].default_value = (0.9, 0.9, 0.9, 1.0)
    bg.inputs[1].default_value = 1.0

    bpy.ops.object.select_all(action="SELECT")
    bpy.ops.object.delete(use_global=False)

    # camera (orthographic, looking down -Z)
    bpy.ops.object.camera_add(location=(0, 0, 10))
    cam = bpy.context.object
    cam.data.type = "ORTHO"
    cam.data.ortho_scale = spec.get("ortho_scale", 8.0)
    scene.camera = cam

    # text card
    bpy.ops.object.text_add(location=(0, 0, 0))
    txt = bpy.context.object
    txt.data.body = spec.get("text", "VOX STYLE")
    txt.data.size = spec.get("text_size", 2.5)
    txt.data.align_x = "CENTER"
    txt.data.align_y = "CENTER"
    tmat = bpy.data.materials.new("text_mat")
    tmat.use_nodes = True
    tmat.node_tree.nodes["Principled BSDF"].inputs["Base Color"].default_value = (
        spec.get("text_color", [0.08, 0.08, 0.1, 1.0])
    )
    txt.data.materials.append(tmat)

    # paper plane
    bpy.ops.mesh.primitive_plane_add(size=20, location=(0, 0, -0.1))
    pmat = bpy.data.materials.new("paper_mat")
    pmat.use_nodes = True
    pmat.node_tree.nodes["Principled BSDF"].inputs["Base Color"].default_value = (
        spec.get("bg_color", [0.87, 0.82, 0.72, 1.0])
    )
    bpy.context.object.data.materials.append(pmat)


def build_textures(spec: dict) -> dict[str, bpy.types.Image]:
    w, h = spec["width"], spec["height"]
    seed = spec.get("seed", 1)
    rng = np.random.default_rng(seed)
    images: dict[str, bpy.types.Image] = {}

    # film grain — white-ish noise, screen blend
    grain = rng.random((h, w, 3), dtype=np.float32)
    images["grain"] = make_image("grain", grain)

    # halftone letterpress dots (dark dots on white map -> multiply)
    ht = spec.get("halftone", {})
    spacing = max(4.0, float(ht.get("scale", 20)))
    density = float(ht.get("density", 0.5))
    yy, xx = np.mgrid[0:h, 0:w].astype(np.float32)
    cy = (yy / spacing).astype(int)
    cx = (xx / spacing).astype(int)
    cell_rng = np.random.default_rng(seed + 1)
    ncells_y, ncells_x = h // int(spacing) + 2, w // int(spacing) + 2
    radii = cell_rng.random((ncells_y, ncells_x), dtype=np.float32)
    rad = radii[np.clip(cy, 0, ncells_y - 1), np.clip(cx, 0, ncells_x - 1)] * spacing * density
    cyc = (cy + 0.5) * spacing
    cxc = (cx + 0.5) * spacing
    dist = np.sqrt(((yy - cyc) / spacing) ** 2 + ((xx - cxc) / spacing) ** 2)
    dots = (dist < rad).astype(np.float32)
    halftone = 1.0 - dots
    opacity = float(ht.get("opacity", 0.35))
    halftone = 1.0 - (1.0 - halftone) * opacity  # bake opacity
    images["halftone"] = make_image("halftone", np.repeat(halftone[..., None], 3, axis=2))

    # vignette — radial darkening multiply map
    vg = spec.get("vignette", {})
    if vg.get("enabled", True):
        amount = float(vg.get("amount", 0.55))
        yy, xx = np.mgrid[0:h, 0:w].astype(np.float32)
        d = np.sqrt(((xx - w / 2) / (w / 2)) ** 2 + ((yy - h / 2) / (h / 2)) ** 2)
        v = np.clip(1.0 - amount * np.clip(d, 0.0, 1.5) ** 2, 0.0, 1.0).astype(np.float32)
        images["vignette"] = make_image("vignette", np.repeat(v[..., None], 3, axis=2))

    # camera-lens blur map — radial focus gradient (white sharp center)
    lb = spec.get("lens_blur", {})
    if lb.get("enabled", False):
        yy, xx = np.mgrid[0:h, 0:w].astype(np.float32)
        d = np.sqrt(((xx - w / 2) / (w / 2)) ** 2 + ((yy - h / 2) / (h / 2)) ** 2)
        d = d / d.max()
        focus = float(lb.get("focus", 0.55))
        m = np.clip(1.0 - np.maximum(d - focus, 0.0) / max(1e-6, 1.0 - focus), 0.0, 1.0)
        images["blur_map"] = make_image("blur_map", np.repeat(m[..., None], 3, axis=2))

    return images


def link(nt, a, b):
    nt.links.new(a, b)


def mix_node(nt, blend: str, fac: float) -> bpy.types.Node:
    """Blender 5.x compositor has no MixRGB — ShaderNodeMix works in a compositor tree."""
    n = nt.nodes.new("ShaderNodeMix")
    n.data_type = "RGBA"
    n.blend_type = blend
    n.inputs[1].default_value = (fac, fac, fac)  # Factor_Vector (index 1)
    return n


def build_compositor(scene: bpy.types.Scene, spec: dict, images: dict[str, bpy.types.Image]) -> None:
    scene.use_nodes = True
    nt = bpy.data.node_groups.new("VoxCompositor", type="CompositorNodeTree")
    scene.compositing_node_group = nt

    rl = nt.nodes.new("CompositorNodeRLayers")
    viewer = nt.nodes.new("CompositorNodeViewer")
    cur = rl.outputs["Image"]

    def img_node(image: bpy.types.Image) -> bpy.types.Node:
        n = nt.nodes.new("CompositorNodeImage")
        n.image = image
        return n

    # roughen edges: erode alpha with noise, threshold via steep curve
    rough = spec.get("roughen", {})
    if rough.get("enabled", True):
        sa = nt.nodes.new("CompositorNodeSetAlpha")
        link(nt, rl.outputs["Image"], sa.inputs["Image"])
        mn = mix_node(nt, "MULTIPLY", 1.0)
        link(nt, img_node(images["grain"]).outputs["Image"], mn.inputs[7])
        link(nt, rl.outputs["Alpha"], mn.inputs[6])
        cr = nt.nodes.new("CompositorNodeCurveRGB")
        c = cr.mapping.curves[3]
        pts = c.points
        while len(pts) > 2:
            pts.remove(pts[-1])
        thr = float(rough.get("threshold", 0.55))
        pts[0].location = (0.0, 0.0)
        pts.new(thr, 0.0)
        pts.new(min(thr + 0.2, 1.0), 1.0)
        pts.new(1.0, 1.0)
        link(nt, mn.outputs["Result"], cr.inputs["Image"])
        link(nt, cr.outputs["Image"], sa.inputs["Alpha"])
        cur = sa.outputs["Image"]

    # halftone letterpress overlay (multiply)
    ht = spec.get("halftone", {})
    if ht.get("enabled", True):
        mn = mix_node(nt, "MULTIPLY", 1.0)
        link(nt, cur, mn.inputs[6])
        link(nt, img_node(images["halftone"]).outputs["Image"], mn.inputs[7])
        cur = mn.outputs["Result"]

    # camera lens blur via Defocus blur-map (heavy, off by default)
    lb = spec.get("lens_blur", {})
    if lb.get("enabled", False):
        df = nt.nodes.new("CompositorNodeDefocus")
        df.max_blur = float(lb.get("radius", 8))
        link(nt, cur, df.inputs["Image"])
        link(nt, img_node(images["blur_map"]).outputs["Image"], df.inputs["Z"])
        cur = df.outputs["Image"]

    # grain (screen)
    gr = spec.get("grain", {})
    if gr.get("enabled", True):
        mn = mix_node(nt, "SCREEN", 1.0)
        link(nt, cur, mn.inputs[6])
        link(nt, img_node(images["grain"]).outputs["Image"], mn.inputs[7])
        cur = mn.outputs["Result"]

    # chromatic aberration
    ca = spec.get("chromatic_aberration", {})
    if ca.get("enabled", True):
        ld = nt.nodes.new("CompositorNodeLensdist")
        ld.inputs["Distortion"].default_value = 0.0
        ld.inputs["Dispersion"].default_value = float(ca.get("dispersion", 0.012))
        link(nt, cur, ld.inputs["Image"])
        cur = ld.outputs["Image"]

    # vignette (multiply)
    vg = spec.get("vignette", {})
    if vg.get("enabled", True):
        mn = mix_node(nt, "MULTIPLY", 1.0)
        link(nt, cur, mn.inputs[6])
        link(nt, img_node(images["vignette"]).outputs["Image"], mn.inputs[7])
        cur = mn.outputs["Result"]

    link(nt, cur, viewer.inputs["Image"])


def main() -> None:
    spec_path, out_dir = parse_args()
    with open(spec_path) as f:
        spec = json.load(f)
    os.makedirs(out_dir, exist_ok=True)
    bpy.context.scene.render.filepath = os.path.join(out_dir, "frame_")
    build_scene(spec)
    images = build_textures(spec)
    build_compositor(bpy.context.scene, spec, images)
    print(f"[vox] rendering {spec.get('duration_frames', 24)} frames @ {spec['fps']}fps -> {out_dir}")
    bpy.ops.render.render(animation=True)
    print("[vox] done")


if __name__ == "__main__":
    main()