import { invoke } from "@tauri-apps/api/core";
import { loadSprites, drawFrame, frameAt } from "./sprites.js";
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

const canvas = /** @type {HTMLCanvasElement} */ (document.getElementById("orb"));
const ctx = canvas.getContext("2d");
const statusEl = /** @type {HTMLElement} */ (document.getElementById("status"));
const win = getCurrentWindow();

const ORB_R = 55;      // sphere radius in logical px
const ORB_CY = 90;     // centre of the 150x180 window
const ORB_BREATH = 1;  // breathing scale applied to the idle envelope

let sprites = null;
loadSprites().then((s) => { sprites = s; }).catch((e) => console.warn("orb sprites unavailable:", e));

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
  runningTools: new Set(),  // tools in flight, from "action" events
  lastAction: "",
  lastActionFailed: "",
};

// Continuous-listening toggle (declared here, not in the mic section, because
// draw() reads it before that section is evaluated).
let micOn = false;

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
  // A finished turn must also retire the overlay's parked bunny: it is drawn on
  // the click-through cursor window, so an idle one is a bunny you can see but
  // never click.
  if (mode === "idle") emit("cursor-park-fade").catch(() => {});
  if (mode === "idle") state.targetAmp = 0;
}

// Backend session states → orb visuals. `speaking` is applied without
// setMode() so a running tool (acting) keeps ownership of the cursor.
function applyVoiceState(s) {
  if (s === "hearing") { setMode("listening"); return; }
  if (s === "listening") { setMode(micOn ? "listening" : "idle"); return; }
  if (s === "thinking") { setMode("thinking"); return; }
  if (s === "speaking") { state.mode = "speaking"; return; }
  if (s === "idle" && !act.on) setMode(micOn ? "listening" : "idle");
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

// Rounded-rect path helper (ctx.roundRect is Safari 16+/newer WKWebView only).
function rr(x, y, w, h, r) {
  ctx.beginPath();
  if (typeof ctx.roundRect === "function") ctx.roundRect(x, y, w, h, r);
  else ctx.rect(x, y, w, h);
}

/* Pixel-art hover chips, drawn with hard 2px cells (no anti-aliasing) from the
 * bunny sprite's sampled palette: black outline, pale grey, white, warm orange. */
const PX = {
  K: "rgb(0,0,0)",          // black outline (sprite outline)
  G: "rgb(203,204,204)",    // pale grey (bunny body)
  W: "rgb(241,241,241)",    // white (bunny highlight)
  O: "rgb(245,191,128)",    // warm orange (bunny accent)
};
const CHIP_ICONS = {
  // 8x8 grids; '.' = chip background.
  mic: [
    "..KKKK..",
    ".KWWWWK.",
    ".KWWWWK.",
    ".KWWWWK.",
    "K......K",
    "KK....KK",
    ".KKKKKK.",
    "...OO...",
  ],
  chat: [
    ".KKKKKK.",
    "KWWWWWWK",
    "KOWOWOWK",
    "KWWWWWWK",
    ".KKKKKK.",
    ".KK.....",
    "K.......",
    "........",
  ],
};
function drawPixelChip(cx, cy, icon, active = false) {
  const grid = CHIP_ICONS[icon];
  const cell = 4;                 // 8x8 icon = 32px, chip = 36px with border
  const x0 = cx - 18, y0 = cy - 18;
  // Chip body: black 2px border around a pale grey field, like a sprite tile.
  // `active` (mic listening) fills the field with the warm accent so the
  // toggle state is visible at a glance, not only by the orb's colour.
  ctx.fillStyle = PX.K;
  ctx.fillRect(x0, y0, 36, 36);
  ctx.fillStyle = active ? PX.O : PX.G;
  ctx.fillRect(x0 + 2, y0 + 2, 32, 32);
  // Glyph cells.
  for (let r = 0; r < grid.length; r++) {
    for (let c = 0; c < grid[r].length; c++) {
      const ch = grid[r][c];
      if (ch === ".") continue;
      ctx.fillStyle = PX[ch];
      ctx.fillRect(x0 + 2 + c * cell, y0 + 2 + r * cell, cell, cell);
    }
  }
}

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

  // Hover indicators: mic (listen) and chat (open input), shown instantly while
  // the pointer is over the bunny itself. Drawn as tiny pixel-art chips using
  // the bunny sprite's own palette (black outline, pale grey body, white, warm
  // orange) so they read as part of the same artwork.
  hoverBtns = [];
  hoverAlpha = hovered ? 1 : 0; // instant — chips appear the moment the pointer reaches the bunny
  if (hoverAlpha > 0.02) {
    const btns = [
      { dx: -26, icon: "mic", active: micOn, action: toggleMic },
      { dx: 26, icon: "chat", action: () => invoke("orb_prompt").catch(() => {}) },
    ];
    for (const b of btns) {
      const bx = cx + b.dx, by = cy - ORB_R - 4;
      ctx.save();
      ctx.globalAlpha = hoverAlpha;
      drawPixelChip(bx, by, b.icon, !!b.active);
      ctx.restore();
      hoverBtns.push({ x: bx, y: by, r: 20, icon: b.icon, action: b.action });
    }
  }
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

  /* Outer bloom removed — the bunny sits on a fully transparent background. */

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

  /* The bunny — replaces the blue sphere. */
  if (sprites && sprites.idle) {
    const sp = sprites.idle;
    const px = 4;
    const dpr = window.devicePixelRatio || 1;
    const k = px / dpr;
    const spriteH = sp.h * k;
    drawFrame(ctx, sp, frameAt(sp, state.t), cx, cy + ORB_R, px, dpr, false, 1 - 0.4 * act.k, 1, 1, 0);
  } else {
    /* fallback sphere */
    const r = ORB_R * (1 + a * 0.06);
    const body = ctx.createRadialGradient(cx - r * 0.34, cy - r * 0.40, r * 0.08, cx, cy, r * 1.12);
    body.addColorStop(0, rgb(pal.hot));
    body.addColorStop(0.34, rgb(mix(pal.hot, pal.mid, 0.75)));
    body.addColorStop(0.66, rgb(pal.mid));
    body.addColorStop(1, rgb(pal.cold));
    ctx.fillStyle = body;
    ctx.beginPath();
    ctx.arc(cx, cy, r, 0, Math.PI * 2);
    ctx.fill();
  }
}

