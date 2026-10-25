"""UI harness for the orb: bunny mic, 48k→16k resampler, and TTS playback.

    ../.venv/bin/python test/orb_ui.py            # headless
    ../.venv/bin/python test/orb_ui.py --headed   # watch it, and hear the clip

Uses test/tauri_mock.py: the real renderer modules run in Chromium, only the
native IPC boundary and the mic device are mocked. `--headed` plays the
injected TTS through your speakers, which is the quickest manual check.
"""
import base64
import struct
import sys

from playwright.sync_api import sync_playwright

from tauri_mock import (
    MIC_CHIP,
    ORB_BODY,
    calls,
    estimate_hz,
    fire_event,
    open_orb,
    serve_vite,
    sine_wav_b64,
    timed_calls,
)

FAILS: list[str] = []


def check(name: str, ok: bool, detail="") -> bool:
    print(f"  [{'PASS' if ok else 'FAIL'}] {name}{(' — ' + str(detail)) if detail else ''}")
    if not ok:
        FAILS.append(name)
    return ok


def feed_tone(page, *, blocks: int = 4, freq: float = 1000, size: int = 2048, rate: int = 48000) -> str:
    """Push real samples through the renderer's mic processor (48 kHz tone)."""
    return page.evaluate("""({ blocks, freq, rate, size }) => {
        const proc = window.__proc;
        if (!proc || !proc.onaudioprocess) return "no processor";
        for (let b = 0; b < blocks; b++) {
            const a = new Float32Array(size);
            for (let i = 0; i < size; i++)
                a[i] = 0.5 * Math.sin(2 * Math.PI * freq * (b * size + i) / rate);
            proc.onaudioprocess({ inputBuffer: { getChannelData: () => a } });
        }
        return "fed";
    }""", {"blocks": blocks, "freq": freq, "rate": rate, "size": size})


def fed_frames(page) -> list[bytes]:
    return [base64.b64decode(a["audioB64"]) for a in calls(page, "orb_feed_audio")]


