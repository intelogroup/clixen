import { invoke } from "@tauri-apps/api/core";
import { listen, emit } from "@tauri-apps/api/event";
import { getCurrentWindow, LogicalPosition } from "@tauri-apps/api/window";

/* =========================================================================
 * Clixen Orb
 *
 * The orb is the whole product. It floats above every other window as an
 * NSPanel and renders nothing but itself onto a transparent canvas; all text
 * is handed off to the separate `caption` window so this one stays tight
 * enough to never swallow a click meant for the page underneath.
 *
 * States:
 *   idle      — slow breathing, indigo
 *   listening — mic open, cyan ripples driven by real amplitude
 *   thinking  — the model is working, a violet swirl
 *   speaking  — TTS playing, magenta ripples driven by the audio envelope
 *   acting    — a tool is running: orb shrinks, amber cursor glides and clicks
 * ========================================================================= */

const canvas = document.getElementById("orb");
const ctx = canvas.getContext("2d");
const statusEl = document.getElementById("status");
const win = getCurrentWindow();

const ORB_R = 55;      // sphere radius in logical px
const ORB_CY = 75;     // centre of the 150x150 window
const ORB_BREATH = 1;  // breathing scale applied to the idle envelope

/* ---- canvas sizing ----------------------------------------------------- */

let W = 0, H = 0, dpr = 1;

function resize() {
  dpr = window.devicePixelRatio || 1;
  W = window.innerWidth;
  H = window.innerHeight;
  canvas.width = Math.round(W * dpr);
  canvas.height = Math.round(H * dpr);
  ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
}
resize();
window.addEventListener("resize", resize);

/* ---- state ------------------------------------------------------------ */

const state = {
  mode: "idle",      // idle | listening | thinking | speaking
  amp: 0,            // smoothed 0..1 envelope driving the ripples
  targetAmp: 0,
  t: 0,
};

const PALETTES = {
  idle:      { hot: [186, 210, 255], mid: [96, 128, 245], cold: [46, 58, 150] },
  listening: { hot: [190, 250, 255], mid: [86, 190, 248], cold: [26, 96, 178] },
  thinking:  { hot: [225, 200, 255], mid: [150, 118, 245], cold: [76, 44, 158] },
  speaking:  { hot: [255, 214, 240], mid: [236, 122, 216], cold: [122, 40, 158] },
  acting:    { hot: [255, 236, 190], mid: [255, 178, 70],  cold: [168, 88, 24] },
};

function setMode(mode) {
  state.mode = mode;
  emit("orb-mode", mode).catch(() => {}); // the cursor overlay shows its bunny thinking while the model works
  if (mode === "idle") state.targetAmp = 0;
}

/* ---- action: orb -> cursor -> click ------------------------------------
 * While a tool runs, the orb shrinks to a dim core and a mouse cursor glides
 * between a few spots, pressing and rippling at each — "I'm doing it for you".
 * `act.k` eases 0..1 so entering and leaving the state morphs, never snaps.
 * ------------------------------------------------------------------------ */

const act = { on: false, k: 0, t0: 0, offAt: 0, fly: false };
const ACT_MIN_S = 1.3; // tools finish in ~1s; keep the cursor up long enough to read as a gesture
const ACT_TARGETS = [[34, 26], [-30, 30], [24, -32], [-36, -10]]; // px from orb centre
const ACT_CYCLE = 1.5; // seconds per glide+click
const ease = (x) => (x < 0.5 ? 4 * x * x * x : 1 - Math.pow(-2 * x + 2, 3) / 2);
const clamp01 = (x) => Math.max(0, Math.min(1, x));

function setAction(on) {
  if (!on && act.fly) return; // the overlay cursor is out; cursorHome() ends it
  if (!on && act.on && state.t - act.t0 < ACT_MIN_S) {
    act.offAt = act.t0 + ACT_MIN_S; // frame() releases it once the minimum has elapsed
    return;
  }
  if (act.on === on) return;
  act.on = on;
  act.offAt = 0;
  if (on) act.t0 = state.t;
}

