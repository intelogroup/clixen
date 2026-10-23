/* Overlay cursor: leaves the orb for the first action, then lives on its own:
 * each later action flies from wherever it last clicked, and it stays parked
 * there (fading out after PARK_S idle) instead of returning to the orb. Coordinates are logical screen points,
 * relative to this window's origin (Rust already subtracted it). */
import { listen, emit } from "@tauri-apps/api/event";
import { frameAt, loadSprites, drawFrame } from "./sprites.js";

const canvas = document.getElementById("c");
const ctx = canvas.getContext("2d");
let W = 0, H = 0;

function resize() {
  const dpr = window.devicePixelRatio || 1;
  W = window.innerWidth;
  H = window.innerHeight;
  canvas.width = Math.round(W * dpr);
  canvas.height = Math.round(H * dpr);
  ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
}
resize();
window.addEventListener("resize", resize);

const AMBER = "255,178,70";
const ease = (x) => (x < 0.5 ? 4 * x * x * x : 1 - Math.pow(-2 * x + 2, 3) / 2);
const clamp01 = (x) => Math.max(0, Math.min(1, x));

// seconds: fly, press, hold
const T_FLY = 0.75, T_PRESS = 0.25, T_HOLD = 0.45;
const T_TOTAL = T_FLY + T_PRESS + T_HOLD;
const PARK_S = 30, FADE_S = 0.8; // idle time before the parked cursor fades

// The bunny that rides beside the arrow. null until the strips load, and stays null if they fail:
// the plain arrow then draws exactly as before.
let sprites = null;
loadSprites().then((s) => { sprites = s; }).catch((e) => console.warn("cursor sprites unavailable:", e));
const SPRITE_PX = 3; // device pixels per sprite pixel (integer keeps the art crisp)
let pose = { name: "idle", t0: 0, flip: false };
function setPose(name, flip = pose.flip) { if (pose.name !== name || pose.flip !== flip) pose = { name, t0: performance.now() / 1000, flip }; }

function bunny(x, y, alpha) {
  const sp = sprites && sprites[pose.name];
  if (!sp) return;
  const dpr = window.devicePixelRatio || 1;
  const k = SPRITE_PX / dpr;
  const side = pose.flip ? -1 : 1; // the bunny stands on the side the cursor came from
  const f = frameAt(sp, performance.now() / 1000 - pose.t0);
  drawFrame(ctx, sp, f, x + side * (6 + (sp.w * k) / 2), y + sp.h * k * 0.6, SPRITE_PX, dpr, pose.flip, alpha);
}

let fly = null; // { from, to, click, t0 }
let park = null; // [x, y] where the cursor last clicked
let parkTimer = null;
let trail = [];
let typing = null; // { text, per, t0 } while the typing hand is shown
let scrolling = null; // { dir, dur, t0 } while the scroll gesture is shown

function drawPark(alpha = 1) {
  ctx.clearRect(0, 0, W, H);
  if (park) arrow(park[0], park[1], 1.2, alpha);
}

// Typing: a small hand taps four keys under the parked cursor while a bubble fills in the
// text, one character per `per` seconds, the same pace the keystrokes are sent at.
const T_TYPE_TAIL = 0.5;
let pending = null; // a typing/scroll gesture that arrived while the cursor was still flying in
listen("cursor-type", (ev) => {
  if (fly) { pending = () => startType(ev.payload); return; }
  startType(ev.payload);
});

function startType({ text, per }) {
  if (!park) park = [W / 2, H / 2]; // no earlier click: type from the middle of the screen
  clearTimeout(parkTimer);
  typing = { text: String(text || ""), per: Math.max(0.004, Number(per) || 0.04), t0: performance.now() / 1000 };
  requestAnimationFrame(typeTick);
}

function roundRect(x, y, w, h, r) {
  ctx.beginPath();
  ctx.moveTo(x + r, y);
  ctx.arcTo(x + w, y, x + w, y + h, r);
  ctx.arcTo(x + w, y + h, x, y + h, r);
  ctx.arcTo(x, y + h, x, y, r);
  ctx.arcTo(x, y, x + w, y, r);
  ctx.closePath();
}

