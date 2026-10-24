"""Shared Playwright harness: the real renderer, mocked Tauri IPC.

Why this exists: the orb's behaviour lives in the webview — gesture routing,
canvas hit-testing, the 48k→16k mic resampler, `orb_feed_audio`, WebAudio
playback. Playwright runs those real modules; only the native boundary is
faked. That catches the bugs the app actually shipped (an ungranted
`orb_listen`, a silent `.catch` around TTS) without a signed bundle or a
physical microphone.

Limits, deliberately:
  * The mock bypasses the ACL, so `cargo test` (tests/security_config.rs) stays
    the guard for capabilities and command registration.
  * This cannot validate real WKWebView autoplay policy or macOS TCC mic
    permissions. The bundled `.app` remains the final smoke test.
"""
import base64
import contextlib
import math
import struct
import subprocess
import time
import urllib.request
import wave
from io import BytesIO

ORB_VIEWPORT = {"width": 150, "height": 180}
MIC_CHIP = (49, 31)  # cx-26, cy-(ORB_R+4) in the orb's 150x180 window
ORB_BODY = (75, 90)

_BRIDGE = r"""
window.__calls = [];
window.__h = {};
window.__TAURI_INTERNALS__ = {
  metadata: { currentWindow: { label: "orb" }, currentWebview: { label: "orb" } },
  transformCallback(cb) {
    const id = Math.floor(Math.random() * 1e9);
    window["_" + id] = cb;
    return id;
  },
  invoke(cmd, args) {
    window.__calls.push({ cmd, args, t: performance.now() });
    if (cmd === "plugin:event|listen") {
      window.__h[args.event] = window["_" + args.handler];
      return Promise.resolve(0);
    }
    if (cmd === "plugin:event|unlisten" || cmd === "plugin:event|emit") return Promise.resolve(null);
    if (cmd === "orb_listen") return Promise.resolve({
      ok: true, listening: !!args.on, state: args.on ? "listening" : "idle",
      chat_id: "orb_ui", mic: "mock", turns: 0, busy: false, last_rms: 0,
      last_partial: "", last_final: "",
    });
    if (cmd === "orb_status") return Promise.resolve({ reachable: true, backend: "http://127.0.0.1:9234" });
    if (cmd === "plugin:window|outer_position" || cmd === "plugin:window|inner_position")
      return Promise.resolve({ x: 0, y: 0 });
    return Promise.resolve(null);
  },
};
window.__TAURI_EVENT_PLUGIN_INTERNALS__ = { unregisterListener() {} };
navigator.mediaDevices.getUserMedia = async () => ({
  getTracks: () => [{ stop() { window.__micStopped = true; } }],
});
"""

_FAKE_MIC = r"""
window.AudioContext = class {
  constructor() { this.sampleRate = 48000; this.state = "running"; this.destination = {}; }
  resume() { this.state = "running"; return Promise.resolve(); }
  createMediaStreamSource() { return { connect() {}, disconnect() {} }; }
  createScriptProcessor() {
    const n = { onaudioprocess: null, connect() { window.__proc = n; }, disconnect() {} };
    return n;
  }
  createAnalyser() { return { fftSize: 512, getByteTimeDomainData() {} }; }
  createBufferSource() {
    // TTS playback in tests: end on our own clock so a queued clip can advance.
    const s = { connect() {}, onended: null, buffer: null,
      start() { setTimeout(() => s.onended && s.onended(), window.__clipMs || 350); } };
    return s;
  }
  decodeAudioData() { return Promise.resolve({ duration: (window.__clipMs || 350) / 1000 }); }
  close() {}
};
"""


def bridge_script(*, fake_mic: bool = False) -> str:
    """Init script for `page.add_init_script`; `fake_mic` swaps in a capture stub."""
    return _BRIDGE + (_FAKE_MIC if fake_mic else "")


@contextlib.contextmanager
def serve_vite(port: int):
    """Run the Vite dev server for the duration of a test."""
    proc = subprocess.Popen(
        ["npx", "vite", "--port", str(port), "--strictPort"],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    try:
        base = f"http://localhost:{port}"
        for _ in range(60):
            try:
                urllib.request.urlopen(base + "/index.html", timeout=1)
                break
            except Exception:
                time.sleep(0.3)
        else:
            raise RuntimeError("vite did not start")
        yield base
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            proc.kill()


def open_orb(browser, base: str, *, fake_mic: bool = False, errors: list | None = None):
    """Open index.html with the mocked bridge; returns a ready page."""
    page = browser.new_page(viewport=ORB_VIEWPORT)
    if errors is not None:
        page.on("pageerror", lambda e: errors.append(str(e)))
    page.add_init_script(bridge_script(fake_mic=fake_mic))
    page.goto(base + "/index.html")
    page.wait_for_function("!!(window.__h && window.__h['orb-event'])", timeout=5000)
    page.wait_for_timeout(500)  # sprites load, first frame draws the hover chips
    return page


def calls(page, cmd: str) -> list[dict]:
    return [c["args"] for c in page.evaluate("window.__calls") if c["cmd"] == cmd]


def timed_calls(page, cmd: str) -> list[dict]:
    """Entries include `t` (performance.now()) so tests can assert ordering."""
    return [c for c in page.evaluate("window.__calls") if c["cmd"] == cmd]


def fire_event(page, name: str, payload: dict) -> None:
    page.evaluate("([n, p]) => window.__h[n]({ event: n, payload: p })", [name, payload])


def sine_wav_b64(seconds: float = 0.6, freq: float = 440.0, rate: int = 24000, amp: float = 0.4) -> str:
    """A small, valid mono Int16 WAV for the TTS playback path."""
    buf = BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(rate)
        w.writeframes(b"".join(
            struct.pack("<h", int(amp * 32767 * math.sin(2 * math.pi * freq * i / rate)))
            for i in range(int(seconds * rate))
        ))
    return base64.b64encode(buf.getvalue()).decode()


def estimate_hz(pcm: bytes, rate: int = 16000) -> float:
    """Zero-crossing frequency estimate for a clean tone."""
    samples = struct.unpack("<%dh" % (len(pcm) // 2), pcm)
    crossings = sum(1 for a, b in zip(samples, samples[1:]) if (a < 0) != (b < 0))
    return crossings / 2 / (len(samples) / rate) if samples else 0.0