function drawCursor(cx, cy) {
  const k = act.k;
  // While the overlay cursor is out flying across the screen, it owns the cursor.
  if (k < 0.02 || act.fly) return;
  const c = (state.t - act.t0) / ACT_CYCLE;
  const idx = Math.max(0, Math.floor(c));
  const f = c - idx;
  const from = idx === 0 ? [0, 0] : ACT_TARGETS[(idx - 1) % ACT_TARGETS.length];
  const to = ACT_TARGETS[idx % ACT_TARGETS.length];
  const g = ease(clamp01(f / 0.55));
  // Glide on a shallow arc so it reads as a hand, not a slide.
  const arc = Math.sin(Math.PI * g) * 12;
  const dx = to[0] - from[0], dy = to[1] - from[1];
  const len = Math.hypot(dx, dy) || 1;
  const x = cx + from[0] + dx * g - (dy / len) * arc;
  const y = cy + from[1] + dy * g + (dx / len) * arc;

  const clickF = clamp01((f - 0.6) / 0.25);          // press down and up
  const press = Math.sin(Math.PI * clickF);
  const rip = clamp01((f - 0.6) / 0.4);              // ripple after the press starts
  const hot = PALETTES.acting.hot;

  if (f >= 0.6) {
    ctx.save();
    ctx.globalAlpha = k * (1 - rip) * 0.9;
    ctx.strokeStyle = rgb(hot);
    ctx.lineWidth = 2;
    ctx.beginPath();
    ctx.arc(x, y, 4 + rip * 22, 0, Math.PI * 2);
    ctx.stroke();
    ctx.restore();
  }

  ctx.save();
  ctx.globalAlpha = k;
  ctx.translate(x, y);
  const sc = (0.7 + 0.3 * k) * (1 - 0.16 * press);
  ctx.scale(sc, sc);
  ctx.shadowColor = rgb(PALETTES.acting.mid, 0.9);
  ctx.shadowBlur = 10;
  ctx.beginPath(); // arrow, tip at (0,0)
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

/* ---- caption -----------------------------------------------------------
 * All text goes through Rust so the caption window can be positioned
 * directly beneath wherever the orb currently sits.
 * ------------------------------------------------------------------------ */

let captionText = "";
const persistFlags = new Set(["listening", "thinking"]);

function say(text, kind, persist) {
  captionText = text || "";
  invoke("orb_caption", {
    text: captionText,
    kind: kind || "",
    persist: persist ?? persistFlags.has(state.mode),
  }).catch(() => {});
}

function sayClear() {
  captionText = "";
  invoke("orb_caption", { text: "", kind: "", persist: false }).catch(() => {});
}

/* ---- drawing ---------------------------------------------------------- */

const mix = (a, b, k) => [
  a[0] + (b[0] - a[0]) * k,
  a[1] + (b[1] - a[1]) * k,
  a[2] + (b[2] - a[2]) * k,
];
const rgb = (c, a = 1) => `rgba(${c[0] | 0},${c[1] | 0},${c[2] | 0},${a})`;

function draw() {
  const cx = W / 2, cy = ORB_CY;
  ctx.clearRect(0, 0, W, H);
  // Acting: the sphere shrinks to a dim core while the cursor works.
  ctx.save();
  const s = 1 - 0.6 * act.k;
  ctx.globalAlpha = 1 - 0.4 * act.k;
  ctx.translate(cx, cy);
  ctx.scale(s, s);
  ctx.translate(-cx, -cy);
  drawOrb(cx, cy);
  ctx.restore();
  drawCursor(cx, cy);
}

function drawOrb(cx, cy) {
  const base = PALETTES[state.mode] || PALETTES.idle;
  const pal = act.k > 0.01
    ? {
        hot: mix(base.hot, PALETTES.acting.hot, act.k),
        mid: mix(base.mid, PALETTES.acting.mid, act.k),
        cold: mix(base.cold, PALETTES.acting.cold, act.k),
      }
    : base;
  const a = state.amp;

  /* Outer bloom — a soft halo that swells with the envelope. */
  const bloomR = ORB_R * (1.85 + a * 0.55);
  const bloom = ctx.createRadialGradient(cx, cy, ORB_R * 0.4, cx, cy, bloomR);
  bloom.addColorStop(0, rgb(pal.mid, 0.32 + a * 0.24));
  bloom.addColorStop(0.45, rgb(pal.cold, 0.13 + a * 0.11));
  bloom.addColorStop(1, rgb(pal.cold, 0));
  ctx.fillStyle = bloom;
  ctx.beginPath();
  ctx.arc(cx, cy, bloomR, 0, Math.PI * 2);
  ctx.fill();

  /* Radiating ripples. Their spacing widens with amplitude, which is what
     makes the orb read as "reacting" rather than merely pulsing. */
  const spin = state.t * 0.22;
  for (let i = 0; i < 5; i++) {
    const phase = (state.t * (0.55 + a * 1.5) + i * 0.2) % 1;
    const rr = ORB_R * (0.55 + phase * (1.05 + a * 0.5));
    if (rr > ORB_R * 1.7) continue;
    const fade = (1 - phase) * (1 - phase) * (0.10 + a * 0.36);
    ctx.save();
    ctx.translate(cx, cy);
    ctx.rotate(spin + i * 0.35);
    ctx.strokeStyle = rgb(pal.hot, fade);
    ctx.lineWidth = 1 + a * 1.6;
    ctx.beginPath();
    ctx.ellipse(0, 0, rr, rr * 0.985, 0, 0, Math.PI * 2);
    ctx.stroke();
    ctx.restore();
  }

  /* The sphere. */
  const r = ORB_R * (1 + a * 0.06);

  const body = ctx.createRadialGradient(
    cx - r * 0.34, cy - r * 0.40, r * 0.08,
    cx, cy, r * 1.12,
  );
  body.addColorStop(0, rgb(pal.hot));
  body.addColorStop(0.34, rgb(mix(pal.hot, pal.mid, 0.75)));
  body.addColorStop(0.66, rgb(pal.mid));
  body.addColorStop(1, rgb(pal.cold));
  ctx.fillStyle = body;
  ctx.beginPath();
  ctx.arc(cx, cy, r, 0, Math.PI * 2);
  ctx.fill();

  ctx.save();
  ctx.beginPath();
  ctx.arc(cx, cy, r, 0, Math.PI * 2);
  ctx.clip();

  /* Inner "liquid" contour that wobbles with the envelope — the thing that
     makes it feel organic instead of like a CSS gradient. */
  const cxr = cx + Math.cos(state.t * 0.9) * r * 0.10;
  const cyr = cy + Math.sin(state.t * 1.15) * r * 0.12;
  const inner = ctx.createRadialGradient(
    cxr, cyr, r * 0.05,
    cxr, cyr, r * (1.15 - a * 0.12),
  );
  inner.addColorStop(0, rgb(mix(pal.hot, [255, 255, 255], 0.55), 0.55));
  inner.addColorStop(0.4, rgb(pal.hot, 0.16));
  inner.addColorStop(1, rgb(pal.cold, 0));
  ctx.fillStyle = inner;
  ctx.beginPath();
  for (let i = 0; i <= 96; i++) {
    const th = (i / 96) * Math.PI * 2;
    const wob =
      1 +
      0.055 * Math.sin(th * 3 + state.t * 1.6) * (0.5 + a * 1.6) +
      0.03 * Math.sin(th * 5 - state.t * 2.3) * (0.5 + a);
    const px = cx + Math.cos(th) * r * wob;
    const py = cy + Math.sin(th) * r * wob;
    i ? ctx.lineTo(px, py) : ctx.moveTo(px, py);
  }
  ctx.closePath();
  ctx.fill();

  /* Rim darkening — what sells the sphere as 3D on a flat page. */
  const rim = ctx.createRadialGradient(cx, cy, r * 0.62, cx, cy, r);
  rim.addColorStop(0, rgb(pal.cold, 0));
  rim.addColorStop(1, rgb(pal.cold, 0.55));
  ctx.fillStyle = rim;
  ctx.beginPath();
  ctx.arc(cx, cy, r, 0, Math.PI * 2);
  ctx.fill();
  ctx.restore();

  /* Inner rim light along the top-left arc. */
  ctx.strokeStyle = rgb(pal.hot, 0.5);
  ctx.lineWidth = 1.4;
  ctx.beginPath();
  ctx.arc(cx, cy, r - 0.7, Math.PI * 0.86, Math.PI * 1.72);
  ctx.stroke();

  /* Specular highlight. */
  ctx.save();
  ctx.globalCompositeOperation = "lighter";
  const sx = cx - r * 0.30, sy = cy - r * 0.34;
  const spec = ctx.createRadialGradient(sx, sy, 0, sx, sy, r * 0.42);
  spec.addColorStop(0, `rgba(255,255,255,${0.42 + a * 0.16})`);
  spec.addColorStop(1, "rgba(255,255,255,0)");
  ctx.fillStyle = spec;
  ctx.beginPath();
  ctx.arc(sx, sy, r * 0.42, 0, Math.PI * 2);
  ctx.fill();
  ctx.restore();
}

/* ---- animation loop --------------------------------------------------- */

let last = performance.now();
function frame(now) {
  const dt = Math.min(0.05, (now - last) / 1000);
  last = now;
  state.t += dt;

  // Fast attack, slow release — a VU meter, not a lerp.
  const k = state.targetAmp > state.amp ? 0.35 : 0.08;
  state.amp += (state.targetAmp - state.amp) * k;
  if (act.offAt && state.t >= act.offAt) setAction(false);
  act.k += ((act.on ? 1 : 0) - act.k) * (1 - Math.exp(-dt * 10));

  if (state.mode === "idle") {
    // Idle never stops breathing, so the orb is alive even at rest.
    state.targetAmp =
      ORB_BREATH * (0.055 + 0.045 * Math.sin(state.t * 2.2));
  }

  draw();
  requestAnimationFrame(frame);
}
requestAnimationFrame(frame);

/* ---- audio: mic capture + envelope ------------------------------------ */

let mediaStream = null;
let mediaRecorder = null;
let audioCtx = null;
let analyser = null;
let analyserData = null;
let rafId = 0;

function ensureAudioCtx() {
  if (!audioCtx) audioCtx = new AudioContext();
  if (audioCtx.state === "suspended") audioCtx.resume();
  return audioCtx;
}

function meterLoop() {
  if (!analyser) return;
  analyser.getByteTimeDomainData(analyserData);
  let sum = 0;
  for (let i = 0; i < analyserData.length; i++) {
    const v = (analyserData[i] - 128) / 128;
    sum += v * v;
  }
  const rms = Math.sqrt(sum / analyserData.length);
  // Square it so room tone stays near zero while speech pops.
  state.targetAmp = Math.min(1, Math.pow(rms * 3.2, 1.5));
  rafId = requestAnimationFrame(meterLoop);
}

function attachAnalyser(source) {
  analyser = audioCtx.createAnalyser();
  analyser.fftSize = 512;
  analyserData = new Uint8Array(analyser.fftSize);
  source.connect(analyser);
  cancelAnimationFrame(rafId);
  meterLoop();
}

function detachAnalyser() {
  cancelAnimationFrame(rafId);
  analyser = null;
  analyserData = null;
  state.targetAmp = 0;
}

let recordedChunks = [];
let recordGuard = 0;

/**
 * Absolute ceiling on a single recording.
 *
 * The window-level `pointerup` handles a normal release, but there are
 * moments the event simply never arrives — the screen locks mid-press, the
 * app is switched away, the system steals the gesture. Without a ceiling the
 * microphone would stay hot indefinitely, so cut it off here.
 */
const MAX_RECORD_MS = 45000;

async function startListening() {
  if (mediaRecorder) return;
  // Never record while the orb is talking: the microphone would pick the
  // reply back up and the two would trade sentences forever.
  if (state.mode === "speaking") return;
  setMode("listening");
  say("Listening…", "you", true);
  recordedChunks = [];

  try {
    mediaStream = await navigator.mediaDevices.getUserMedia({
      audio: { echoCancellation: true, noiseSuppression: true, channelCount: 1 },
    });
  } catch (e) {
    say(`Mic unavailable: ${e.message || e}`, "", false);
    setMode("idle");
    return;
  }

  const c = ensureAudioCtx();
  attachAnalyser(c.createMediaStreamSource(mediaStream));

  mediaRecorder = new MediaRecorder(mediaStream);
  mediaRecorder.ondataavailable = (ev) => {
    if (ev.data.size) recordedChunks.push(ev.data);
  };
  mediaRecorder.onstop = onRecordingStop;
  mediaRecorder.start();

  clearTimeout(recordGuard);
  recordGuard = setTimeout(() => {
    if (mediaRecorder && mediaRecorder.state !== "inactive") stopListening();
  }, MAX_RECORD_MS);
}

function stopListening() {
  if (mediaRecorder && mediaRecorder.state !== "inactive") mediaRecorder.stop();
}

function teardownMic() {
  clearTimeout(recordGuard);
  if (mediaStream) {
    mediaStream.getTracks().forEach((t) => t.stop());
    mediaStream = null;
  }
  detachAnalyser();
  mediaRecorder = null;
}

async function onRecordingStop() {
  const blob = new Blob(recordedChunks, { type: "audio/webm" });
  teardownMic();
  if (blob.size < 2048) {
    // ~silence — don't bother the backend with it.
    setMode("idle");
    say("Didn't catch that.", "", false);
    setTimeout(sayClear, 2400);
    return;
  }
  await sendAudio(blob);
}

/* ---- backend bridge --------------------------------------------------- */

function blobToB64(blob) {
  return new Promise((resolve, reject) => {
    const fr = new FileReader();
    fr.onload = () => resolve(String(fr.result).split(",", 2)[1]);
    fr.onerror = reject;
    fr.readAsDataURL(blob);
  });
}

async function sendAudio(blob) {
  setMode("thinking");
  say("…", "you", true);
  try {
    const audio_b64 = await blobToB64(blob);
    await invoke("orb_send_audio", { audioB64: audio_b64, tts: true });
  } catch (e) {
    say(String(e), "", false);
    setMode("idle");
    setTimeout(sayClear, 3200);
  }
}

async function sendText(text) {
  setMode("thinking");
  say(text, "you", true);
  try {
    await invoke("orb_send_text", { message: text });
  } catch (e) {
    say(String(e), "", false);
    setMode("idle");
    setTimeout(sayClear, 3200);
  }
}

/* ---- backend → orb ---------------------------------------------------- */

let botBuffer = "";

/**
 * How many TTS clips are still playing.
 *
 * The backend speaks a reply one sentence at a time, so a single answer can
 * arrive as several audio frames. The strip must stay up — and the orb must
 * stay in its "speaking" palette — until the LAST clip has finished.
 */
let pendingAudio = 0;

async function playWavB64(b64) {
  const bytes = Uint8Array.from(atob(b64), (c) => c.charCodeAt(0));
  const buf = await ensureAudioCtx().decodeAudioData(bytes.buffer);
  const src = audioCtx.createBufferSource();
  src.buffer = buf;
  // The analyser alone is a dead end — a node that nothing downstream reads
  // from never reaches the speakers. Route it to the output as well.
  src.connect(audioCtx.destination);
  attachAnalyser(src);
  pendingAudio += 1;
  src.onended = () => {
    pendingAudio -= 1;
    if (pendingAudio <= 0 && state.mode === "speaking") {
      pendingAudio = 0;
      detachAnalyser();
      setMode("idle");
      sayClear();
    }
  };
  src.start();
}

// The overlay cursor (cursor.js) is flying to a click target: the orb stays in
// its "acting" core shape until the cursor has come home.
let flyGuard = 0;
listen("cursor-fly", () => {
  act.fly = true;
  setAction(true);
  act.offAt = 0;
  clearTimeout(flyGuard);
  flyGuard = setTimeout(() => cursorHome(), 4000); // never strand the orb in acting
});
listen("cursor-done", () => cursorHome());
function cursorHome() {
  clearTimeout(flyGuard);
  act.fly = false;
  act.offAt = 0;
  act.on = false;
}

listen("orb-stop-listening", () => {
  // Rust asks us to stop (the orb was dismissed). Never leave the mic hot.
  if (state.mode === "listening") stopListening();
});

listen("orb-event", (ev) => {
  const p = ev.payload;
  switch (p.kind) {
    case "transcript":
      say(p.text, "you", true);
      break;

    case "token":
      // The backend announces a running tool as "⚙️ Running <tool>…" inside the
      // token stream; show it as the cursor animation, not as spoken caption text.
      if (p.text.includes("\u2699\ufe0f Running ")) {
        setAction(true);
        state.mode = state.mode === "speaking" ? "speaking" : "thinking";
        break;
      }
      setAction(false);
      botBuffer += p.text;
      state.mode = state.mode === "speaking" ? "speaking" : "thinking";
      say(botBuffer, "", true);
      break;

    case "audio":
      setAction(false);
      // The reply is being spoken — hand the envelope over to the TTS.
      state.mode = "speaking";
      if (!botBuffer) say("…", "", true);
      playWavB64(p.data).catch(() => {});
      break;

    case "done":
      setAction(false);
      if (state.mode === "thinking") {
        // Stream finished without ever entering "speaking" — so this reply
        // came from typed input (`/chat/stream` carries no audio). Voice it
        // now, which flips the mode to speaking and owns the envelope.
        const reply = botBuffer;
        botBuffer = "";
        if (reply) {
          invoke("orb_speak_reply", { text: reply }).catch(() => {
            setMode("idle");
            sayClear();
          });
        } else {
          setMode("idle");
          sayClear();
        }
        return;
      }
      botBuffer = "";
      break;

    case "error":
      setAction(false);
      say(p.text || "Something went wrong.", "", false);
      setMode("idle");
      setTimeout(sayClear, 3600);
      botBuffer = "";
      break;
  }
});

/* ---- interaction ------------------------------------------------------
 * Orb-first model: press to talk, drag to move, double-click to type.
 * ------------------------------------------------------------------------ */

let pressTimer = null;
let dragging = false;
let dragStart = null;
let winOrigin = null;

canvas.addEventListener("pointerdown", (e) => {
  if (e.button !== 0) return;
  dragStart = { x: e.screenX, y: e.screenY };
  // Logical window origin at press time; the drag is origin + cursor delta.
  winOrigin = win.outerPosition().then((p) => ({
    x: p.x / window.devicePixelRatio,
    y: p.y / window.devicePixelRatio,
  }));
  pressTimer = setTimeout(() => {
    // Held: start listening.
    pressTimer = null;
    startListening();
  }, 200);
});

// On the WINDOW so the drag keeps tracking once the cursor leaves the 150px orb.
// Manual set_position, not win.startDragging(): that needs NSApp.currentEvent to
// still be the mouse event when the async IPC lands, which it isn't on the panel.
window.addEventListener("pointermove", async (e) => {
  if (!dragStart) return;
  const dx = e.screenX - dragStart.x;
  const dy = e.screenY - dragStart.y;
  if (!dragging && Math.hypot(dx, dy) > 6) {
    dragging = true;
    clearTimeout(pressTimer);
    pressTimer = null;
  }
  if (!dragging) return;
  const o = await winOrigin;
  win.setPosition(new LogicalPosition(o.x + dx, o.y + dy)).catch(() => {});
});

/**
 * Release handling lives on the WINDOW, not on the canvas.
 *
 * The orb is only 150x150, so lifting the button a few pixels off the sphere
 * means the canvas never sees `pointerup` — the recorder would stay open,
 * capturing the room and (because the reply is spoken aloud) transcribing its
 * own voice in a loop. `pointercancel` covers the same ground for gestures
 * the system takes over.
 */
function releasePress() {
  const wasTap = pressTimer !== null;
  if (pressTimer) {
    clearTimeout(pressTimer);
    pressTimer = null;
  }
  // Released before the hold threshold: a plain tap, so don't record.
  // Held past it: the 200ms timer already started listening — stop it now.
  if (!wasTap && state.mode === "listening") stopListening();
  dragStart = null;
  dragging = false;
}

window.addEventListener("pointerup", releasePress);
window.addEventListener("pointercancel", releasePress);

canvas.addEventListener("pointerleave", () => {
  if (dragging) return;
  dragStart = null;
  if (pressTimer) {
    clearTimeout(pressTimer);
    pressTimer = null;
  }
});

canvas.addEventListener("contextmenu", (e) => {
  e.preventDefault();
  if (state.mode === "listening") stopListening();
});

canvas.addEventListener("dblclick", () => {
  // Not `window.prompt()`: the orb is a non-activating panel, so its webview
  // can never take focus and the dialog would return null instantly. The
  // caption strip is a normal window — Rust focuses that instead.
  invoke("orb_prompt").catch(() => {});
});

listen("orb-prompt", (ev) => {
  const text = (ev.payload && ev.payload.text) || "";
  if (text.trim()) sendText(text.trim());
});

window.addEventListener("keydown", (e) => {
  if (e.key === "Escape" && state.mode === "listening") stopListening();
});

/* ---- boot ------------------------------------------------------------- */

(async function boot() {
  try {
    const s = await invoke("orb_status");
    if (!s.reachable) {
      statusEl.hidden = false;
      statusEl.title = `Clixen backend unreachable (${s.backend})\n${s.error || ""}`;
      say(`Clixen isn't running — start core.py`, "", false);
      setTimeout(sayClear, 5000);
    }
  } catch (e) {
    statusEl.hidden = false;
    statusEl.title = String(e);
  }
})();
