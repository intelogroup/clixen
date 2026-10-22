/* Overlay cursor: leaves the orb for the first action, then lives on its own:
 * each later action flies from wherever it last clicked, and it stays parked
 * there (fading out after PARK_S idle) instead of returning to the orb. Coordinates are logical screen points,
 * relative to this window's origin (Rust already subtracted it). */
import { listen, emit } from "@tauri-apps/api/event";

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

let fly = null; // { from, to, click, t0 }
let park = null; // [x, y] where the cursor last clicked
let parkTimer = null;
let trail = [];

function drawPark(alpha = 1) {
  ctx.clearRect(0, 0, W, H);
  if (park) arrow(park[0], park[1], 1.2, alpha);
}

function fadePark() {
  const t0 = performance.now() / 1000;
  const step = () => {
    if (fly || !park) return; // a new action took over
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
}

function tick() {
  if (!fly) return;
  const t = performance.now() / 1000 - fly.t0;
  ctx.clearRect(0, 0, W, H);

  if (t >= T_TOTAL) {
    park = fly.to;
    fly = null;
    drawPark();
    parkTimer = setTimeout(fadePark, PARK_S * 1000);
    emit("cursor-done").catch(() => {});
    return;
  }

  let pos, scale = 1, alpha = 1;
  const tPress = T_FLY, tHold = T_FLY + T_PRESS;
  if (t < T_FLY) {
    const g = ease(t / T_FLY);
    pos = arc(fly.from, fly.to, g, 1);
    scale = 0.7 + 0.5 * g; // grows from orb-cursor size to full size
  } else if (t < tHold) {
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
