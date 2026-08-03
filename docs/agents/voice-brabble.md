# Brabble — Wake Word Voice Agent

Voice interface via Brabble fork at `/tmp/brabble-src`. Binary at `~/.local/bin/brabble`.

## Working Config (`~/.config/brabble/config.toml`)

```toml
[vad]
enabled = true
silence_ms = 1500          # end-of-speech silence window
aggressiveness = 1          # 0-3; 1 = sensitive (captures quiet speech on MacBook Air mic)
energy_threshold = -40.0    # dBFS; lower = more sensitive
min_speech_ms = 300
max_segment_ms = 60000     # 2026-07: raised 5000→10000→60000; auto-send on 1.5s silence unchanged
partial_flush_ms = 0        # disabled — only final segments hit the hook

[wake]
enabled = true
word = "clawd"
aliases = ["claude", "clixen", "picovoice", "pico voice", ...]
engine = "whisper"          # or 'porcupine' with custom .ppn
sensitivity = 0.7

[[hooks]]
wake = ["clawd"]
aliases = ["claude", "picovoice", ...]
command = '~/Developer/gemma4llama/tools-harness/brabble_hook.py'
cooldown_sec = 1.0
min_chars = 1
timeout_sec = 60
```

## Architecture

1. **PortAudio capture** → WebRTC VAD → segment accumulation → whisper transcription → wake word matching → hook dispatch
2. **Porcupine** (optional): runs on raw audio frames before VAD for lower-latency wake detection. Falls back to whisper text matching if `access_key` is empty.
3. **Audio gate** (`audio_gate.go`): music/speech classifier with ring-buffer ZCR + energy analysis. Drops sustained music segments before they hit whisper. Hardcoded RMS threshold 0.005 (-46dBFS).
4. **Wake word window** (`server.go`): 5-second forwarding window after wake match — all subsequent whisper segments forwarded without requiring wake word in text.

## Voiceprint

- Hook: `brabble_hook.py` — verifies speaker identity via Resemblyzer before dispatching to harness.
- Threshold: 0.55 (lowered from 0.65 for live mic variance). Enrollment at `~/.config/g4l/voiceprint/voiceprint.npy`.
- Rejection below threshold sends "voiceprint rejected" log only (no dispatch).

## Key Decisions

- **VAD aggressiveness 1** over 2: MacBook Air built-in mic needs more sensitivity. 2+ missed many utterances.
- **partial_flush_ms = 0**: eliminated segment queue overflow during continuous audio. Only VAD-terminated segments processed.
- **energy_threshold -40dBFS** over -35dBFS: captures quieter speech without increasing noise hallucination.
- **Absolute Python shebang** (`#!/Users/kalinovdameus/miniforge3/bin/python3`) in hook: launchd has restricted PATH.

## Useful Commands

```
launchctl kickstart gui/$(id -u)/com.brabble.agent   # restart daemon
~/.local/bin/brabble tail-log                          # tail logs
~/.local/bin/brabble test-hook "text"                  # invoke hook directly (bypasses mic)
```

## Latency & Streaming (2026-07)

`brabble_hook.py` is exec'd as a **cold subprocess** on every wake-word match — cold-importing `harness.py`'s full dependency tree (LangGraph, 89+ tool registry, clients) cost ~4.3-4.8s before any work started. Fixed by having the hook POST to the already-warm `chat_ui.py` server instead of importing harness directly:
- `_run_harness_streaming(text)`: streams from `/chat/stream`, uses `sentence_boundary()` (from `src/g4l/core/utils.py` — this is one of the two genuinely-live files under `src/g4l/`, see root CLAUDE.md) to detect complete sentences and speaks each one as it arrives, rather than waiting for the full reply.
- `_run_harness_blocking(text)`: POSTs to `/chat` (non-streaming) as a fallback if streaming raises for any reason; falls back further to a direct `import harness; harness.run(...)` only if the HTTP call itself fails (server down).
- `cloud_client.py`'s tool loop (used whenever the routed model is `openrouter/...`, which is the 2026-07 default per CLAUDE.md's cloud-first revamp) previously called the OpenAI-SDK-compatible API **without** `stream=True` — it fired one `on_token(full_text)` at the end, defeating per-sentence speaking entirely. Fixed via `_stream_completion()`/`_create_stream_with_retry()`: real `stream=True` + `stream_options={"include_usage": True}`, with tool-call deltas reassembled across chunks by `.index` (arrive fragmented — `id`/`function.name`/`function.arguments` each need accumulating separately). This also benefits Web UI and Telegram, not just voice.

**Tool-progress labels and emoji must never reach `say`.** Both `cloud_client.py` and `ollama_client.py` inject `on_token(f"\n\n{label}")` (e.g. "🔍 Searching the web…") for the Web UI's live tool-progress bubble. Two separate stripping layers exist in `brabble_hook.py`:
- `_TOOL_PROGRESS_RE`: matches the exact known label text (a small hardcoded tuple, **not** imported from `cloud_client` — importing it alone cost ~2.4s via the openai SDK/dotenv/tool-registry chain, defeating the cold-start fix; the duplication is deliberate, marked with a comment). Must tolerate labels both `\n\n`-prefixed (native tool-calling path) **and** glued directly onto the answer with no separator at all (a separate "temporal"/websearch-fallback code path in `harness.py`/`chat_ui.py` returns a final, non-streamed reply with the label concatenated straight onto the content — confirmed live via a weather query where the raw SSE token was one single blob, no `\n\n`).
- `_EMOJI_RE`: strips emoji (broad Unicode ranges incl. variation selectors/ZWJ) **only inside `_speak()`**, not from the text used for display/logging — otherwise macOS `say` reads emoji aloud by their full Unicode name ("smiling face with smiling eyes and rosy cheeks"). Do not widen this to match bare emoji character classes generally — an earlier attempt did that and ate legitimate content emoji in replies (e.g. `☁️ Condition:`, `🌡️ Temperature:` in a weather reply got truncated).

**Same bug existed on the Web UI TTS path** (`chat_ui.py`'s `/tts/speak` + `/tts/speak/stream`) — the Speak button read the raw accumulated bubble text (including progress labels) with no equivalent filter. Fixed 2026-07: `_strip_md()` in `chat_ui.py` now also strips the known `_TOOL_PROGRESS_LABELS` (imported live from `cloud_client.py`, not a hardcoded copy — chat_ui.py already pays that import cost) before the existing markdown/emoji strip.

## Stale Code Gotchas

After rebuilding `go build -o ~/.local/bin/brabble .`, run `install_name_tool -add_rpath "$HOME/.local/opt/whisper/lib" ~/.local/bin/brabble` to fix dylib paths.
