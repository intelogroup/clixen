# Clixen Orb

A floating voice orb for macOS. No window, no dock icon, no chrome — just a
glowing sphere that sits on top of whatever you're doing and answers when you
press and hold it.

It is a thin shell over the existing Clixen backend (`tools-harness/chat_ui.py`):
the orb renders, the Rust side talks to the API, and the backend does the
thinking, transcribing and speaking.

```
clixen-orb/
├── index.html          # the orb window
├── caption.html        # the whisper strip (and the typed-input surface)
├── src/
│   ├── main.js         # canvas renderer, mic capture, backend bridge
│   ├── styles.css
│   ├── caption.js      # caption strip + text input
│   └── caption.css
└── src-tauri/
    ├── src/lib.rs      # NSPanel setup, SSE pump, Keychain token, commands
    ├── tauri.conf.json # two windows, private API, LSUIElement
    ├── Info.plist      # mic + automation usage strings
    └── capabilities/default.json
```

## Running it

```bash
npm install
npm run dev          # Tauri dev server (Vite on :1420, app on :1420)
npm run build        # → src-tauri/target/release/bundle/macos/Clixen Orb.app
npm run release      # build app + dmg, then verify signature, entitlements, checksum
```

`npm run release` also ejects stale `rw.*.dmg` scratch images first: a killed
Tauri build leaves one mounted and the next DMG step then dies with
`couldn't unmount … Resource busy`.

The backend must be up (`tools-harness/core.py`, `http://127.0.0.1:9234`).
Override the address with `CLIXEN_BACKEND_URL`.

Auth uses the per-install localhost token from the macOS Keychain
(service `clixen.localhost.token`), read natively in Rust and sent as the
`x-clixen-token` header. The webview never sees it.

> **Use the bundled app for microphone work.** `tauri dev` runs an unbundled
> binary with no `Info.plist`, so macOS has no `NSMicrophoneUsageDescription`
> to show and will kill the app the moment it reaches for the mic. The bundled
> `.app` carries the plist and works.

## Tests

```bash
npm run test:web        # pure renderer logic (node --test)
npm run test:ui         # orb UI harness (Playwright): mic, resampler, TTS, half-duplex, gestures
../.venv/bin/python test/cursor_overlay.py   # cursor sprite overlay
(cd src-tauri && cargo test)                 # Rust units + ACL/capability config
```

`test/tauri_mock.py` runs the **real** renderer in headless Chromium with only
the Tauri IPC boundary mocked: the bunny's mic chip → `orb_listen` →
`orb_feed_audio`, the 48k→16k resampler (a 1 kHz tone must stay 1 kHz in
960-byte frames), and a typed prompt → `orb_send_text` → backend audio frame →
WebAudio playback, including that back-to-back TTS clips are played strictly in
sequence rather than overlapping. It also covers half-duplex: with the mic chip
on, a reply still plays while the mic keeps its frame clock running but forwards
silence, so the orb cannot transcribe its own voice. `npm run test:ui -- --headed`
opens a visible window and plays the injected clip through your speakers.

The mock deliberately bypasses the ACL, so `cargo test` remains the guard for
capability/command registration; real WKWebView autoplay and TCC mic behaviour
still need the bundled app.

## Interaction

| Gesture | Effect |
|---|---|
| Press and hold ≥200 ms | Record; release to send |
| Drag | Move the orb (its position is remembered) |
| Double-click | Type a question into the strip |
| Esc / right-click | Stop listening |
| ⌃⇧Space | Show / hide the orb |
| ⌃⇧P | Click-through (the orb stops eating clicks) |
| ⌃⇧O | Toggle above full-screen apps |

## How it stays out of the way

The orb is an `NSPanel` at `NSStatusWindowLevel` with
`NSNonactivatingPanelMask`, `canJoinAllSpaces` and `fullScreenAuxiliary`, so it
floats above browsers, editors and full-screen apps without ever taking
focus away from the page underneath. It is 150×150 on purpose — small enough
that its transparent corners can't intercept a click meant for the page below.

