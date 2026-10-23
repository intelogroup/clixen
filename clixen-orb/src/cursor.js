/* Overlay cursor: leaves the orb for the first action, then lives on its own:
 * each later action flies from wherever it last clicked, and it stays parked
 * there (fading out after PARK_S idle) instead of returning to the orb. Coordinates are logical screen points,
 * relative to this window's origin (Rust already subtracted it). */
import { listen, emit } from "@tauri-apps/api/event";
import { frameAt, loadSprites, drawFrame } from "./sprites.js";
import { TRAVEL, pickTravel, drawSkateboard, drawRoad, drawRocketRide } from "./ride.js";
import { standoff, phaseAt, T_FIRE, T_IMPACT, T_END, MUZZLE_PX, drawBazooka, drawRocket, drawExplosion } from "./shoot.js";

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
const T_WIND = 0.14; // crouch before launch
const PARK_S = 30, FADE_S = 0.8; // idle time before the parked cursor fades

// The bunny that rides beside the arrow. null until the strips load, and stays null if they fail:
// the plain arrow then draws exactly as before.
let sprites = null;
loadSprites().then((s) => { sprites = s; }).catch((e) => console.warn("cursor sprites unavailable:", e));
const SPRITE_PX = 3; // device pixels per sprite pixel (integer keeps the art crisp)
let pose = { name: "idle", t0: 0, flip: false, loop: false };
// `loop` forces a one-shot strip (click) to repeat, for typing.
function setPose(name, flip = pose.flip, loop = false) {
  if (pose.name !== name || pose.flip !== flip || pose.loop !== loop) pose = { name, t0: performance.now() / 1000, flip, loop };
}
const poseDef = () => { const sp = sprites && sprites[pose.name]; return sp && pose.loop ? { ...sp, loop: true } : sp; };

// Per-frame motion of the bunny, set by tick(): lift = px above the ground, sy = squash on landing.
const motion = { lift: 0, sy: 1, lean: 0 };
let dust = []; // pixel puffs at the feet: { x, y, vx, vy, t0 }
let hopN = -1, lastPuff = 0, lastSmoke = 0;
const DUST_S = 0.4;
function puff(pos, side) {
  const k = SPRITE_PX / (window.devicePixelRatio || 1);
  const now = performance.now() / 1000;
  for (let i = 0; i < 3; i++) {
    dust.push({ x: pos[0] - side * 8 * k, y: pos[1] + 12 * k, vx: -side * (10 + Math.random() * 30), vy: -(6 + Math.random() * 16), t0: now });
  }
}
function drawDust() {
  const now = performance.now() / 1000;
  dust = dust.filter((d) => now - d.t0 < DUST_S);
  for (const d of dust) {
    const u = (now - d.t0) / DUST_S;
    ctx.fillStyle = d.c ? `rgba(${d.c},${0.7 * (1 - u)})` : `rgba(255,236,190,${0.9 * (1 - u)})`;
    ctx.fillRect(Math.round(d.x + d.vx * (now - d.t0)), Math.round(d.y + d.vy * (now - d.t0)), 3, 3);
  }
}

function bunny(x, y, alpha) {
  const sp = poseDef();
  if (!sp) return;
  const dpr = window.devicePixelRatio || 1;
  const k = SPRITE_PX / dpr;
  const side = pose.flip ? 1 : -1; // the strips face left, so a mirrored bunny faces right
  const f = frameAt(sp, performance.now() / 1000 - pose.t0);
  // (x, y) is the click point: it sits on the bunny's front paw (idle-cell pixel 8 across and 12 up
  // from the bottom centre), so the sprite points at the target the way the arrow used to.
  const fx = x - side * 8 * k, fy = y + 12 * k;
  if (motion.lift > 0) { // ground shadow shrinks as the bunny rises
    ctx.save();
    ctx.globalAlpha = alpha * 0.18;
    ctx.fillStyle = "#000";
    ctx.beginPath();
    ctx.ellipse(fx, fy, 9 * k * (1 - motion.lift / 160), 2.5 * k, 0, 0, Math.PI * 2);
    ctx.fill();
    ctx.restore();
  }
  drawFrame(ctx, sp, f, fx, fy - motion.lift, SPRITE_PX, dpr, pose.flip, alpha, 1, motion.sy, motion.lean);
  ctx.save();
  ctx.globalAlpha = alpha;
  ctx.fillStyle = `rgb(${AMBER})`;
  ctx.beginPath();
  ctx.arc(x, y, 2.5, 0, Math.PI * 2);
  ctx.fill();
  ctx.restore();
}

