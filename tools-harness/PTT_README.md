# clixen_ptt.py — Push-to-Talk Voice Recorder

Local voice dictation for macOS. Global hotkeys, local Whisper transcription (whisper.cpp via `pywhispercpp`), no cloud calls.

## Hotkeys

| Key | Mode | Behavior |
|-----|------|----------|
| **Hold RIGHT-Cmd** | Push-to-talk | Records while held. Release → transcribes → saves `.wav` + `.txt` to disk. |
| **Tap RIGHT-Option** | Toggle | Tap once → starts continuous recording. Tap again → stops, transcribes, saves `.wav` + `.txt` to disk. |
| **Hold RIGHT-Shift** | Field dictation | Records while held. Release → transcribes in memory (nothing written to disk) → types the result into whichever app/field is currently focused. |

Only one recording session can be active at a time — pressing a different key while one is running is ignored until the active one finishes.

## Why RIGHT-side keys

RIGHT-Cmd double-tap collides with macOS Siri (system-reserved), and MacBook keyboards have no physical RIGHT-Control key — hence RIGHT-Option and RIGHT-Shift instead.

## Output

- RIGHT-Cmd / RIGHT-Option → `~/Documents/clixen-voice/<timestamp>.wav` + `.txt`
- RIGHT-Shift → nothing saved; text goes straight to the focused field via synthetic keystrokes (`pynput.keyboard.Controller().type()`)

## Model

`pywhispercpp` wrapping whisper.cpp, `tiny.en` model, Metal GPU on Apple Silicon. Model held warm in memory → ~110ms per transcription (vs ~1900ms with old faster-whisper small). Model auto-downloads to `~/.cache/whisper.cpp/` on first use.

## Optional: forward to clixen chat

Set `CLIXEN_URL` near the top of the script (currently `None` = local-only) to also POST the transcript to a running `chat_ui.py` instance as a chat message. Only applies to the disk-saving keys (RIGHT-Cmd / RIGHT-Option), not RIGHT-Shift field dictation.

## Running

```bash
python3 clixen_ptt.py
```

Runs in the foreground; logs each recording start/stop and the transcribed text to stdout. Run it under `nohup ... &` or wrap it in a launchd job to keep it alive across terminal sessions.

**Gotcha**: if you relaunch the script while an old instance is still running, you'll get two competing keyboard listeners — check with `ps -ef | grep clixen_ptt.py` and kill stale PIDs before starting a new one.

## Dependencies

`pynput`, `sounddevice`, `soundfile`, `numpy`, `pywhispercpp`. System dep: `whisper-cpp` via Homebrew.
