"""Build the orb's pixel sprite strips from the Mascot Bunny pack (CC0, Sunardin/Segel, opengameart.org).

    .venv/bin/python clixen-orb/tools/make_sprites.py <dir containing Bunny1/> [--preview out.png]

Each animation becomes one horizontal strip `public/sprites/<state>.png` (frames side by side, all
cells the same size, feet on one baseline) plus `manifest.json`. The source art is 669px smooth
cartoon, so frames are shrunk to CELL_H, snapped to a small palette and a hard alpha edge, and the
blue shirt is shifted to the orb's amber. Facing: the source faces LEFT; the cursor mirrors it when the bunny moves right.
"""
import colorsys
import glob
import json
import math
import os
import sys

from PIL import Image

CELL_H = 40        # native pixel height of a frame; the page draws it at an integer multiple
PALETTE = 32       # colours shared by all strips, so shading never flickers between frames
# state -> (source folder, fps, loop)
STATES = {
    "idle": ("01-Idle", 8, True),
    "move": ("02-Run", 14, True),
    "click": ("04-Throw", 14, False),
    "back": ("03-Back", 12, True),      # backing off: the settle after an overshoot (Rolling is not used: its frames sink below the feet line)
    # single poses for long flights (hops); they do not count toward the shared crop/scale below
    "jump": ("06-Jump&Fall/__Bunny1_Jump_*.png", 1, False),
    "fall": ("06-Jump&Fall/__Bunny1_Fall_*.png", 1, False),
    "jthrow": ("05-JumpThrow", 14, False),  # mid-air click: throws while hopping
    "fail": ("08-Hurt", 12, False),         # a tool errored: startled hurt pose
    "dead": ("09-Dead", 8, False),          # the whole chain aborted: one dramatic flop
}
CORE = ("idle", "move", "click")
HOP = 6            # the click strip is this many px taller: the bunny hops on the press
THINK_PAD = 8      # empty columns each side of the think cells; the thought dots sit in the right one
OUT = os.path.join(os.path.dirname(__file__), "..", "public", "sprites")


def amber(im):
    """Blue shirt -> amber (same saturation and value, so shading survives)."""
    px = im.load()
    for y in range(im.height):
        for x in range(im.width):
            r, g, b, a = px[x, y]
            if a and b > r + 40 and b > g + 20:
                h, s, v = colorsys.rgb_to_hsv(r / 255, g / 255, b / 255)
                r, g, b = (round(c * 255) for c in colorsys.hsv_to_rgb(0.105, min(1, s * 1.05), v))
                px[x, y] = (r, g, b, a)
    return im


def shrink(im, box, scale):
    im = im.crop(box)
    im = im.resize((max(1, round(im.width * scale)), CELL_H), Image.LANCZOS)
    im.putalpha(im.getchannel("A").point(lambda v: 255 if v >= 128 else 0))  # hard edge: no blurry halo
    return amber(im)


def snap(cells):
    """Quantise every cell against one palette built from all of them."""
    w, n = max(c.width for c in cells), len(cells)
    mont = Image.new("RGB", (w, CELL_H * 2 * n))
    for i, c in enumerate(cells):
        mont.paste(c.convert("RGB"), (0, i * CELL_H * 2))
    pal = mont.quantize(PALETTE, method=Image.Quantize.MEDIANCUT, dither=Image.Dither.NONE)
    out = []
    for c in cells:
        q = c.convert("RGB").quantize(palette=pal, dither=Image.Dither.NONE).convert("RGB")
        q.putalpha(c.getchannel("A"))
        out.append(q)
    return out


def hopping(cells):
    """The throw frames with the bunny rising and landing over the strip, so a press reads at small size."""
    n, out = len(cells), []
    for i, c in enumerate(cells):
        cell = Image.new("RGBA", (c.width, CELL_H + HOP), (0, 0, 0, 0))
        cell.paste(c, (0, HOP - round(HOP * math.sin(math.pi * i / (n - 1)))))
        out.append(cell)
    return out


def thinking(idle):
    """The idle cycle, padded, with a thought bubble of 1-3 amber dots filling up above the head."""
    n, out = len(idle), []
    for i, c in enumerate(idle):
        cell = Image.new("RGBA", (c.width + 2 * THINK_PAD, CELL_H), (0, 0, 0, 0))
        cell.paste(c, (THINK_PAD, 0))
        px = cell.load()
        for d in range(1 + i * 3 // n):
            for dx in range(2):
                for dy in range(2):
                    px[cell.width - THINK_PAD + d * 3 + dx, 4 + dy] = (255, 178, 70, 255)
        out.append(cell)
    return out


def main(src, preview=None):
    frames = {s: [Image.open(f).convert("RGBA") for f in sorted(glob.glob(os.path.join(src, "Bunny1", d if "*" in d else d + "/*.png")))]
              for s, (d, _, _) in STATES.items()}
    assert all(frames.values()), "missing source frames under Bunny1/"
    boxes = [im.getbbox() for st in CORE for im in frames[st]]
    box = (min(b[0] for b in boxes), min(b[1] for b in boxes), max(b[2] for b in boxes), max(b[3] for b in boxes))
    scale = CELL_H / (box[3] - box[1])  # one scale for every state, so the bunny never changes size
    os.makedirs(OUT, exist_ok=True)
    manifest, sheets = {}, []
    def state_box(st):  # same height and feet line as the core crop, widened where a pose (rolling) sticks out sideways
        bs = [im.getbbox() for im in frames[st]]
        return (min(box[0], *(b[0] for b in bs)), box[1], max(box[2], *(b[2] for b in bs)), box[3])

    cells_by = {s: [shrink(im, state_box(s), scale) for im in frames[s]] for s in STATES}
    flat = snap([c for cs in cells_by.values() for c in cs])
    k = 0
    for s, (_, fps, loop) in STATES.items():
        cells = flat[k:k + len(cells_by[s])]
        k += len(cells)
        if s == "idle":
            idle = cells
        if s == "click":
            cells = hopping(cells)
        w, h = cells[0].size
        strip = Image.new("RGBA", (w * len(cells), h), (0, 0, 0, 0))
        for i, c in enumerate(cells):
            strip.paste(c, (i * w, 0))
        strip.save(os.path.join(OUT, f"{s}.png"), optimize=True)
        manifest[s] = {"file": f"{s}.png", "frames": len(cells), "w": w, "h": h, "fps": fps, "loop": loop}
        sheets.append(strip)
    cells = thinking(idle)
    strip = Image.new("RGBA", (cells[0].width * len(cells), CELL_H), (0, 0, 0, 0))
    for i, c in enumerate(cells):
        strip.paste(c, (i * cells[0].width, 0))
    strip.save(os.path.join(OUT, "think.png"), optimize=True)
    manifest["think"] = {"file": "think.png", "frames": len(cells), "w": cells[0].width, "h": CELL_H, "fps": 6, "loop": True}
    sheets.append(strip)
    with open(os.path.join(OUT, "manifest.json"), "w") as f:
        json.dump(manifest, f, indent=2)
    print(json.dumps(manifest))
    if preview:  # 4x nearest, dark backdrop
        k = 4
        W = max(s.width for s in sheets) * k
        sheet = Image.new("RGBA", (W, CELL_H * k * len(sheets)), (40, 40, 48, 255))
        for i, s in enumerate(sheets):
            sheet.alpha_composite(s.resize((s.width * k, s.height * k), Image.NEAREST), (0, i * CELL_H * k))
        sheet.save(preview)


if __name__ == "__main__":
    main(sys.argv[1], sys.argv[sys.argv.index("--preview") + 1] if "--preview" in sys.argv else None)