let fly = null; // { from, to, click, t0 }
let park = null; // [x, y] where the cursor last clicked
let parkTimer = null;
let trail = [];
let typing = null; // { text, per, t0 } while the bunny's typing keyboard is shown
let scrolling = null; // { dir, dur, t0 } while the scroll gesture is shown

let thinking = false; // the orb's model is working: the parked bunny shows the thought dots
let fading = false;
let loopFrame = -1, looping = false;

function drawPark(alpha = 1) {
  ctx.clearRect(0, 0, W, H);
  if (park) arrow(park[0], park[1], 1.2, alpha);
  if (alpha === 1 && !looping) { looping = true; requestAnimationFrame(parkLoop); }
}

// Keeps the parked bunny animating (idle / thinking) by redrawing only when its frame changes.
function parkLoop() {
  const sp = poseDef();
  if (!park || fly || typing || scrolling || fading || !sp) { looping = false; return; }
  const f = frameAt(sp, performance.now() / 1000 - pose.t0);
  if (f !== loopFrame) { loopFrame = f; ctx.clearRect(0, 0, W, H); arrow(park[0], park[1], 1.2, 1); }
  requestAnimationFrame(parkLoop);
}

const restPose = () => setPose(thinking ? "think" : "idle");
listen("orb-mode", (ev) => {
  thinking = ev.payload === "thinking";
  if (!fly && !typing && !scrolling) { restPose(); if (park) drawPark(); }
});

// Typing: the bunny taps a tiny keyboard in front of it while a bubble fills in the text,
// one character per `per` seconds, the same pace the keystrokes are sent at.
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
  setPose("click", pose.flip, true); // the bunny taps along with the keys
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

function keyboard(x, y, i, alpha) {
  // x,y = click point. A tiny 4-key board stands on the same ground line as the bunny's feet,
  // just in front of its front paw; the key being tapped dips and lights up amber, showing
  // the character it just pressed.
  const KEYS = 4, KW = 11, KH = 6, GAP = 1.5;
  const k = SPRITE_PX / (window.devicePixelRatio || 1);
  const kx = x + 3, ky = y + 12 * k - KH; // bottom of the keys on the bunny's feet line
  ctx.save();
  ctx.globalAlpha = alpha;
  ctx.font = '700 8px ui-monospace, "SF Mono", Menlo, monospace';
  ctx.textAlign = "center";
  ctx.textBaseline = "middle";
  for (let j = 0; j < KEYS; j++) {
    const down = j === i % KEYS;
    const bx = kx + j * (KW + GAP);
    roundRect(bx, ky + (down ? 1.5 : 0), KW, KH, 2);
    ctx.fillStyle = down ? `rgba(${AMBER},0.95)` : "rgba(28,28,42,0.92)";
    ctx.fill();
    ctx.lineWidth = 1;
    ctx.strokeStyle = down ? "rgba(90,50,10,0.9)" : "rgba(255,255,255,0.28)";
    ctx.stroke();
    if (down && i < typing.text.length) { // label the tapped key with its own character
      ctx.fillStyle = "rgba(30,18,6,0.95)";
      ctx.fillText(typing.text[i], bx + KW / 2, ky + 1.5 + KH / 2);
    }
  }
  ctx.restore();
}

function bubble(x, y, text, caret, alpha, progress) {
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
  if (progress != null) { // amber fill bar along the bubble's bottom edge
    ctx.fillStyle = `rgba(${AMBER},0.35)`;
    ctx.fillRect(bx + 1.5, by + 26 - 2.5, w - 3, 1);
    ctx.fillStyle = `rgba(${AMBER},1)`;
    ctx.fillRect(bx + 1.5, by + 26 - 2.5, (w - 3) * clamp01(progress), 1);
  }
  ctx.restore();
}

