from __future__ import annotations
import os
import sys
import time
import threading
import json
import urllib.request
from concurrent.futures import Future, ThreadPoolExecutor
from pathlib import Path
from datetime import datetime

import mlx.core as mx
import numpy as np
from parakeet_mlx import BaseParakeet, from_pretrained
from parakeet_mlx.audio import get_logmel
from pynput import keyboard
import sounddevice as sd
import soundfile as sf

SAVE_DIR = Path.home() / "Documents" / "clixen-voice"
SAVE_DIR.mkdir(parents=True, exist_ok=True)

SAMPLERATE = 16000
INPUT_DEVICE = os.environ.get("CLIXEN_PTT_INPUT_DEVICE", "").strip() or None
# English + 24 European languages (incl. French, Spanish), language auto-detected.
PARAKEET_MODEL = "mlx-community/parakeet-tdt-0.6b-v3"
_model: BaseParakeet | None = None
recording = False
frames: list = []
stream = None
lock = threading.Lock()
typer = keyboard.Controller()

# ponytail: set to clixen URL to also send; None = local-only save
CLIXEN_URL = None
CHAT_ID = "brabble_voice"

autotype_this = False  # per-session flag, set by whichever key started the recording
_typing = False          # guard: prevent simulated Shift events from re-triggering the listener during autotype


# MLX streams belong to the thread that created them and die with it (mlx
# >=0.31.2): a model loaded in a startup thread that has since exited raises
# "There is no Stream(cpu, 1) in current thread" when pynput's listener thread
# transcribes. So every MLX call runs on this one long-lived thread; it also
# serializes them, so a recording made during warm-up just waits for it.
_mlx_thread = ThreadPoolExecutor(max_workers=1, thread_name_prefix="mlx")


def _get_model() -> BaseParakeet:  # only ever called on _mlx_thread
    global _model
    if _model is None:
        model = from_pretrained(PARAKEET_MODEL)
        assert model.preprocessor_config.sample_rate == SAMPLERATE
        # Weights load lazily; one pass on 1s of silence pulls them in now so
        # the first real recording doesn't pay for it.
        model.generate(get_logmel(mx.zeros(SAMPLERATE), model.preprocessor_config))
        _model = model
    return _model


def warm_model() -> Future:
    return _mlx_thread.submit(_get_model)


def prewarm_mic() -> bool:
    # ponytail: opens+closes once at startup so the first real recording
    # doesn't eat the device-open latency FluidVoice avoids via pre-warming.
    # Also acts as the mic gate: returns False when the default input can't be
    # opened — macOS TCC mic denial surfaces as "Error querying device -1",
    # and a launchd agent can't prompt for it, so it would otherwise run
    # forever capturing silence.
    try:
        dev = sd.query_devices(kind="input")
        print(f"  input device: {dev['name']} ({int(dev['default_samplerate'])} Hz)", flush=True)
    except Exception:
        pass
    try:
        s = sd.InputStream(samplerate=SAMPLERATE, channels=1)
        s.start()
        s.stop()
        s.close()
        return True
    except Exception as e:
        print(f"  [mic prewarm failed] {e}", flush=True)
        return False


def start_rec() -> bool:
    global recording, frames, stream
    with lock:
        if recording:
            return False
        frames = []
        try:
            stream = sd.InputStream(
                samplerate=SAMPLERATE,
                channels=1,
                device=INPUT_DEVICE,
                callback=lambda i, n, t, s: frames.append(i.copy()),
            )
            stream.start()
        except Exception as e:
            stream = None
            frames = []
            print(f"  [mic error] {e}", flush=True)
            print("  Check macOS Microphone permission and input device; "
                  "set CLIXEN_PTT_INPUT_DEVICE if needed.", flush=True)
            return False
        recording = True
    print("● recording… (release RIGHT-Cmd to save)", flush=True)
    return True


