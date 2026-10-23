/* The bazooka click: the bunny stops short of the target, pulls out a bazooka, fires, and the real
 * click happens where the rocket explodes. The timeline lives here so the backend can wait for the
 * impact (tools-harness/tools/orb_cursor.py keeps IMPACT_S in step; a test compares the two).
 * Times are seconds measured from the end of the flight (T_FLY in cursor.js). */

export const B_DRAW = 0.30;    // the bazooka comes up
export const B_AIM = 0.12;     // held level, then fires
export const B_BULLET = 0.25;  // rocket flight
export const B_TAIL = 0.55;    // explosion fades, bazooka goes away
export const STANDOFF = 170;   // px the bunny stops short of the target
export const T_FIRE = B_DRAW + B_AIM;
export const T_IMPACT = T_FIRE + B_BULLET;
export const T_END = T_IMPACT + B_TAIL;

/** Where the bunny stands: STANDOFF px before the target on the line from where it started. With
 * `bounds` [w, h] it stays on screen: if that spot is off the edge it tries the far side of the
 * target, and failing that clamps (the bazooka still aims at the target from wherever it stands). */
export function standoff(from, to, bounds = null, margin = 40) {
  const dx = to[0] - from[0], dy = to[1] - from[1], len = Math.hypot(dx, dy) || 1;
  const d = Math.min(STANDOFF, len * 0.8);
  const near = [to[0] - (dx / len) * d, to[1] - (dy / len) * d];
  if (!bounds) return near;
  const inside = (p) => p[0] >= margin && p[0] <= bounds[0] - margin && p[1] >= margin && p[1] <= bounds[1] - margin;
  if (inside(near)) return near;
  const far = [to[0] + (dx / len) * STANDOFF, to[1] + (dy / len) * STANDOFF];
  if (inside(far)) return far;
  const c = (v, hi) => Math.min(Math.max(v, margin), hi - margin);
  return [c(near[0], bounds[0]), c(near[1], bounds[1])];
}

/** Phase of the shot `t` seconds after landing at the standoff point. */
export function phaseAt(t) {
  if (t < B_DRAW) return { name: "draw", u: t / B_DRAW };
  if (t < T_FIRE) return { name: "aim", u: (t - B_DRAW) / B_AIM };
  if (t < T_IMPACT) return { name: "rocket", u: (t - T_FIRE) / B_BULLET };
  return { name: "boom", u: Math.min(1, (t - T_IMPACT) / B_TAIL) };
}

// Pixel art, one char per pixel: K outline, G tube, D shade, R warhead, Y grip, W highlight.
const BAZOOKA = [
  "..KKKKKKKKKKKKKKKKKK..",
  ".KWWWWWWWWWWWWWWWWRRK.",
  "KGGGGGGGGGGGGGGGGGRRRK",
  "KDDDDDDDDDDDDDDDDDRRRK",
  ".KKKKYYKKKKKKKKKKKKK..",
  ".....KYYK.............",
  ".....KKKK.............",
];
const COLORS = { K: "#14141e", G: "#7c8a4a", D: "#566032", R: "#d8452e", Y: "#e0b040", W: "#b8c47a" };
export const BAZOOKA_LEN = BAZOOKA[0].length;
export const MUZZLE_PX = 21; // pixels from the butt (grid x 0) to the muzzle

/** Draw the bazooka with its grip at (x, y), tube pointing at `angle`. `px` is CSS px per art pixel.
 * `rise` 0..1 slides it up from below the bunny while it is drawn. */
export function drawBazooka(ctx, x, y, angle, px, rise, alpha = 1) {
  ctx.save();
  ctx.globalAlpha = alpha;
  ctx.translate(x, y + (1 - rise) * 14 * px);
  ctx.rotate(angle);
  if (Math.cos(angle) < 0) ctx.scale(1, -1); // keep the grip underneath when aiming left
  for (let r = 0; r < BAZOOKA.length; r++) {
    for (let c = 0; c < BAZOOKA[r].length; c++) {
      const ch = BAZOOKA[r][c];
      if (ch === ".") continue;
      ctx.fillStyle = COLORS[ch];
      ctx.fillRect((c - 6) * px, (r - 4) * px, px + 0.5, px + 0.5);
    }
  }
  ctx.restore();
}

/** The rocket: a 3x2 head with a flame, pointing along `angle`. */
export function drawRocket(ctx, x, y, angle, px) {
  ctx.save();
  ctx.translate(x, y);
  ctx.rotate(angle);
  ctx.fillStyle = "#ffe9a0"; ctx.fillRect(-7 * px, -px / 2, 3 * px, px);       // flame
  ctx.fillStyle = "#ff8a30"; ctx.fillRect(-5 * px, -px, 2 * px, 2 * px);
  ctx.fillStyle = "#d8d8e0"; ctx.fillRect(-3 * px, -px, 4 * px, 2 * px);       // body
  ctx.fillStyle = "#d8452e"; ctx.fillRect(1 * px, -px, 2 * px, 2 * px);        // warhead
  ctx.restore();
}

/** Pixel explosion: a growing disc of white/yellow/orange/red squares, then it dissolves. u 0..1. */
export function drawExplosion(ctx, x, y, u, px) {
  const R = 11 * px * Math.min(1, u * 3.2);          // swells in the first third
  const fade = u < 0.4 ? 1 : 1 - (u - 0.4) / 0.6;
  const step = px * 1.5;
  ctx.save();
  for (let gy = -R; gy <= R; gy += step) {
    for (let gx = -R; gx <= R; gx += step) {
      const d = Math.hypot(gx, gy), edge = R * (0.82 + 0.18 * (((Math.round(gx / step) * 7 + Math.round(gy / step) * 13) % 5) / 4));
      if (d > edge || (u > 0.5 && ((Math.round(gx / step) + Math.round(gy / step)) & 1) && Math.random() < u - 0.4)) continue;
      const k = d / R;
      ctx.globalAlpha = fade;
      ctx.fillStyle = k < 0.3 ? "#fff6d0" : k < 0.55 ? "#ffd04a" : k < 0.8 ? "#ff8a30" : "#c8341e";
      ctx.fillRect(x + gx, y + gy, step, step);
    }
  }
  // sparks flying out
  ctx.fillStyle = "#ffd04a";
  for (let i = 0; i < 10; i++) {
    const a = (i / 10) * Math.PI * 2 + 0.3, r = R * (0.9 + 1.5 * u);
    ctx.globalAlpha = Math.max(0, 1 - u * 1.3);
    ctx.fillRect(x + Math.cos(a) * r, y + Math.sin(a) * r, px, px);
  }
  ctx.restore();
}