let hovered = false;
let hoverAlpha = 0; // eased 0..1 visibility of the hover buttons
let hoverBtns = []; // {x,y,r,action}

// Show the mic/chat buttons only when the pointer is over the bunny itself
// (within its radius) or over one of the buttons, not merely anywhere in the
// transparent window. Without the button check the icons would fade out while
// the pointer travels from the bunny up to a button (they sit ~67px from the
// orb centre, beyond the ORB_R+6 bunny zone).
const BTN_DY = ORB_R + 4; // matches the draw() offset (cy - by)
canvas.addEventListener("pointermove", (e) => {
  const rect = canvas.getBoundingClientRect();
  const x = e.clientX - rect.left;
  const y = e.clientY - rect.top;
  const cx = W / 2, cy = ORB_CY;
  hovered = Math.hypot(x - cx, y - cy) <= ORB_R + 6
    || Math.hypot(x - (cx - 26), y - (cy - BTN_DY)) <= 20
    || Math.hypot(x - (cx + 26), y - (cy - BTN_DY)) <= 20;
});
canvas.addEventListener("pointerleave", () => { hovered = false; dragStart = null; });

// Draw hover indicators inside draw() after orb
const origDraw = draw;

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
  return audioCtx;
}

// WebKit only lets an AudioContext leave "suspended" inside a user gesture, and
// it grants that most reliably when the context is CONSTRUCTED synchronously in
// the handler. TTS frames arrive later over SSE (no gesture of their own), so
// unlock the output context on the press that opens the mic — otherwise decode
// succeeds and `start()` runs silently on a suspended context.
async function unlockAudio() {
  const c = ensureAudioCtx();
  if (c.state === "suspended") {
    try {
      await c.resume();
    } catch (e) {
      dbg(`audio unlock failed: ${e && e.message ? e.message : e}`);
    }
  }
  dbg(`audio unlocked ctx=${c.state}`);
}
function unlockAudioNow() {
  // Synchronous path: build the context while the gesture is still live, which
  // is what WebKit's autoplay gate keys off (activation, not the resume call).
  unlockAudio(); // fire-and-forget; the await below in playWavB64 re-checks
}
canvas.addEventListener("pointerdown", unlockAudioNow, { capture: true });
canvas.addEventListener("touchstart", unlockAudioNow, { capture: true, passive: true });
window.addEventListener("keydown", unlockAudioNow, { capture: true });

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
// Bumped on every start/stop: a getUserMedia that resolves after the press
// already ended must not start recording (nor leave the caption stranded).
let listenSeq = 0;
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
  // Opening the mic takes a TCC round-trip; a quick press-and-hold can end
  // before it returns. Every start/stop bumps this, so a stream that arrives
  // after the user let go is handed straight back instead of recording into
  // the void (and leaving the caption stuck on "Listening…").
  const seq = ++listenSeq;
  setMode("listening");
  say("Listening…", "you", true);
  recordedChunks = [];

  let stream;
  try {
    stream = await navigator.mediaDevices.getUserMedia({
      audio: { echoCancellation: true, noiseSuppression: true, channelCount: 1 },
    });
  } catch (e) {
    if (seq === listenSeq) {
      say(`Mic unavailable: ${e.message || e}`, "", false);
      setMode("idle");
    }
    return;
  }
  if (seq !== listenSeq) {
    stream.getTracks().forEach((t) => t.stop());
    return;
  }
  mediaStream = stream;

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
  listenSeq++;   // any getUserMedia still in flight is stale from here on
  if (!mediaRecorder || mediaRecorder.state === "inactive") {
    teardownMic();
    // Nothing was recorded (the press ended before the mic opened): put the
    // orb back to rest instead of leaving "Listening…" waiting for a reply.
    if (!micOn) {
      setMode("idle");
      sayClear();
    }
    return Promise.resolve();
  }
  // Resolve only once the recorder has fully stopped: while a mic stream is
  // live the window server wedges programmatic moves (empirically — a drag
  // during listening moves the window zero pixels), so callers that need to
  // move the orb must await this before dragging.
  return new Promise((resolve) => {
    mediaRecorder.addEventListener("stop", resolve, { once: true });
    mediaRecorder.stop();
  });
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

