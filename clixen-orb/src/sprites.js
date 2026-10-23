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
 * integer, so the art stays crisp); `dpr` converts that to CSS px. `flip` mirrors a right-facing frame. */
export function drawFrame(ctx, sp, frame, x, y, px, dpr, flip, alpha) {
  const k = px / dpr;
  ctx.save();
  ctx.globalAlpha = alpha;
  ctx.imageSmoothingEnabled = false;
  ctx.translate(Math.round(x * dpr) / dpr, Math.round(y * dpr) / dpr);
  if (flip) ctx.scale(-1, 1);
  ctx.drawImage(sp.img, frame * sp.w, 0, sp.w, sp.h, -(sp.w * k) / 2, -sp.h * k, sp.w * k, sp.h * k);
  ctx.restore();
}