function hand(x, y, t, alpha) {
  // x,y = cursor tip. Hand sits just below-right; fingers alternate down on a row of keys.
  ctx.save();
  ctx.globalAlpha = alpha;
  ctx.translate(x + 16, y + 26);
  ctx.shadowColor = `rgba(${AMBER},0.7)`;
  ctx.shadowBlur = 10;
  roundRect(-6, -24, 52, 20, 7); // back of the hand
  ctx.fillStyle = "#fff";
  ctx.fill();
  ctx.shadowBlur = 0;
  ctx.lineWidth = 1.3;
  ctx.strokeStyle = "rgba(20,20,30,0.85)";
  ctx.stroke();
  for (let i = 0; i < 4; i++) {
    const tap = Math.max(0, Math.sin(t * 22 + i * 1.9)); // each finger taps on its own phase
    const fx = i * 12.5 - 1, fy = -6 + tap * 7;
    roundRect(fx, fy, 10, 18, 5); // finger
    ctx.fillStyle = "#fff";
    ctx.fill();
    ctx.stroke();
    roundRect(fx - 0.5, 18 + 5, 11, 5, 2); // key under it
    ctx.fillStyle = tap > 0.6 ? `rgba(${AMBER},0.95)` : "rgba(40,40,60,0.9)";
    ctx.fill();
  }
  ctx.restore();
}

function bubble(x, y, text, caret, alpha) {
  ctx.save();
  ctx.globalAlpha = alpha;
  ctx.font = '600 13px ui-monospace, "SF Mono", Menlo, monospace';
  const shown = text.length > 34 ? "…" + text.slice(-33) : text;
  const w = Math.max(54, ctx.measureText(shown).width + 28);
  const bx = x + 26, by = y - 44;
  ctx.shadowColor = `rgba(${AMBER},0.55)`;
  ctx.shadowBlur = 12;
  roundRect(bx, by, w, 28, 14);
  ctx.fillStyle = "rgba(20,20,30,0.9)";
  ctx.fill();
  ctx.shadowBlur = 0;
  ctx.lineWidth = 1.2;
  ctx.strokeStyle = `rgba(${AMBER},0.85)`;
  ctx.stroke();
  ctx.fillStyle = "#fff";
  ctx.textBaseline = "middle";
  ctx.fillText(shown, bx + 14, by + 14.5);
  if (caret) {
    const cx = bx + 14 + ctx.measureText(shown).width + 1.5;
    ctx.fillStyle = `rgba(${AMBER},1)`;
    ctx.fillRect(cx, by + 7, 2, 14);
  }
  ctx.restore();
}

function typeTick() {
  if (!typing) return;
  const t = performance.now() / 1000 - typing.t0;
  const full = typing.text.length * typing.per;
  if (t >= full + T_TYPE_TAIL) {
    typing = null;
    drawPark();
    parkTimer = setTimeout(fadePark, PARK_S * 1000);
    return;
  }
  const n = Math.min(typing.text.length, Math.floor(t / typing.per));
  const typingNow = t < full;
  const out = clamp01((t - full) / T_TYPE_TAIL); // bubble and hand fade out after the last key
  ctx.clearRect(0, 0, W, H);
  arrow(park[0], park[1], 1.2, 1);
  const a = 1 - out;
  hand(park[0], park[1], typingNow ? t : full, a);
  bubble(park[0], park[1], typing.text.slice(0, n), typingNow ? true : Math.floor(t * 3) % 2 === 0, a);
  requestAnimationFrame(typeTick);
}

// Scrolling: a two-finger hand swipes in the direction the content moves while chevrons stream
// the same way, for the length of the real wheel gesture.
const T_SCROLL_TAIL = 0.35;
const SCROLL_VEC = { up: [0, -1], down: [0, 1], left: [-1, 0], right: [1, 0] };
listen("cursor-scroll", (ev) => {
  if (fly) { pending = () => startScroll(ev.payload); return; }
  startScroll(ev.payload);
});