def _peak(pcm: bytes) -> float:
    if not pcm:
        return 0.0
    return max(abs(v) for v in struct.unpack("<%dh" % (len(pcm) // 2), pcm)) / 32768


def test_mic_and_resampler(browser, base: str) -> None:
    """Click the mic chip and prove the 48k→16k path emits clean 30 ms frames."""
    errs: list[str] = []
    page = open_orb(browser, base, fake_mic=True, errors=errs)
    page.mouse.move(*MIC_CHIP)
    page.wait_for_timeout(100)
    page.mouse.click(*MIC_CHIP)          # mic on
    page.wait_for_timeout(500)

    on = [a for a in calls(page, "orb_listen") if a.get("on") is True]
    check("mic chip invokes orb_listen(on:true)", bool(on))
    check("caption shows Listening…", any(
        a.get("text") == "Listening…" for a in calls(page, "orb_caption")))

    fed = feed_tone(page, blocks=8)
    page.wait_for_timeout(600)

    frames = fed_frames(page)
    sizes = sorted({len(f) for f in frames})
    pcm = b"".join(frames)
    hz = estimate_hz(pcm)
    peak = _peak(pcm)

    check("processor installed", fed == "fed")
    check("30 ms / 960-byte frames", sizes == [960], sizes)
    check("enough frames for 8 blocks", len(frames) >= 6, len(frames))
    check("1 kHz tone survives resampling", 900 <= hz <= 1100, f"{hz:.1f} Hz")
    check("signal not silent", peak > 0.2, f"peak {peak:.2f}")
    check("no page errors (mic)", not errs, errs)

    page.mouse.click(*MIC_CHIP)          # mic off
    page.wait_for_timeout(300)
    off = [a for a in calls(page, "orb_listen") if a.get("on") is False]
    check("second click invokes orb_listen(on:false)", bool(off))
    page.close()


def test_text_in_audio_out(browser, base: str) -> None:
    """Typed text reaches Rust; a backend audio frame then plays through WebAudio.

    This is the manual loop described as “send text or audio to the app and hear
    the reply”: the `orb-prompt` event is what Rust emits after the prompt
    window, and `orb-event` is what the SSE pump emits for TTS frames.
    """
    errs: list[str] = []
    page = open_orb(browser, base, fake_mic=False, errors=errs)
    page.mouse.click(*ORB_BODY)          # user gesture: unlock the AudioContext
    page.wait_for_timeout(250)

    fire_event(page, "orb-prompt", {"text": "say hello"})
    page.wait_for_timeout(250)
    sent = calls(page, "orb_send_text")
    check("typed prompt invokes orb_send_text", bool(sent) and sent[0].get("message") == "say hello", sent)

    wav = sine_wav_b64(seconds=0.6, freq=440)
    fire_event(page, "orb-event", {"kind": "token", "text": "Hello!"})
    fire_event(page, "orb-event", {"kind": "audio", "data": wav})
    page.wait_for_timeout(1200)

    dbg = [a.get("msg", "") for a in calls(page, "orb_debug")]
    played = [m for m in dbg if m.startswith("audio ") and ("ctx=running" in m or "via <audio>" in m)]
    failed = [m for m in dbg if m.startswith("audio failed") or m.startswith("audio blocked")]
    check("audio frame decodes and starts", bool(played), played)
    check("no audio failure logged", not failed, failed)
    check("no page errors (playback)", not errs, errs)
    page.close()


def test_action_events_drive_the_bunny(browser, base: str) -> None:
    """Tool events must animate the orb without a single rendered error.

    This case exists because the first version of the handler referenced an
    undefined identifier (``cfg.debug``) inside the switch: the event threw a
    ReferenceError, the pose never cleared, and nothing in the build or the type
    checker could see it — the file is plain JS. Firing the real event through the
    real renderer is the only check that catches that class.
    """
    errs: list[str] = []
    page = open_orb(browser, base, fake_mic=False, errors=errs)
    page.mouse.click(*ORB_BODY)
    page.wait_for_timeout(250)

    fire_event(page, "orb-event", {"kind": "action", "phase": "started", "tool": "observe"})
    fire_event(page, "orb-event", {"kind": "action", "phase": "done", "tool": "observe", "ok": True, "ms": 42})
    fire_event(page, "orb-event", {"kind": "action", "phase": "started", "tool": "act"})
    fire_event(page, "orb-event", {"kind": "action", "phase": "done", "tool": "act", "ok": False, "ms": 7, "error": "stale element"})
    page.wait_for_timeout(400)

    dbg = [a.get("msg", "") for a in calls(page, "orb_debug")]
    check("action start is traced", "action start observe" in dbg, dbg[-6:])
    check("action completion carries its duration", "action done observe 42ms" in dbg, dbg[-6:])
    check("a failed action is reported as failed",
          any("action failed act" in m for m in dbg), dbg[-6:])
    check("no page errors (action events)", not errs, errs)
    page.close()


def test_speech_is_serialized(browser, base: str) -> None:
    """Two audio frames fired back-to-back must not play on top of each other."""
    errs: list[str] = []
    page = open_orb(browser, base, fake_mic=False, errors=errs)
    page.mouse.click(*ORB_BODY)
    page.wait_for_timeout(250)
    wav = sine_wav_b64(seconds=0.5, freq=440)
    fire_event(page, "orb-event", {"kind": "audio", "data": wav})
    fire_event(page, "orb-event", {"kind": "audio", "data": wav})
    page.wait_for_timeout(1800)

    starts = [
        c for c in timed_calls(page, "orb_debug")
        if c["args"].get("msg", "").startswith("audio ") and "ms " in c["args"]["msg"]
    ]
    delta = (starts[1]["t"] - starts[0]["t"]) if len(starts) >= 2 else 0
    check("both clips are played", len(starts) >= 2, [c["args"]["msg"] for c in starts])
    check("second clip starts after the first", delta >= 400, f"{delta:.0f} ms apart")
    check("no page errors (queue)", not errs, errs)
    page.close()


def test_half_duplex_gate(browser, base: str) -> None:
    """With the mic chip on, a reply plays *and* the mic keeps its clock.

    Half-duplex: while a clip is audible the mic stream stays open but forwards
    zeros. The backend still receives frames (so it can endpoint), the orb cannot
    transcribe its own voice, and the moment the clip ends real audio resumes.
    """
    errs: list[str] = []
    page = open_orb(browser, base, fake_mic=True, errors=errs)
    page.mouse.move(*MIC_CHIP)
    page.wait_for_timeout(100)
    page.mouse.click(*MIC_CHIP)          # mic on — continuous listening
    page.wait_for_timeout(400)

    feed_tone(page, blocks=4)            # baseline: nothing is playing
    page.wait_for_timeout(350)
    base_frames = fed_frames(page)
    check("mic on: tone reaches the backend", _peak(b"".join(base_frames)) > 0.2,
          f"peak {_peak(b''.join(base_frames)):.2f}")

    n0 = len(fed_frames(page))
    fire_event(page, "orb-event", {"kind": "audio", "data": sine_wav_b64(seconds=0.4, freq=440)})
    page.wait_for_timeout(120)           # the clip is playing now
    feed_tone(page, blocks=4)            # same tone, but the gate is shut
    page.wait_for_timeout(220)
    during = b"".join(fed_frames(page)[n0:])

    n1 = len(fed_frames(page))
    page.wait_for_timeout(600)           # clip ends -> gate reopens
    feed_tone(page, blocks=4)
    page.wait_for_timeout(300)
    after = b"".join(fed_frames(page)[n1:])

    played = [c.get("msg", "") for c in calls(page, "orb_debug")]
    check("reply is played with the mic on", any(m.startswith("audio ") and "ctx=running" in m for m in played),
          played[-4:])
    check("mic forwarded frames while the reply played", len(during) >= 2880, f"{len(during)} bytes")
    check("those frames are silence (no self-hearing)", _peak(during) == 0, f"peak {_peak(during):.2f}")
    check("real audio resumes when the clip ends", _peak(after) > 0.2, f"peak {_peak(after):.2f}")
    check("gate transitions are logged", any("mic gated" in m for m in played) and any("mic open again" in m for m in played))
    check("no page errors (half-duplex)", not errs, errs)

    page.mouse.click(*MIC_CHIP)          # mic off
    page.wait_for_timeout(300)
    page.close()


def test_tap_vs_drag(browser, base: str) -> None:
    """A tap with trackpad jitter must stay a tap; a deliberate drag must move.

    Six pixels of slop used to turn ordinary taps into drags: the orb slid away
    instead of clicking, and a press-and-hold lost its hold half the time.
    """
    errs: list[str] = []
    page = open_orb(browser, base, fake_mic=False, errors=errs)

    # jittery tap: press, wander 5 px, release
    page.mouse.move(*ORB_BODY)
    page.mouse.down()
    page.mouse.move(ORB_BODY[0] + 3, ORB_BODY[1] + 2, steps=2)
    page.mouse.move(ORB_BODY[0] + 5, ORB_BODY[1] + 1, steps=2)
    page.mouse.up()
    page.wait_for_timeout(400)

    jitter = [c.get("msg", "") for c in calls(page, "orb_debug")]
    check("jittery tap does not start a drag", not any("drag engage" in m for m in jitter), jitter[-3:])
    check("jittery tap releases as a tap", any("release wasTap=true" in m for m in jitter), jitter[-3:])

    # deliberate drag: press, move 45 px, release
    page.mouse.move(*ORB_BODY)
    page.mouse.down()
    page.mouse.move(ORB_BODY[0] + 45, ORB_BODY[1], steps=8)
    page.mouse.up()
    page.wait_for_timeout(500)

    after = [c.get("msg", "") for c in calls(page, "orb_debug")]
    moved = [c for c in calls(page, "plugin:window|set_position")]
    check("real drag engages", any("drag engage" in m for m in after))
    check("real drag moves the window", len(moved) >= 1, f"{len(moved)} setPosition calls")
    check("no page errors (gestures)", not errs, errs)
    page.close()


def main() -> int:
    headed = "--headed" in sys.argv
    print("orb UI harness")
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=not headed)
        try:
            with serve_vite(5196) as base:
                print("mic chip + 48k→16k resampler")
                test_mic_and_resampler(browser, base)
            with serve_vite(5197) as base:
                print("typed text in → TTS audio out")
                test_text_in_audio_out(browser, base)
            with serve_vite(5198) as base:
                print("back-to-back TTS clips stay sequential")
                test_speech_is_serialized(browser, base)
            with serve_vite(5199) as base:
                print("half-duplex: reply plays, mic sends silence")
                test_half_duplex_gate(browser, base)
            with serve_vite(5200) as base:
                print("tap vs drag (trackpad jitter)")
                test_tap_vs_drag(browser, base)
            with serve_vite(5201) as base:
                print("action events drive the bunny")
                test_action_events_drive_the_bunny(browser, base)
        finally:
            browser.close()
    if FAILS:
        print(f"\n{len(FAILS)} FAILED: {', '.join(FAILS)}")
        return 1
    print("\nall checks passed")
    return 0


if __name__ == "__main__":
    sys.exit(main())

