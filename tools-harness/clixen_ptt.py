from __future__ import annotations
import os
import time
import tempfile
import threading
import json
import urllib.request
from pathlib import Path
from datetime import datetime

import numpy as np
from pynput import keyboard
import sounddevice as sd
import soundfile as sf
from pywhispercpp.model import Model

SAVE_DIR = Path.home() / "Documents" / "clixen-voice"
SAVE_DIR.mkdir(parents=True, exist_ok=True)

SAMPLERATE = 16000
_model: Model | None = None
recording = False
frames: list = []
stream = None
lock = threading.Lock()
typer = keyboard.Controller()

# ponytail: set to clixen URL to also send; None = local-only save
CLIXEN_URL = None
CHAT_ID = "brabble_voice"

autotype_this = False  # per-session flag, set by whichever key started the recording


def get_model() -> Model:
    global _model
    if _model is None:
        _model = Model("base", models_dir=str(Path.home() / ".cache" / "whisper.cpp"), n_threads=os.cpu_count())
    return _model


def _whisper(wav_path: str) -> str:
    segs = get_model().transcribe(wav_path)
    return "".join(s.text for s in segs).strip()


def prewarm_mic():
    # ponytail: opens+closes once at startup so the first real recording
    # doesn't eat the device-open latency FluidVoice avoids via pre-warming
    try:
        s = sd.InputStream(samplerate=SAMPLERATE, channels=1)
        s.start()
        s.stop()
        s.close()
    except Exception as e:
        print(f"  [mic prewarm failed] {e}", flush=True)


def start_rec():
    global recording, frames, stream
    with lock:
        if recording:
            return
        recording = True
        frames = []
        stream = sd.InputStream(samplerate=SAMPLERATE, channels=1,
                                callback=lambda i, n, t, s: frames.append(i.copy()))
        stream.start()
    print("● recording… (release RIGHT-Cmd to save)", flush=True)


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
    if not frames:
        print("  no audio captured", flush=True)
        return
    audio = np.concatenate(frames, axis=0)

    if autotype_this:
        print("■ transcribing…", flush=True)
        text = transcribe_array(audio)
        print(f"  text: {text}", flush=True)
        if text.strip() and "[BLANK_AUDIO]" not in text and "[BLANK AUDIO]" not in text:
            type_text(text)
        return

    print("■ saving + transcribing…", flush=True)
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    wav = SAVE_DIR / f"{ts}.wav"
    txt = SAVE_DIR / f"{ts}.txt"
    sf.write(str(wav), audio, SAMPLERATE)
    text = transcribe(wav)
    txt.write_text(text, encoding="utf-8")
    print(f"  saved: {wav.name} + {txt.name}", flush=True)
    print(f"  text: {text}", flush=True)
    if CLIXEN_URL and text.strip():
        send_to_clixen(text)


def type_text(text: str) -> None:
    # ponytail: release the trigger key first so its modifier flag doesn't
    # get glued onto the first typed character (same footgun FluidVoice's
    # PasteManager strips before injecting keystrokes)
    time.sleep(0.05)
    try:
        typer.type(text)
    except Exception as e:
        print(f"  [autotype error] {e}", flush=True)


def transcribe(wav_path: Path) -> str:
    return _whisper(str(wav_path))


def transcribe_array(audio: np.ndarray) -> str:
    with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as f:
        sf.write(f.name, audio, SAMPLERATE)
        try:
            return _whisper(f.name)
        finally:
            os.unlink(f.name)


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
    if k == rec_key:
        if _any_active():
            return
        held_active = True
        autotype_this = False
        start_rec()
    elif k == toggle_key:
        if toggle_active:
            toggle_active = False
            stop_rec()
            return
        if held_active or field_active:
            return
        toggle_active = True
        autotype_this = False
        start_rec()
    elif k == field_key:
        if _any_active():
            return
        field_active = True
        autotype_this = True
        start_rec()


def on_release(k):
    global held_active, field_active
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
    threading.Thread(target=prewarm_mic, daemon=True).start()
    threading.Thread(target=get_model, daemon=True).start()
    with keyboard.Listener(on_press=on_press, on_release=on_release) as l:
        l.join()