function typeTick() {
  if (!typing) return;
  const t = performance.now() / 1000 - typing.t0;
  const full = typing.text.length * typing.per;
  if (t >= full + T_TYPE_TAIL) {
    typing = null;
    restPose();
    drawPark();
    parkTimer = setTimeout(fadePark, PARK_S * 1000);
    return;
  }
  const n = Math.min(typing.text.length, Math.floor(t / typing.per));
  const typingNow = t < full;
  const out = clamp01((t - full) / T_TYPE_TAIL); // bubble and board fade out after the last key
  ctx.clearRect(0, 0, W, H);
  setPose("idle");
  arrow(park[0], park[1], 1.2, 1);
  const a = 1 - out;
  keyboard(park[0], park[1], n, a);
  bubble(park[0], park[1], typing.text.slice(0, n), typingNow ? true : Math.floor(t * 3) % 2 === 0, a, n / typing.text.length);
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
  setPose("move", pose.flip, true); // jogs in place while the page moves
  motion.lean = 0;
  scrolling = { dir: SCROLL_VEC[dir] ? dir : "up", dur: Math.max(0.2, Number(dur) || 0.7), t0: performance.now() / 1000 };
  requestAnimationFrame(scrollTick);
}

function scrollHand(x, y, k, dir, alpha) {
  // k 0..1 = progress through the gesture. The hand rides an eased sweep along `dir`.
  const [vx, vy] = SCROLL_VEC[dir];
  const sweep = (k * k * (3 - 2 * k) - 0.5) * 70;
  if (!sprites) {
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
  }
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
    restPose();
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
  fading = true;
  const step = () => {
    if (fly || typing || scrolling || !park) { fading = false; return; } // a new action took over
    const k = (performance.now() / 1000 - t0) / FADE_S;
    if (k >= 1) { park = null; fading = false; drawPark(); return; }
    drawPark(1 - k);
    requestAnimationFrame(step);
  };
  step();
}

listen("cursor-fly", (ev) => {
  const { from, to, click, style, travel } = ev.payload;
  clearTimeout(parkTimer);
  fly = { from: park || from, to, click: click !== false, t0: performance.now() / 1000 };
  // Bazooka click: the bunny stops short, shoots, and the click lands where the rocket explodes.
  fly.shoot = fly.click && style === "bazooka" && !!sprites;
  fly.dest = fly.shoot ? standoff(fly.from, to, [W, H]) : to;
  lastSmoke = 0;
  fly.travel = TRAVEL.includes(travel) ? travel : pickTravel(Math.hypot(fly.dest[0] - fly.from[0], fly.dest[1] - fly.from[1]));
  fly.ride = null;
  // The source frames face left: mirror when moving right. A near-vertical move keeps the old facing.
  setPose("move", Math.abs(to[0] - fly.from[0]) < 8 ? pose.flip : to[0] > fly.from[0]);
  trail = [];
  dust = []; hopN = -1; lastPuff = 0;
  requestAnimationFrame(tick);
});

function arc(a, b, g, bend) {
  const dx = b[0] - a[0], dy = b[1] - a[1];
  const len = Math.hypot(dx, dy) || 1;
  const off = Math.sin(Math.PI * g) * Math.min(90, len * 0.18) * bend;
  return [a[0] + dx * g - (dy / len) * off, a[1] + dy * g + (dx / len) * off];
}

function arrow(x, y, scale, alpha) {
  if (sprites) { bunny(x, y, alpha); return; } // the bunny replaces the arrow; the arrow is the fallback
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
}