def stop_rec():
    global recording, stream
    with lock:
        if not recording:
            return
        recording = False
        if stream:
            stream.stop()
            stream.close()
            stream = None
    captured = list(frames)
    if not captured:
        print("  no audio captured", flush=True)
        return
    audio = np.concatenate(captured, axis=0)
    if len(audio) < int(SAMPLERATE * 0.1):
        print("  no usable audio captured", flush=True)
        return
    peak = float(np.abs(audio).max())
    if peak < 0.01:
        print("  [silence] mic captured no signal (peak < 0.01) — "
              "check Microphone permission and input device", flush=True)
        return

    if autotype_this:
        print("■ transcribing…", flush=True)
        text = transcribe_array(audio)
        print(f"  text: {text}", flush=True)
        if text.strip():
            type_text(text)
        return

    print("■ saving + transcribing…", flush=True)
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    wav = SAVE_DIR / f"{ts}.wav"
    txt = SAVE_DIR / f"{ts}.txt"
    sf.write(str(wav), audio, SAMPLERATE)
    text = transcribe_array(audio)
    txt.write_text(text, encoding="utf-8")
    print(f"  saved: {wav.name} + {txt.name}", flush=True)
    print(f"  text: {text}", flush=True)
    if CLIXEN_URL and text.strip():
        send_to_clixen(text)


def type_text(text: str) -> None:
    global _typing
    import ApplicationServices as _AX
    if not _AX.AXIsProcessTrusted():
        print("  [autotype error] accessibility permission missing — add this python to "
              "System Settings → Privacy & Security → Accessibility, then restart", flush=True)
        return
    time.sleep(0.05)
    _typing = True
    try:
        typer.type(text)
    except Exception as e:
        print(f"  [autotype error] {e}", flush=True)
    finally:
        _typing = False


def transcribe_array(audio: np.ndarray) -> str:
    # In memory: no temp file, and no ffmpeg (model.transcribe(path) shells out to
    # it, and it's not on launchd's PATH).
    # ponytail: whole recording in one pass, like model.transcribe's default; if a
    # very long RIGHT-Option toggle recording runs out of memory, chunk it the way
    # model.transcribe(chunk_duration=120) does.
    def run() -> str:
        model = _get_model()
        mel = get_logmel(mx.array(audio.reshape(-1).astype(np.float32)), model.preprocessor_config)
        return model.generate(mel)[0].text.strip()

    return _mlx_thread.submit(run).result()


def send_to_clixen(text: str) -> None:
    try:
        req = urllib.request.Request(
            CLIXEN_URL,
            data=json.dumps({"message": text, "chat_id": CHAT_ID}).encode(),
            headers={"Content-Type": "application/json"})
        urllib.request.urlopen(req, timeout=120)
    except Exception as e:
        print(f"  [clixen error] {e}", flush=True)


rec_key = keyboard.Key.cmd_r        # hold to record, release to save (file only)
toggle_key = keyboard.Key.alt_r     # tap to start continuous rec, tap again to stop (file only)
field_key = keyboard.Key.shift_r    # hold to record, release to save + type into focused field

held_active = False
toggle_active = False
field_active = False


def _any_active():
    return held_active or toggle_active or field_active


def on_press(k):
    global held_active, toggle_active, field_active, autotype_this
    if _typing:
        return
    if k == rec_key:
        if _any_active():
            return
        autotype_this = False
        if start_rec():
            held_active = True
    elif k == toggle_key:
        if toggle_active:
            toggle_active = False
            stop_rec()
            return
        if held_active or field_active:
            return
        autotype_this = False
        if start_rec():
            toggle_active = True
    elif k == field_key:
        if _any_active():
            return
        autotype_this = True
        if start_rec():
            field_active = True


def on_release(k):
    global held_active, field_active
    if _typing:
        return
    if k == rec_key and held_active:
        held_active = False
        stop_rec()
    elif k == field_key and field_active:
        field_active = False
        stop_rec()


if __name__ == "__main__":
    print(f"clixen voice recorder ready.\n"
          f"  Hold RIGHT-Cmd -> record, save file only\n"
          f"  Tap RIGHT-Option -> toggle continuous rec, save file only\n"
          f"  Hold RIGHT-Shift -> record, save file + type into focused field\n"
          f"Files -> {SAVE_DIR}", flush=True)
    if not prewarm_mic():
        print("\n  Mic unavailable — System Settings → Privacy & Security → Microphone,\n"
              "  allow this python binary, then restart:\n"
              "  launchctl kickstart -k gui/$(id -u)/com.clixen.pushtotalk\n",
              flush=True)
        sys.exit(2)
    warm_model()
    with keyboard.Listener(on_press=on_press, on_release=on_release) as l:
        l.join()
