#!/usr/bin/env python3
"""Measure voice-turn latency against the live core, hop by hop.

Answers "is voice faster now?" with numbers instead of impressions. Each query is
synthesized to 16 kHz WAV, posted to /voice/process exactly as the orb's mic path
does, and the SSE stream is timestamped:

    t0            request sent
    transcript    speech -> text done (STT)
    token         first token reached the client (LLM TTFT, after the leak filter)
    audio         first playable WAV frame (TTS)
    done          turn over

Usage:
    ../.venv/bin/python scripts/voice_latency.py            # default query set
    ../.venv/bin/python scripts/voice_latency.py --tts      # include TTS timing
    ../.venv/bin/python scripts/voice_latency.py --csv /tmp/voice.csv --repeat 3

Reads the localhost token from the Keychain the same way the app does, so it must run
on the Mac with the core up (launchctl kickstart -k gui/$(id -u)/com.clixen.core).
Exit code 1 if any turn errored, so CI can gate on it.
"""
from __future__ import annotations

import argparse
import csv
import json
import os
import subprocess
import sys
import tempfile
import time
from pathlib import Path

BASE = os.environ.get("CLIXEN_BASE", "http://127.0.0.1:9234")

# One cheap factual answer, one spoken-reply answer, one that needs a tool round.
QUERIES = [
    ("simple", "What is two plus two?"),
    ("spoken", "Say hello in one short sentence."),
    ("clock", "What time is it right now?"),
]


def _token() -> str:
    raw = subprocess.run(
        ["security", "find-generic-password", "-s", "clixen.localhost.token", "-w"],
        capture_output=True, text=True,
    ).stdout.strip()
    try:
        return json.loads(raw)["value"]
    except Exception:
        return raw


def _wav(text: str) -> Path:
    """16 kHz mono WAV — what the orb actually posts (see main.js's resampler)."""
    aiff = Path(tempfile.mkstemp(suffix=".aiff")[1])
    wav = Path(tempfile.mkstemp(suffix=".wav")[1])
    subprocess.run(["say", "-v", os.environ.get("CLIXEN_SAY_VOICE", "Samantha"),
                    "-o", str(aiff), text], check=True)
    subprocess.run(["afconvert", "-f", "WAVE", "-d", "LEI16@16000", "-c", "1",
                    str(aiff), str(wav)], check=True)
    aiff.unlink(missing_ok=True)
    return wav



def measure(query: str, *, tts: bool, chat_id: str) -> dict:
    import httpx

    path = _wav(query)
    row = {"query": query, "ok": False, "transcript_s": None, "ttft_s": None,
           "first_audio_s": None, "done_s": None, "tokens": 0, "audio_frames": 0, "text": ""}
    t0 = time.time()
    try:
        with httpx.stream(
            "POST", f"{BASE}/voice/process",
            headers={"x-clixen-token": _token()},
            data={"chat_id": chat_id, "tts": "1" if tts else "0"},
            files={"file": ("q.wav", path.read_bytes(), "audio/wav")},
            timeout=180,
        ) as resp:
            resp.raise_for_status()
            for line in resp.iter_lines():
                if not line.startswith("data: "):
                    continue
                try:
                    ev = json.loads(line[6:])
                except ValueError:
                    continue
                kind = ev.get("type")
                elapsed = round(time.time() - t0, 3)
                if kind == "transcript" and row["transcript_s"] is None:
                    row["transcript_s"] = elapsed
                elif kind == "token":
                    row["tokens"] += 1
                    if row["ttft_s"] is None:
                        row["ttft_s"] = elapsed
                elif kind == "audio":
                    row["audio_frames"] += 1
                    if row["first_audio_s"] is None:
                        row["first_audio_s"] = elapsed
                elif kind == "done":
                    row["done_s"] = elapsed
                    row["ok"] = True
                    break
                elif kind == "error":
                    row["error"] = ev.get("text", "")
                    break
                if kind in ("token", "final"):
                    row["text"] = (ev.get("text") or row["text"])[:120]
    except Exception as exc:  # noqa: BLE001 - a failed row, not a crash
        row["error"] = f"{type(exc).__name__}: {exc}"
    finally:
        path.unlink(missing_ok=True)
    return row


def _p50(rows: list[dict], key: str):
    vals = sorted(r[key] for r in rows if r[key] is not None)
    return vals[len(vals) // 2] if vals else None


def main() -> int:
    ap = argparse.ArgumentParser(description="Voice turn latency, hop by hop")
    ap.add_argument("--tts", action="store_true", help="include TTS (first audio frame)")
    ap.add_argument("--chat-id", default="voice_agent", help="chat id the turn is filed under")
    ap.add_argument("--repeat", type=int, default=1, help="runs per query")
    ap.add_argument("--csv", type=Path, help="append rows to this CSV")
    args = ap.parse_args()

    rows = []
    for _ in range(args.repeat):
        for label, query in QUERIES:
            row = measure(query, tts=args.tts, chat_id=args.chat_id)
            row["label"] = label
            rows.append(row)
            print(
                f"{label:7s} stt={row['transcript_s']}s ttft={row['ttft_s']}s "
                f"first_audio={row['first_audio_s']}s done={row['done_s']}s "
                f"tokens={row['tokens']} audio={row['audio_frames']} "
                f"ok={row['ok']} {row.get('error', '')}"
            )

    if args.csv:
        new = not args.csv.exists()
        with args.csv.open("a", newline="") as fh:
            writer = csv.DictWriter(fh, fieldnames=list(rows[0]))
            if new:
                writer.writeheader()
            writer.writerows(rows)
        print(f"appended {len(rows)} rows -> {args.csv}")

    done = [r for r in rows if r["ok"]]
    if done:
        print(f"p50: stt={_p50(done, 'transcript_s')}s ttft={_p50(done, 'ttft_s')}s "
              f"first_audio={_p50(done, 'first_audio_s')}s done={_p50(done, 'done_s')}s")
    return 0 if len(done) == len(rows) else 1


if __name__ == "__main__":
    sys.exit(main())