function tick() {
  if (!fly) return;
  const t = performance.now() / 1000 - fly.t0 - T_WIND;
  ctx.clearRect(0, 0, W, H);
  if (t < 0) { // anticipation: a quick crouch before the launch
    setPose("idle");
    motion.lift = 0; motion.sy = 0.86; motion.lean = 0;
    arrow(fly.from[0], fly.from[1], 1, 1);
    requestAnimationFrame(tick);
    return;
  }

  if (t >= (fly.shoot ? T_FLY + T_END : fly.click ? T_TOTAL : T_FLY + 0.05)) { // a no-click glide (before a scroll) lands and frees the cursor at once
    park = fly.dest;
    fly = null;
    motion.lift = 0; motion.sy = 1; motion.lean = 0; dust = [];
    restPose();
    drawPark();
    parkTimer = setTimeout(fadePark, PARK_S * 1000);
    emit("cursor-done").catch(() => {});
    if (pending) { const go = pending; pending = null; go(); }
    return;
  }

  let pos, scale = 1, alpha = 1, after = null;
  const before = []; // vehicle drawn under the bunny's feet
  const side = fly.to[0] < fly.from[0] ? -1 : 1;
  const tPress = T_FLY, tHold = T_FLY + T_PRESS;
  if (t < T_FLY) {
    const g = ease(t / T_FLY);
    pos = arc(fly.from, fly.dest, g, 1);
    scale = 0.7 + 0.5 * g; // grows from orb-cursor size to full size
    const len = Math.hypot(fly.dest[0] - fly.from[0], fly.dest[1] - fly.from[1]);
    const hops = fly.travel === "hop" ? Math.max(1, Math.round(len / 300)) : 0;
    const kk = SPRITE_PX / (window.devicePixelRatio || 1);
    if (fly.travel === "skate" || fly.travel === "rocket") {
      const skate = fly.travel === "skate";
      if (skate) pos = [fly.from[0] + (fly.dest[0] - fly.from[0]) * g, fly.from[1] + (fly.dest[1] - fly.from[1]) * g]; // a straight line
      motion.lift = 0; motion.sy = 1; motion.lean = skate ? 2 : 0;
      setPose(skate ? "idle" : "jump");
      const ahead = skate ? fly.dest : arc(fly.from, fly.dest, Math.min(1, g + 0.02), 1);
      const ang = Math.atan2(ahead[1] - pos[1], ahead[0] - pos[0]) || 0;
      const fx = pos[0] - side * 8 * kk, fy = pos[1] + 12 * kk, spin = Math.floor(t * 20), gg = g;
      fly.ride = (a) => {
        if (skate) { drawRoad(ctx, fly.from, fly.dest, gg, a); drawSkateboard(ctx, fx, fy, kk, spin, a); }
        else drawRocketRide(ctx, fx, fy + 2 * kk, ang, kk, spin, a);
      };
      before.push(() => fly.ride(1));
      if (t - lastPuff > (skate ? 0.09 : 0.03)) {
        if (skate) puff(pos, side);
        else dust.push({ x: fx - Math.cos(ang) * 16 * kk, y: fy + 2 * kk - Math.sin(ang) * 16 * kk, vx: -Math.cos(ang) * 25, vy: -Math.sin(ang) * 25, t0: performance.now() / 1000, c: Math.random() < 0.5 ? "190,190,200" : "255,170,70" });
        lastPuff = t;
      }
    } else if (hops) {
      const ph = (g * hops) % 1;
      motion.lift = Math.sin(Math.PI * ph) * Math.min(70, len * 0.1);
      motion.sy = ph < 0.08 || ph > 0.92 ? 0.88 : 1;
      motion.lean = ph < 0.5 ? 1 : 0;
      setPose(ph < 0.5 ? "jump" : "fall");
      const n = Math.floor(g * hops);
      if (n !== hopN) { if (hopN >= 0) puff(pos, side); hopN = n; } // a puff at each landing
    } else {
      motion.lift = Math.round(Math.sin(t * 44)); // 1 px bob at about 7 Hz
      motion.sy = 1;
      motion.lean = 2;
      setPose("move");
      if (g > 0.85) { // lunge a little past the target, then back off onto it
        const u = (g - 0.85) / 0.15;
        const dx = fly.dest[0] - fly.from[0], dy = fly.dest[1] - fly.from[1], d = Math.hypot(dx, dy) || 1;
        pos = [pos[0] + (dx / d) * 7 * Math.sin(Math.PI * u), pos[1] + (dy / d) * 7 * Math.sin(Math.PI * u)];
        if (u > 0.5) { setPose("back"); motion.lean = 0; }
      }
      if (t - lastPuff > 0.09) { puff(pos, side); lastPuff = t; }
    }
  } else if (fly.shoot) {
    const ts = t - T_FLY, ph = phaseAt(ts);
    if (fly.ride && ts < 0.25) before.push(() => fly.ride(1 - ts / 0.25));
    if (!fly.landed) { fly.landed = true; puff(fly.dest, side); }
    const k = SPRITE_PX / (window.devicePixelRatio || 1);
    const ang = Math.atan2(fly.to[1] - fly.dest[1], fly.to[0] - fly.dest[0]);
    const recoil = ts >= T_FIRE && ts < T_FIRE + 0.1 ? 1 - (ts - T_FIRE) / 0.1 : 0; // bunny kicks back as it fires
    pos = [fly.dest[0] - Math.cos(ang) * 5 * recoil, fly.dest[1] - Math.sin(ang) * 5 * recoil];
    motion.lift = 0; motion.lean = 0;
    motion.sy = ts < 0.12 ? 0.88 : 1;
    // face the target (the bunny may stand on the far side of it when the near side is off-screen)
    setPose(ts >= T_FIRE ? "click" : "idle", Math.abs(fly.to[0] - fly.dest[0]) < 8 ? pose.flip : fly.to[0] > fly.dest[0]);
    const muzzle = [pos[0] + Math.cos(ang) * (MUZZLE_PX - 6) * k, pos[1] + Math.sin(ang) * (MUZZLE_PX - 6) * k];
    after = () => {
      const gone = ph.name === "boom" ? clamp01((ph.u - 0.25) / 0.35) : 0; // bazooka slips away once it has fired
      drawBazooka(ctx, pos[0], pos[1], ang, k, ph.name === "draw" ? ph.u : 1, 1 - gone);
      if (ts >= T_FIRE && ts < T_FIRE + 0.08) { // muzzle flash
        ctx.save(); ctx.fillStyle = "#fff0b0"; ctx.beginPath(); ctx.arc(muzzle[0], muzzle[1], 7, 0, Math.PI * 2); ctx.fill(); ctx.restore();
      }
      if (ph.name === "rocket") {
        const e = Math.pow(ph.u, 1.6), rx = muzzle[0] + (fly.to[0] - muzzle[0]) * e, ry = muzzle[1] + (fly.to[1] - muzzle[1]) * e;
        if (ts - lastSmoke > 0.03) { dust.push({ x: rx, y: ry, vx: 0, vy: -4, t0: performance.now() / 1000, c: "190,190,200" }); lastSmoke = ts; }
        drawRocket(ctx, rx, ry, ang, k);
      } else if (ph.name === "boom") {
        drawExplosion(ctx, fly.to[0], fly.to[1], ph.u, k);
        const rip = clamp01(ph.u * 1.4);
        ctx.save(); ctx.globalAlpha = (1 - rip) * 0.9; ctx.strokeStyle = `rgba(${AMBER},1)`; ctx.lineWidth = 2.5;
        ctx.beginPath(); ctx.arc(fly.to[0], fly.to[1], 8 + rip * 46, 0, Math.PI * 2); ctx.stroke(); ctx.restore();
      }
    };
  } else if (t < tHold) {
    if (fly.ride && t - T_FLY < 0.25) before.push(() => fly.ride(1 - (t - T_FLY) / 0.25));
    if (!fly.landed) { fly.landed = true; puff(fly.to, side); } // skid puff on arrival
    motion.lift = 0; motion.lean = 0;
    motion.sy = t < T_FLY + 0.12 ? 0.88 : 1; // brief squash as it lands
    if (fly.click) setPose("click");
    else setPose("idle");
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
    motion.lift = 0; motion.sy = 1;
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

  drawDust();
  before.forEach((f) => f());
  arrow(pos[0], pos[1], scale, alpha);
  if (after) after();
  requestAnimationFrame(tick);
}