async function sendAudio(blob, turnId = "") {
  setMode("thinking");
  say("…", "you", true);
  try {
    const audio_b64 = await blobToB64(blob);
    await invoke("orb_send_audio", { audioB64: audio_b64, tts: true, turnId });
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

/* ---- continuous listening (mic chip) ----------------------------------
 * Click the mic: the backend session listens, turn after turn, until you
 * click it again. Capture happens here (WebAudio + echoCancellation) and is
 * streamed as 16 kHz mono PCM; the backend runs VAD, streaming ASR, the turn
 * and TTS, then pushes state/partial/final/audio frames back over
 * /voice/events. Never a second mic: hold-to-talk stays MediaRecorder-based,
 * and the hold path is disabled while continuous mode owns the device.
 * ---------------------------------------------------------------------- */

let micCtx = null;
let micStream = null;
let micSrc = null;
let micNode = null;
// Half-duplex gate: while a reply is playing the mic stream stays open but its
// frames go out as silence. Set from the audio processor, which is the only
// place that needs to know.
let micGate = false;

const PCM_RATE = 16000;
const FRAME_SAMPLES = 480;   // 30 ms at 16 kHz — the session's frame size

function b64FromBytes(bytes) {
  let s = "";
  for (let i = 0; i < bytes.length; i++) s += String.fromCharCode(bytes[i]);
  return btoa(s);
}

async function startContinuousMic() {
  micStream = await navigator.mediaDevices.getUserMedia({
    audio: { echoCancellation: true, noiseSuppression: true, channelCount: 1 },
  });
  micCtx = new (window.AudioContext || window.webkitAudioContext)();
  micSrc = micCtx.createMediaStreamSource(micStream);

  // -------------------------------------------------------------------------
  // Proper 48k -> 16k decimation. The old code picked every 3rd sample with a
  // drift-prone floating `phase` and NO low-pass filter, so every frequency
  // above 8 kHz aliased down into the speech band and muddied the signal
  // (the "Hello can you can you can you" garbling).
  //
  // This does it correctly: a windowed-sinc FIR low-pass at 8 kHz (Nyquist of
  // 16 kHz), then decimate by the integer ratio. The FIR is applied to the
  // rolling input history via a filter that only ever emits at the downsampled
  // rate, so no transient folds back.
  // -------------------------------------------------------------------------

  const ratio = Math.round(micCtx.sampleRate / PCM_RATE); // 3 at 48 kHz
  const TAPS_PER_RATIO = 16;                              // filter length = 3*16 = 48 taps
  const NTAPS = ratio * TAPS_PER_RATIO;
  const cutoff = 1.0 / ratio;                             // 0..1 relative to source Nyquist

  // Precompute a windowed-sinc (Blackman) low-pass kernel, normalised to DC gain 1.
  const coeffs = new Float64Array(NTAPS);
  let sum = 0;
  for (let n = 0; n < NTAPS; n++) {
    const x = n - (NTAPS - 1) / 2;
    // sinc(2*cutoff*x) with cutoff in units of pi
    const sinc = x === 0 ? 2 * cutoff : Math.sin(2 * Math.PI * cutoff * x) / (Math.PI * x);
    const win = 0.42 - 0.5 * Math.cos((2 * Math.PI * n) / (NTAPS - 1)) + 0.08 * Math.cos((4 * Math.PI * n) / (NTAPS - 1));
    coeffs[n] = sinc * win;
    sum += coeffs[n];
  }
  for (let n = 0; n < NTAPS; n++) coeffs[n] /= sum; // DC gain 1

  // Rolling input history (ring buffer) for the FIR. `ring` holds the last
  // NTAPS input samples, newest first (ring[0] is the most recent sample).
  const ring = new Float64Array(NTAPS);
  let filled = 0;        // how many valid samples the ring currently holds
  let sampleCount = 0;   // total input samples consumed (exact, no float drift)
  let pending = [];      // resampled floats waiting to be framed

  const node = micCtx.createScriptProcessor(2048, 1, 1);
  node.onaudioprocess = (ev) => {
    if (!micOn) return;
    const input = ev.inputBuffer.getChannelData(0);
    // A reply is on the wire: from here until it ends, the samples we forward are
    // zeros. Toggling is logged because "the mic went deaf for a second" is
    // otherwise invisible while debugging.
    if (audioBusy !== micGate) {
      micGate = audioBusy;
      dbg(micGate ? "mic gated — reply playing" : "mic open again");
    }

    for (let i = 0; i < input.length; i++) {
      // Shift the ring right: newest sample goes to index 0.
      ring.copyWithin(1, 0, NTAPS - 1);
      ring[0] = input[i];
      if (filled < NTAPS) filled++;
      sampleCount++;

      // Emit one resampled point when the input index hits a decimation
      // multiple (ratio-step). `sampleCount % ratio === 0` uses an integer
      // counter, so the 3:1 decimation can never drift.
      if (filled >= NTAPS && sampleCount % ratio === 0) {
        // Convolve the FIR: ring[n] holds input[t - n], coeffs[n] weights it.
        let acc = 0;
        for (let n = 0; n < NTAPS; n++) {
          acc += coeffs[n] * ring[n];
        }
        pending.push(acc);
      }
    }

    while (pending.length >= FRAME_SAMPLES) {
      const pcm = new Int16Array(FRAME_SAMPLES);
      if (micGate) {
        // Half-duplex: the reply is playing, so send silence. A zero frame keeps
        // the backend's frame clock running (its VAD can endpoint the utterance)
        // while making sure the orb never hears itself.
      } else {
        for (let i = 0; i < FRAME_SAMPLES; i++) {
          const v = Math.max(-1, Math.min(1, pending[i]));
          pcm[i] = v < 0 ? v * 0x8000 : v * 0x7fff;
        }
      }
      pending = pending.slice(FRAME_SAMPLES);
      invoke("orb_feed_audio", { audioB64: b64FromBytes(new Uint8Array(pcm.buffer)) })
        .catch(() => {});
    }
  };
  micSrc.connect(node);
  node.connect(micCtx.destination);   // a ScriptProcessor only runs with a sink
  micNode = node;
}

function stopContinuousMic() {
  try { if (micNode) micNode.disconnect(); } catch { /* already gone */ }
  try { if (micSrc) micSrc.disconnect(); } catch { /* already gone */ }
  try { if (micStream) micStream.getTracks().forEach((t) => t.stop()); } catch { /* already gone */ }
  try { if (micCtx) micCtx.close(); } catch { /* already gone */ }
  micNode = micSrc = micStream = micCtx = null;
  micGate = false;   // a fresh stream starts open
}

async function toggleMic() {
  if (micOn) {
    micOn = false;
    stopContinuousMic();
    setMode("idle");
    sayClear();
    invoke("orb_listen", { on: false }).catch(() => {});
    dbg("mic off");
    return;
  }
  try {
    await invoke("orb_events_start").catch(() => {});
    const res = await invoke("orb_listen", { on: true });
    if (!res || res.listening !== true) throw new Error("backend refused to listen");
    await startContinuousMic();
    micOn = true;
    setMode("listening");
    say("Listening…", "you", true);
    dbg(`mic on (${res.mic || "default"}, chat ${res.chat_id})`);
  } catch (e) {
    micOn = false;
    stopContinuousMic();
    say(`Mic unavailable: ${e.message || e}`, "", false);
    setMode("idle");
    invoke("orb_listen", { on: false }).catch(() => {});
  }
}

/* ---- backend → orb ---------------------------------------------------- */

let currentTurnId = "";   // tags every event to the voice turn it belongs to


let botBuffer = "";

/**
 * TTS clips arrive one per sentence — usually faster than they play. Starting
 * each on arrival makes neighbouring sentences overlap, which sounds like the
 * same reply spoken twice. Queue them and play strictly one at a time.
 */
let pendingAudio = 0;   // clips queued + currently playing
let audioQueue = [];
let audioBusy = false;
let currentSrc = null;
let currentEl = null;

function endClip() {
  pendingAudio = Math.max(0, pendingAudio - 1);
  if (pendingAudio === 0 && state.mode === "speaking") {
    detachAnalyser();
    setMode("idle");
    sayClear();
  }
}

/** Drop everything queued/playing — used by barge-in and mic-on ducking. */
function stopSpeech() {
  audioQueue.length = 0;
  pendingAudio = 0;
  try { if (currentSrc) currentSrc.stop(); } catch { /* already ended */ }
  try { if (currentEl) { currentEl.pause(); currentEl.src = ""; } } catch { /* already gone */ }
  currentSrc = currentEl = null;
  detachAnalyser();
  if (state.mode === "speaking") { setMode("idle"); sayClear(); }
}

function queueSpeech(b64) {
  if (!b64) return;
  // Half-duplex, not mute: the reply always plays. While a clip is playing the
  // mic keeps its stream open but feeds silence downstream (see the gate in
  // startContinuousMic), so the orb never transcribes its own voice and the
  // backend still receives a steady frame clock to endpoint on. Between clips —
  // while the next sentence is still being synthesized — the gate is open, which
  // is exactly when a barge-in should land.
  audioQueue.push(b64);
  pendingAudio += 1;
  void pumpSpeech();
}

async function pumpSpeech() {
  if (audioBusy) return;
  const b64 = audioQueue.shift();
  if (!b64) return;
  audioBusy = true;
  try {
    await playWavB64(b64);
  } finally {
    audioBusy = false;
    if (audioQueue.length) void pumpSpeech();
  }
}

async function playWavB64(b64) {
  try {
    if (!b64) throw new Error("empty audio frame");
    const bytes = Uint8Array.from(atob(b64), (c) => c.charCodeAt(0));
    const ctx = ensureAudioCtx();
    if (ctx.state === "suspended") await ctx.resume().catch(() => {});
    // WebKit can leave the context suspended (the unlock gesture happened on a
    // non-focusing panel). Media elements are exempt under wry's autoplay
    // policy, so fall back to one instead of dropping the clip.
    if (ctx.state !== "running") {
      const url = URL.createObjectURL(new Blob([bytes], { type: "audio/wav" }));
      const el = new Audio(url);
      currentEl = el;
      await new Promise((resolve) => {
        let settled = false;
        const done = () => {
          if (settled) return;
          settled = true;
          URL.revokeObjectURL(url);
          currentEl = null;
          endClip();
          resolve();
        };
        el.onended = done;
        el.onerror = () => { dbg("audio failed: <audio> error"); done(); };
        el.play().then(() => dbg(`audio ${Math.round((el.duration || 0) * 1000)}ms via <audio>`))
          .catch((e) => {
            dbg(`audio blocked: ctx=${ctx.state} (${e && e.message ? e.message : e})`);
            say("Tap the orb, then ask again", "", false);
            done();
          });
      });
      return;
    }
    const buf = await ctx.decodeAudioData(bytes.buffer);
    const src = ctx.createBufferSource();
    src.buffer = buf;
    // The analyser alone is a dead end — a node that nothing downstream reads
    // from never reaches the speakers. Route it to the output as well.
    src.connect(ctx.destination);
    attachAnalyser(src);
    currentSrc = src;
    await new Promise((resolve) => {
      src.onended = () => { currentSrc = null; endClip(); resolve(); };
      src.start();
      dbg(`audio ${Math.round(buf.duration * 1000)}ms ctx=${ctx.state}`);
    });
  } catch (e) {
    dbg(`audio failed: ${e && e.message ? e.message : e}`);
    endClip();
  }
}

// Wake-word streaming: subscribe to /voice/stream SSE (backend sends wake-word
// events pushed via /voice/ingest). Each utterance arrives as
// {"type": "voice", "query": "...", "turn_id": "..."}.
let voiceStreamListener = null;
const DOUBLE_TAP_WINDOW = 300;
let lastTap = null;

async function startListeningStream() {
  try {
    const resp = await fetch('http://127.0.0.1:9234/voice/stream', {headers: {'X-Auth-Token': ''}});
    if (!resp.ok) return false;
    const reader = resp.body.getReader();
    const decoder = new TextDecoder();
    while (true) {
      const { done, value } = await reader.read();
      if (done) break;
      const text = decoder.decode(value);
      for (const line of text.split('\n')) {
        const m = line.match(/^data: (.*)$/);
        if (!m) continue;
        try {
          const ev = JSON.parse(m[1]);
          if (ev.type === 'voice') {
            say(ev.query, 'you', true);
          }
        } catch (_) { /* ignore malformed frames */ }
      }
    }
  } catch (_) { /* stream died — retry on next tap */ }
  return false;
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
  if (micOn) { toggleMic(); return; }
  if (state.mode === "listening") stopListening();
});

listen("orb-event", (ev) => {
  const p = ev.payload;
  if (!p) return;
  // Only filter when a turn is pinned; session events carry their own turn_id
  // and currentTurnId is empty for them.
  if (currentTurnId && p.turn_id && p.turn_id !== currentTurnId) return;
  if (p.kind === "cancel") {
    if (currentTurnId) {
      stopSpeech();
      setAction(false);
      setMode("idle");
      sayClear();
      currentTurnId = "";
    }
    return;
  }
  switch (p.kind) {
    case "state":
      applyVoiceState(p.state || "idle");
      break;

    case "partial":
      // Live streaming transcript: the caption grows while you talk.
      setAction(false);
      setMode("listening");
      say(p.text, "you", true);
      break;

    case "final":
      // Endpoint: this is what the agent is answering.
      setAction(false);
      botBuffer = "";
      say(p.text, "you", true);
      setMode("thinking");
      break;

    case "interrupt":
      // You talked over the reply (or cancelled): the turn is gone.
      stopSpeech();
      botBuffer = "";
      setAction(false);
      sayClear();
      setMode(micOn ? "listening" : "idle");
      break;

    case "warning":
      console.warn("orb voice:", p.text || p);
      break;

    case "transcript":
      say(p.text, "you", true);
      break;

    case "action": {
      // Structured tool events: started / done with an outcome and a duration.
      // Before this the bunny animated off a scraped "⚙️ Running <tool>…" string in
      // the narration, so it could not know whether the call worked.
      const name = p.tool || "tool";
      // `ok` is absent on a `started` frame; only an explicit false is a failure.
      // The first version tested `!p.ok`, which silently reported every action as
      // failed once the Rust pump turned out not to forward the field.
      const failed = p.ok === false;
      if (!state.runningTools) state.runningTools = new Set();
      if (p.phase === "started") {
        state.runningTools.add(name);
        state.lastAction = name;
        setAction(true);
        state.mode = state.mode === "speaking" ? "speaking" : "thinking";
        dbg(`action start ${name}`);
      } else {
        state.runningTools.delete(name);
        if (failed) {
          state.lastActionFailed = name;
          dbg(`action failed ${name}: ${p.error || ""}`);
        } else {
          dbg(`action done ${name} ${p.ms || 0}ms`);
        }
        // Only stop the action pose once nothing else is in flight — batched calls
        // arrive as several overlapping start/done pairs.
        if (state.runningTools.size === 0) setAction(false);
      }
      break;
    }

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
      queueSpeech(p.data);
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

    case "ignored":
      // The VAD heard something that is not a turn — a cough, a door, room tone.
      // No reply is coming, so drop the caption instead of leaving "Listening…"
      // on screen waiting for an answer that will never arrive.
      setAction(false);
      botBuffer = "";
      if (micOn) {
        setMode("listening");
        say("Listening…", "you", true);
      } else {
        setMode("idle");
        sayClear();
      }
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
 *
 * Gesture lifecycle (single pointer, primary button only):
 *   pointerdown on chip   -> chip action; gesture ends (no tap/hold/drag armed)
 *   pointerdown on body   -> PRESSED: arm hold timer (HOLD_MS) + drag origin
 *   PRESSED + move > SLOP -> DRAG: window follows the cursor until release;
 *                            if the hold already opened the mic, close it —
 *                            the mic must never record through a reposition
 *   PRESSED >= HOLD_MS    -> HOLD: mic opens; release closes it
 *   release < HOLD_MS     -> TAP: inert on its own; two taps within
 *                            DOUBLE_TAP_WINDOW during acting/speaking cancel
 *                            the turn (and suppress the prompt dblclick);
 *                            a clean double-click opens the typing prompt
 *   right-click           -> closes the mic if it is open
 * Every terminal path (pointerup / pointercancel / pointerleave / a move with
 * the button no longer down) resets ALL gesture state, so a lost mouse-up can
 * never wedge the orb in drag mode.
 * ------------------------------------------------------------------------ */

const HOLD_MS = 200;   // press-and-hold before the mic opens
// 10 px, not 6: a trackpad tap routinely drags 4-8 px between press and release,
// and the old threshold turned those taps into drags — the orb slid a few pixels
// when you meant to click it, and a press-and-hold lost its hold. A real drag is
// deliberate movement, so the extra 4 px cost nothing.
const DRAG_SLOP = 10;  // movement that separates a drag from a hold/tap
const DBG = true;      // gesture trace via orb_debug -> stderr (run from a shell)
const dbg = (m) => { if (DBG) invoke("orb_debug", { msg: m }).catch(() => {}); };

// Every click that lands in the webview is logged once, at the window level.
// If a report says "the bunny isn't clickable", the presence of this line means
// the click arrived and a gesture swallowed it; its absence means the click
// never reached the webview at all (a window-level problem: pass-through on, a
// stale ignoresMouseEvents, or something stacked above the panel).
window.addEventListener("pointerdown", (e) => {
  dbg(`win down btn=${e.button} @${e.clientX},${e.clientY} target=${e.target && e.target.id}`);
}, { capture: true });
// Enter/leave tell us whether the window is receiving mouse input at all, which
// is the difference between "the gesture was swallowed" and "there were no
// events to swallow".
window.addEventListener("pointerenter", () => dbg("win pointerenter"), { capture: true });
window.addEventListener("pointerleave", () => dbg("win pointerleave"), { capture: true });

let pressTimer = null;
let dragging = false;
let dragStart = null;
let winOrigin = null;
let holdListening = false;     // mic was opened by the HOLD timer (drag closes it)
let suppressPromptUntil = 0;   // a barge-in double-tap swallows its dblclick
let micBusy = false;           // recorder teardown in flight — moves wedge then

function endDrag() {
  dragging = false;
  dragStart = null;
  winOrigin = null;
}

canvas.addEventListener("pointerdown", (e) => {
  const rect = canvas.getBoundingClientRect();
  const x = e.clientX - rect.left;
  const y = e.clientY - rect.top;
  // 1) Hover chip clicks consume the gesture outright — no tap/hold/drag armed,
  //    so releasing over a chip can never stop what the chip started.
  for (const b of hoverBtns) {
    if (Math.hypot(x - b.x, y - b.y) <= b.r) {
      dbg(`down chip ${b.icon} @${x},${y}`);
      b.action();
      return;
    }
  }
  if (e.button !== 0) return;
  // 2) Press on the body: arm hold + drag.
  // Capture the pointer so drag tracking keeps flowing to the canvas even when
  // the cursor momentarily slides off the orb onto the transparent margin.
  try { canvas.setPointerCapture(e.pointerId); } catch { /* older WKWebView */ }
  dbg(`down body @${x},${y}`);
  dragStart = { x: e.screenX, y: e.screenY };
  // Logical window origin at press time; the drag is origin + cursor delta.
  winOrigin = win.outerPosition().then((p) => ({
    x: p.x / window.devicePixelRatio,
    y: p.y / window.devicePixelRatio,
  }));
  holdListening = false;
  pressTimer = setTimeout(() => {
    pressTimer = null;
    if (micOn) { dbg("hold ignored — the continuous mic owns the device"); return; }
    holdListening = true;
    dbg("hold -> listening");
    startListening();
  }, HOLD_MS);
});

// On the WINDOW so the drag keeps tracking once the cursor leaves the orb.
// Manual set_position, not win.startDragging(): that needs NSApp.currentEvent to
// still be the mouse event when the async IPC lands, which it isn't on the panel.
window.addEventListener("pointermove", async (e) => {
  if (!dragStart) return;
  const dx = e.screenX - dragStart.x;
  const dy = e.screenY - dragStart.y;
  // Lost pointerup recovery: the primary button is no longer down, so whatever
  // gesture was in flight is over — end it instead of chasing a ghost drag.
  if (dragging && !(e.buttons & 1)) {
    dbg("move with button up -> end drag");
    endDrag();
    return;
  }
  if (!dragging && Math.hypot(dx, dy) > DRAG_SLOP) {
    dragging = true;
    clearTimeout(pressTimer);
    pressTimer = null;
    if (holdListening) {
      holdListening = false;
      dbg("drag after hold -> stop listening");
      // A live mic stream wedges window moves, so finish the teardown before
      // the first setPosition and hold off further moves until it completes.
      micBusy = true;
      stopListening().finally(() => { micBusy = false; });
    }
    dbg(`drag engage d=${Math.hypot(dx, dy).toFixed(1)}`);
  }
  if (!dragging) return;
  if (micBusy) return; // moves during mic teardown are dropped on the floor
  const o = await winOrigin;
  if (!dragging || !o) return; // gesture ended while awaiting the origin
  try {
    await win.setPosition(new LogicalPosition(o.x + dx, o.y + dy));
    dbg(`setPosition -> ${Math.round(o.x + dx)},${Math.round(o.y + dy)}`);
  } catch (err) {
    dbg(`setPosition FAILED: ${err}`);
  }
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
  dbg(`release wasTap=${wasTap} dragging=${dragging} holdListening=${holdListening} mode=${state.mode}`);
  // Held past the threshold with no drag: the timer opened the mic — close it.
  // Only the HOLD path may close the mic here; a mic opened via the chip (or a
  // reply being spoken) must survive the release that follows the chip click.
  if (!wasTap && !dragging && holdListening) {
    holdListening = false;
    dbg("release after hold -> stop listening");
    stopListening();
  }

  // Double-tap barge-in: track tap timing; if two taps land within
  // DOUBLE_TAP_WINDOW ms while a turn is executing, cancel it. Single taps
  // are ignored to avoid accidental cancels.
  if (wasTap && (state.mode === 'acting' || state.mode === 'speaking')) {
    const now = Date.now();
    if (lastTap && now - lastTap < DOUBLE_TAP_WINDOW) {
      // Double tap detected — cancel the current turn. The browser will still
      // raise a dblclick for these two taps; swallow it so the cancel doesn't
      // also pop the typing prompt.
      invoke('orb_cancel', { chat_id: 'orb_ui' }).catch(() => {});
      suppressPromptUntil = now + 400;
      lastTap = null; // reset so a third tap doesn't immediately double-tap again
      dbg("double-tap -> cancel turn");
    } else {
      lastTap = now;
      setTimeout(() => { if (lastTap === now) lastTap = null; }, DOUBLE_TAP_WINDOW);
    }
  }

  endDrag();
}

window.addEventListener("pointerup", releasePress);
window.addEventListener("pointercancel", releasePress);

canvas.addEventListener("pointerleave", () => {
  if (dragging) return;
  // Only worth a line when a press was actually in flight; a plain "the pointer
  // left the orb" used to log like a cancelled gesture and muddied the trace.
  if (pressTimer || dragStart) dbg("pointerleave while pressed -> cancel hold");
  dragStart = null;
  if (pressTimer) {
    clearTimeout(pressTimer);
    pressTimer = null;
  }
});

canvas.addEventListener("contextmenu", (e) => {
  e.preventDefault();
  if (micOn) { toggleMic(); return; }
  if (state.mode === "listening") stopListening();
});

canvas.addEventListener("dblclick", () => {
  if (Date.now() < suppressPromptUntil) {
    dbg("dblclick suppressed (was barge-in cancel)");
    return;
  }
  dbg("dblclick -> prompt");
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
  if (e.key !== "Escape") return;
  if (micOn) { toggleMic(); return; }
  if (state.mode === "listening") stopListening();
});

/* ---- boot ------------------------------------------------------------- */

(async function boot() {
  // One long-lived SSE consumer for /voice/events (session states, partials,
  // finals, audio). Idempotent in Rust; harmless if the backend is down.
  invoke("orb_events_start").catch(() => {});
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