function startScroll({ dir, dur }) {
  if (!park) park = [W / 2, H / 2];
  clearTimeout(parkTimer);
  scrolling = { dir: SCROLL_VEC[dir] ? dir : "up", dur: Math.max(0.2, Number(dur) || 0.7), t0: performance.now() / 1000 };
  requestAnimationFrame(scrollTick);
}

function scrollHand(x, y, k, dir, alpha) {
  // k 0..1 = progress through the gesture. The hand rides an eased sweep along `dir`.
  const [vx, vy] = SCROLL_VEC[dir];
  const sweep = (k * k * (3 - 2 * k) - 0.5) * 70;
  ctx.save();
  ctx.globalAlpha = alpha;
  ctx.translate(x + 22 + vx * sweep, y + 30 + vy * sweep);
  if (vx) ctx.rotate(vx > 0 ? Math.PI / 2 : -Math.PI / 2); // draw the hand pointing along the swipe
  else if (vy > 0) ctx.rotate(Math.PI);
  ctx.shadowColor = `rgba(${AMBER},0.7)`;
  ctx.shadowBlur = 10;
  roundRect(-16, -2, 32, 26, 9); // palm
  ctx.fillStyle = "#fff";
  ctx.fill();
  ctx.shadowBlur = 0;
  ctx.lineWidth = 1.3;
  ctx.strokeStyle = "rgba(20,20,30,0.85)";
  ctx.stroke();
  for (const fx of [-11, 1]) { // index + middle finger extended
    roundRect(fx, -26, 10, 30, 5);
    ctx.fillStyle = "#fff";
    ctx.fill();
    ctx.stroke();
  }
  ctx.restore();
  // chevrons streaming the way the page moves
  ctx.save();
  ctx.translate(x + 22, y + 30);
  ctx.strokeStyle = `rgba(${AMBER},1)`;
  ctx.lineWidth = 3;
  ctx.lineCap = "round";
  for (let i = 0; i < 3; i++) {
    const ph = (k * 2.4 + i / 3) % 1;
    ctx.globalAlpha = alpha * Math.sin(Math.PI * ph);
    const d = (ph - 0.5) * 80 + 56; // distance along the swipe, beside the hand
    ctx.save();
    ctx.translate(vx ? vx * d : 52, vy ? vy * d : 0);
    ctx.rotate(Math.atan2(vy, vx));
    ctx.beginPath();
    ctx.moveTo(-5, -7);
    ctx.lineTo(3, 0);
    ctx.lineTo(-5, 7);
    ctx.stroke();
    ctx.restore();
  }
  ctx.restore();
}

function scrollTick() {
  if (!scrolling) return;
  const t = performance.now() / 1000 - scrolling.t0;
  if (t >= scrolling.dur + T_SCROLL_TAIL) {
    scrolling = null;
    drawPark();
    parkTimer = setTimeout(fadePark, PARK_S * 1000);
    return;
  }
  const out = clamp01((t - scrolling.dur) / T_SCROLL_TAIL);
  ctx.clearRect(0, 0, W, H);
  arrow(park[0], park[1], 1.2, 1);
  scrollHand(park[0], park[1], clamp01(t / scrolling.dur), scrolling.dir, 1 - out);
  requestAnimationFrame(scrollTick);
}

function fadePark() {
  const t0 = performance.now() / 1000;
  const step = () => {
    if (fly || typing || scrolling || !park) return; // a new action took over
    const k = (performance.now() / 1000 - t0) / FADE_S;
    if (k >= 1) { park = null; drawPark(); return; }
    drawPark(1 - k);
    requestAnimationFrame(step);
  };
  step();
}

listen("cursor-fly", (ev) => {
  const { from, to, click } = ev.payload;
  clearTimeout(parkTimer);
  fly = { from: park || from, to, click: click !== false, t0: performance.now() / 1000 };
  setPose("move", to[0] < fly.from[0]); // source frames face right; mirror when flying left
  trail = [];
  requestAnimationFrame(tick);
});

