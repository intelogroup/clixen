/* Sprite strips for the overlay cursor. Strips live in public/sprites (see tools/make_sprites.py):
 * one horizontal PNG per state, frames of equal size side by side, described by manifest.json.
 * frameAt is pure so it can be tested without a DOM. */

/** Frame index of an animation `t` seconds after it started. One-shots hold their last frame. */
export function frameAt(def, t) {
  const i = Math.floor(Math.max(0, t) * def.fps);
  return def.loop ? i % def.frames : Math.min(i, def.frames - 1);
}

/** Has a one-shot played all its frames? Loops never finish. */
export function finished(def, t) {
  return !def.loop && t * def.fps >= def.frames;
}

/** Horizontal shift, in sprite pixels, of each of `bands` stacked bands (top first) for a lean of
 * `lean` px at the head: the feet stay put, so a pixel sprite leans without being rotated and blurred. */
export function leanOffsets(lean, bands = 4) {
  return Array.from({ length: bands }, (_, b) => Math.round((lean * (bands - 1 - b)) / (bands - 1)));
}

/** Load the manifest and every strip. Rejects if anything is missing, so callers fall back to drawing. */
export async function loadSprites(base = "/sprites/") {
  const manifest = await (await fetch(base + "manifest.json")).json();
  const out = {};
  await Promise.all(Object.entries(manifest).map(([name, def]) => new Promise((ok, no) => {
    const img = new Image();
    img.onload = () => { out[name] = { ...def, img }; ok(); };
    img.onerror = () => no(new Error(`sprite ${name} failed to load`));
    img.src = base + def.file;
  })));
  return out;
}

/** Draw one frame with its feet at (x, y) bottom-centre. `px` is device pixels per sprite pixel (an
 * integer, so the art stays crisp); `dpr` converts that to CSS px. `flip` mirrors a right-facing frame;
 * `sx`/`sy` squash or stretch it about the feet (non-integer, so use briefly); `lean` leans it
 * forward by that many sprite px at the head. */
export function drawFrame(ctx, sp, frame, x, y, px, dpr, flip, alpha, sx = 1, sy = 1, lean = 0) {
  const k = px / dpr;
  ctx.save();
  ctx.globalAlpha = alpha;
  ctx.imageSmoothingEnabled = false;
  ctx.translate(Math.round(x * dpr) / dpr, Math.round(y * dpr) / dpr);
  if (flip) ctx.scale(-1, 1);
  ctx.scale(sx, sy); // squash/stretch about the feet
  const bands = 4, bh = sp.h / bands, off = leanOffsets(lean, bands);
  for (let b = 0; b < bands; b++) {
    ctx.drawImage(sp.img, frame * sp.w, b * bh, sp.w, bh, -(sp.w * k) / 2 + off[b] * k, -sp.h * k + b * bh * k, sp.w * k, bh * k);
  }
  ctx.restore();
}