All text lives in a second, separate 420×78 window (the "whisper strip")
positioned directly beneath the orb. That split is deliberate: one window
can't be both a tight click-through sphere and a readable caption.

## Things that will bite you

These are all real bugs that were hit while building this. They are easy to
reintroduce, so they are written down.

**The style mask must be set *before* the class swap.**
`NSNonactivatingPanelMask` has to be on the window's style mask while it is
still a plain `NSWindow`. `setStyleMask:` makes AppKit rebuild the window, and
during teardown WebKit's `WKWindowVisibilityObserver` removes its KVO
registration for `contentLayoutRect`. If the class was already swapped to our
`NSPanel` subclass, that removal fails with *"…because it is not registered as
an observer"*, the ObjC exception aborts `setStyleMask:` halfway, and the
window is left with a **nil content view**. The `WKWebView` is orphaned: blank
canvas, `document.visibilityState === "hidden"`, `requestAnimationFrame` never
fires — and no error is surfaced anywhere. See `setup_orb_window` in
`src-tauri/src/lib.rs`.

**The SSE field is `kind`, not `type`.** The renderer switches on
`payload.kind`. A `#[serde(rename = "type")]` on the Rust side makes every
frame fall through the switch, so the orb goes silent while still looking
perfectly healthy.

**Release the mouse anywhere, not just on the canvas.** `pointerup` is bound to
the window. Bound to the 150×150 canvas instead, lifting the button a few
pixels off the sphere means the canvas never sees the release — the recorder
stays hot, the room gets transcribed, and since the reply is spoken aloud the
microphone hears it and the two trade sentences forever. There is also a hard
45 s ceiling on a single recording as a backstop.

**Positions are logical, moves are physical.** `tao` reports moves in device
pixels; `set_position(Logical)` expects points. Mixing them doubles the orb's
offset on every launch until it strands itself off-screen.

**Never send an empty voice to the TTS.** The backend defaults to `af_heart`,
but `"voice": ""` overrides that default and makes the synthesizer fail.

## Debugging

```bash
CLIXEN_ORB_DEBUG=1 <app binary>     # logs every SSE frame the backend sends
```

The backend's own view of the conversation lands in
`tools-harness/chat_ui.log` — grep for `chat_id=orb_ui` to see what the orb
actually asked and what it transcribed.

## Status

Working end to end: hold to talk → transcribe → answer → speak, with the
caption strip tracking the exchange. Typed input goes through the caption
window. Not yet done: the orb is not code-signed or packaged for
distribution, and there is no installer or launchd plist.


## Shipping build: lifecycle

`npx tauri build --features lifecycle --bundles app` turns on first-run setup and the encrypted vault
(off in the dev orb; `CLIXEN_LIFECYCLE=1|0` overrides at runtime).

Flow: onboarding consent -> Ollama check -> model pull (progress streams) -> AES-256 sparse-image vault
(passphrase in the Keychain, service `clixen.vault.app`) -> mount -> workspace init -> orb shown.
The vault locks on idle (default 15 min, Settings, ctrl+shift+s), on wake from sleep, on quit and on
SIGTERM. A busy vault stays mounted ("vault still busy"), it is never force-detached. Uninstall
(Settings) needs the typed word DELETE and removes the vault, its Keychain entry and the state file.

Test switches (never touch the real vault): `CLIXEN_DATA_DIR`, `CLIXEN_VAULT_SERVICE`,
`CLIXEN_OLLAMA_PORT`, `CLIXEN_MODELS`, `CLIXEN_SIDECAR_CMD` (JSON argv), `CLIXEN_SIDECAR_PORT`.

Known limits: App Sandbox is NOT enabled (the orb shells out to hdiutil, security, osascript and
drives other apps, which the sandbox forbids); Hardened Runtime is on with two entitlements
(microphone, Apple Events). The sleep lock fires on wake (the process is frozen during sleep), so a
Mac that sleeps with the vault mounted is locked the moment it wakes, before any UI is usable.
Activity from other channels (Telegram etc.) does not reset the idle timer; only orb use does.
Signing/notarization needs a Developer ID certificate (only an Apple Development one is installed).