function arc(a, b, g, bend) {
  const dx = b[0] - a[0], dy = b[1] - a[1];
  const len = Math.hypot(dx, dy) || 1;
  const off = Math.sin(Math.PI * g) * Math.min(90, len * 0.18) * bend;
  return [a[0] + dx * g - (dy / len) * off, a[1] + dy * g + (dx / len) * off];
}

function arrow(x, y, scale, alpha) {
  ctx.save();
  ctx.globalAlpha = alpha;
  ctx.translate(x, y);
  ctx.scale(scale, scale);
  ctx.shadowColor = `rgba(${AMBER},0.9)`;
  ctx.shadowBlur = 14;
  ctx.beginPath();
  ctx.moveTo(0, 0);
  ctx.lineTo(0, 18);
  ctx.lineTo(4.6, 14);
  ctx.lineTo(7.6, 20.5);
  ctx.lineTo(10.4, 19.2);
  ctx.lineTo(7.4, 12.8);
  ctx.lineTo(13, 12.4);
  ctx.closePath();
  ctx.fillStyle = "#fff";
  ctx.fill();
  ctx.shadowBlur = 0;
  ctx.lineWidth = 1.4;
  ctx.strokeStyle = "rgba(20,20,30,0.85)";
  ctx.stroke();
  ctx.restore();
  bunny(x, y, alpha);
}

function tick() {
  if (!fly) return;
  const t = performance.now() / 1000 - fly.t0;
  ctx.clearRect(0, 0, W, H);

  if (t >= (fly.click ? T_TOTAL : T_FLY + 0.05)) { // a no-click glide (before a scroll) lands and frees the cursor at once
    park = fly.to;
    fly = null;
    setPose("idle");
    drawPark();
    parkTimer = setTimeout(fadePark, PARK_S * 1000);
    emit("cursor-done").catch(() => {});
    if (pending) { const go = pending; pending = null; go(); }
    return;
  }

  let pos, scale = 1, alpha = 1;
  const tPress = T_FLY, tHold = T_FLY + T_PRESS;
  if (t < T_FLY) {
    const g = ease(t / T_FLY);
    pos = arc(fly.from, fly.to, g, 1);
    scale = 0.7 + 0.5 * g; // grows from orb-cursor size to full size
  } else if (t < tHold) {
    if (fly.click) setPose("click");
    pos = fly.to;
    const p = Math.sin(Math.PI * clamp01((t - tPress) / T_PRESS));
    scale = 1.2 - 0.2 * p; // press down and release
    if (fly.click) {
      const rip = clamp01((t - tPress) / (T_PRESS + 0.3));
      ctx.save();
      ctx.globalAlpha = (1 - rip) * 0.9;
      ctx.strokeStyle = `rgba(${AMBER},1)`;
      ctx.lineWidth = 2.5;
      ctx.beginPath();
      ctx.arc(fly.to[0], fly.to[1], 6 + rip * 34, 0, Math.PI * 2);
      ctx.stroke();
      ctx.restore();
    }
  } else {
    pos = fly.to;
    scale = 1.2;
    if (fly.click) {
      const rip = clamp01((t - tPress) / (T_PRESS + 0.3));
      if (rip < 1) {
        ctx.save();
        ctx.globalAlpha = (1 - rip) * 0.9;
        ctx.strokeStyle = `rgba(${AMBER},1)`;
        ctx.lineWidth = 2.5;
        ctx.beginPath();
        ctx.arc(fly.to[0], fly.to[1], 6 + rip * 34, 0, Math.PI * 2);
        ctx.stroke();
        ctx.restore();
      }
    }
  }

  // Comet trail: recent positions fade out behind the cursor.
  trail.push([pos[0], pos[1]]);
  if (trail.length > 14) trail.shift();
  trail.forEach((p, i) => {
    const k = i / trail.length;
    ctx.fillStyle = `rgba(${AMBER},${0.28 * k * alpha})`;
    ctx.beginPath();
    ctx.arc(p[0], p[1], 1 + 5 * k, 0, Math.PI * 2);
    ctx.fill();
  });

  arrow(pos[0], pos[1], scale, alpha);
  requestAnimationFrame(tick);
}
