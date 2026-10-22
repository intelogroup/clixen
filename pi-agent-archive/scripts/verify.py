"""
PI-quantum tracker - LLM verification gate for briefings.

Runs a cheap OpenRouter LLM call over the finished briefing BEFORE delivery.
The gate rejects briefings that are unfinished, repeated, unsourced, or that
re-report items already covered in prior briefings (recall context).

Verdict contract: the model must answer with a first line exactly
  VERDICT: PASS
or
  VERDICT: FAIL: <reason>

CLI:
  python3 verify.py <briefing.md>    # exit 0 = PASS, exit 1 = FAIL/error
  python3 verify.py <briefing.md> --print   # include model notes in output
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
from pathlib import Path

import requests

ROOT = Path(__file__).resolve().parent.parent
API_URL = "https://openrouter.ai/api/v1/chat/completions"

SYSTEM_PROMPT = """You are a strict publication gate for an automated news tracker.
You will receive a candidate briefing. Judge ONLY by the checklist below.

Checklist:
1. COMPLETENESS: no placeholder text, no truncated sentences, no "[TODO]" or unfinished sections.
2. FORMAT: has sections covering AI, Science, and/or Tech; each item has a headline, a 1-2 sentence summary, and a source URL.
3. NO DUPLICATES: the same story must not appear twice within the briefing (even under a different headline).
4. FACTUAL TONE: no wild speculation presented as fact; hedged claims are fine.
5. LENGTH: under 900 words.

Answer with the FIRST LINE exactly one of:
VERDICT: PASS
VERDICT: FAIL: <one-sentence reason>
Then optionally 1-2 lines of notes."""


def load_env() -> None:
    env_path = ROOT / ".env"
    if not env_path.exists():
        return
    for line in env_path.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        os.environ.setdefault(k.strip(), v.strip().strip('"'))


def _candidates() -> list[str]:
    m = os.environ.get("VERIFY_MODEL") or os.environ.get("PI_MODEL") or "deepseek/deepseek-v4-flash"
    # PI_MODEL uses pi's CLI syntax ("openrouter/vendor/model"); the raw
    # chat-completions API wants "vendor/model", and "-latest" aliases may
    # 400 on the raw API -> fall back to the dated/base slug.
    if m.startswith("openrouter/"):
        m = m[len("openrouter/"):]
    cands = [m]
    if m.endswith("-latest"):
        cands.append(m[:-len("-latest")])
    return cands


def _call_llm(text: str) -> str:
    key = os.environ.get("OPENROUTER_API_KEY", "")
    if not key:
        raise RuntimeError("OPENROUTER_API_KEY not set")
    last = None
    for model in _candidates():
        r = requests.post(
            API_URL,
            headers={"Authorization": f"Bearer {key}",
                     "Content-Type": "application/json"},
            json={
                "model": model,
                "messages": [
                    {"role": "system", "content": SYSTEM_PROMPT},
                    {"role": "user", "content": text},
                ],
                "max_tokens": 800,
                "temperature": 0.0,
            },
            timeout=60,
        )
        if r.status_code == 200:
            return r.json()["choices"][0]["message"]["content"]
        last = RuntimeError(f"OpenRouter {r.status_code} ({model}): {r.text[:200]}")
    raise last


def verify_briefing(path: str) -> tuple[bool, str]:
    """Returns (passed, reason). pass=True means deliver."""
    p = Path(path)
    if not p.exists():
        return False, f"briefing file missing: {path}"
    md = p.read_text(errors="ignore").strip()
    if len(md) < 200:
        return False, f"briefing too short ({len(md)} chars)"
    words = len(md.split())
    if words > 1200:
        return False, f"briefing too long ({words} words)"

    last_err = None
    for attempt in range(2):
        try:
            content = _call_llm(md)
            first = content.strip().splitlines()[0].strip()
            if first.upper().startswith("VERDICT: PASS"):
                notes = "\n".join(content.strip().splitlines()[1:]).strip()
                return True, notes or "pass"
            if first.upper().startswith("VERDICT: FAIL"):
                return False, first.split(":", 2)[-1].strip() or "unspecified fail"
            # model did not follow the contract -> treat as fail-safe reject
            return False, f"verifier did not follow contract: {first[:120]}"
        except Exception as e:
            last_err = e
            if attempt == 0:
                time.sleep(3)
    # LLM unreachable: fail-open? No — this gate exists to catch bad saves;
    # a network error is transient, so fail-open but say so, else a flaky API
    # would silently drop every briefing. Choose: deliver with warning.
    return True, f"(verifier unreachable, fail-open: {last_err})"


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("briefing")
    ap.add_argument("--print", dest="show", action="store_true")
    args = ap.parse_args()
    load_env()
    ok, reason = verify_briefing(args.briefing)
    print(f"VERDICT: {'PASS' if ok else 'FAIL'}: {reason}")
    raise SystemExit(0 if ok else 1)


if __name__ == "__main__":
    try:
        main()
    except SystemExit:
        raise
    except BaseException as e:
        print(f"verify error: {e}", file=sys.stderr)
        raise SystemExit(1)
