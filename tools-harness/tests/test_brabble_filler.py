"""Filler-utterance gating in brabble_hook._run_harness_streaming.

Proves the latency-masking filler fires ONLY when first token is slow, and is
cancelled by a fast token — the two branches that matter. Fakes urlopen so no
server/kokoro is needed; patches _speak/_play to record calls.
"""
import json
import sys
import time
import types
from contextlib import contextmanager
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import brabble_hook as bh


class _FakeResp:
    def __init__(self, lines): self._lines = lines
    def __enter__(self): return self
    def __exit__(self, *a): return False
    def __iter__(self):
        for delay, payload in self._lines:
            if delay: time.sleep(delay)
            yield ("data: " + json.dumps(payload) + "\n").encode()


def _run(lines, monkeypatch_speak):
    spoken = []
    orig_speak = bh._speak
    bh._speak = lambda t: spoken.append(t)
    orig_synth = bh._synthesize_kokoro_chunks
    bh._synthesize_kokoro_chunks = lambda t: iter(())  # no kokoro -> say fallback path
    orig_run = bh.subprocess.run
    bh.subprocess.run = lambda *a, **k: None  # don't actually invoke macOS `say`
    import urllib.request as _req
    orig_urlopen = _req.urlopen
    _req.urlopen = lambda url, timeout=90: _FakeResp(lines)
    bh._FILLER_AFTER_S = 0.1  # speed up the test
    try:
        bh._run_harness_streaming("who won the euro", t0=time.perf_counter())
    finally:
        bh._speak = orig_speak
        bh._synthesize_kokoro_chunks = orig_synth
        bh.subprocess.run = orig_run
        _req.urlopen = orig_urlopen
    return spoken


def test_slow_first_token_fires_filler():
    # first token delayed past _FILLER_AFTER_S -> filler must speak
    spoken = _run([(0.3, {"token": "Italy won."}), (0, {"done": True})], None)
    assert any(s in bh._FILLERS for s in spoken), f"expected a filler, got {spoken}"


def test_fast_first_token_no_filler():
    # token arrives immediately -> timer cancelled, no filler
    spoken = _run([(0, {"token": "Italy won."}), (0, {"done": True})], None)
    assert not any(s in bh._FILLERS for s in spoken), f"unexpected filler: {spoken}"


if __name__ == "__main__":
    test_slow_first_token_fires_filler()
    test_fast_first_token_no_filler()
    print("ok")
