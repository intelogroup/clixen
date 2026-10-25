#!/usr/bin/env python3
"""Live site suite for the orb: does it see the popup, does it dismiss it, does it read?

Deliberately NOT an LLM run. This drives the same tools the orb uses (visible
Chrome, AX) and scores what the *machine* can see, so a regression shows up as a
red row instead of a plausible-sounding sentence.

Safety rules baked in, not left to the caller:
- only public, no-login, no-purchase sites (see SITES);
- a consent banner is dismissed, never accepted or opted-out: choosing is the
  user's privacy election, and the report says which choice was made;
- one visit per site per run, no scrolling, no form filling, no clicking beyond a
  neutral dismissal.

    python scripts/site_suite.py                 # all sites, table
    python scripts/site_suite.py --json
    python scripts/site_suite.py --only bbc,theguardian
"""
from __future__ import annotations

import argparse
import json
import pathlib
import sys
import time

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

# Public, no login, no purchase, safe to automate. A site earns a slot by being a
# realistic JS surface (heavy DOM, consent CMP, or a browser bubble), not by being
# convenient.
SITES: dict[str, str] = {
    "example": "https://example.com",
    "wikipedia": "https://en.wikipedia.org/wiki/United_States_Capitol",
    "bbc": "https://www.bbc.com/news",
    "theguardian": "https://www.theguardian.com/us",
}

DISMISS_WORDS = ("closer", "close", "dismiss", "got it", "maybe later", "not now", "no thanks")


def _obs(execute_tool) -> dict:
    raw = execute_tool("observe", {"app": "Google Chrome"})
    body = raw.split(">", 1)[1].rsplit("</external_content>", 1)[0] if "<external_content" in raw else raw
    try:
        return json.loads(body)
    except Exception:
        return {}


def run_site(name: str, url: str, execute_tool, settle: float = 3.0) -> dict:
    row = {"site": name, "url": url, "loaded": False, "banners": 0, "choice": "none",
           "headline_chars": 0, "seconds": 0.0, "note": ""}
    t0 = time.time()
    out = execute_tool("open_url", {"url": url, "app": "Google Chrome"})
    if "Error" in out:
        row["note"] = out[:120]
        return row
    time.sleep(settle)

    obs = _obs(execute_tool)
    row["title"] = (obs.get("title") or "")[:80]
    url_now = obs.get("url") or ""
    row["loaded"] = bool(obs.get("elements")) and ("http" in url_now or "http" in (obs.get("title") or ""))

    # A banner can be reported in overlays, and a browser bubble is a popover.
    for _ in range(3):
        overlays = " ".join(obs.get("overlays") or [])
        if not overlays:
            break
        row["banners"] += 1
        labels = [(e.get("id"), (e.get("label") or "")) for e in obs.get("elements", [])]
        # Neutral controls only: closing is reversible, accepting is an election.
        neutral = [x for x in labels if x[1].strip().lower() in DISMISS_WORDS]
        if neutral:
            target, label = neutral[0]
            res = execute_tool("act", {"target": target, "action": "click", "app": "Google Chrome"})
            ok = '"ok": true' in res
            row["choice"] = f"dismissed via '{label}'" if ok else f"dismiss failed ({label})"
        else:
            # No neutral control: say so rather than electing accept/opt-out.
            row["choice"] = "left open (only accept/opt-out offered)"
            break
        time.sleep(2)
        obs = _obs(execute_tool)

    text = execute_tool("read_page_text", {"app": "Google Chrome"})
    row["headline_chars"] = len(text or "")
    row["seconds"] = round(time.time() - t0, 1)
    row["banner_after"] = bool(_obs(execute_tool).get("overlays"))
    return row



def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--only", default="")
    ap.add_argument("--settle", type=float, default=3.0)
    args = ap.parse_args()

    from tools.registry import execute_tool

    wanted = [s.strip() for s in args.only.split(",") if s.strip()]
    sites = {k: v for k, v in SITES.items() if not wanted or k in wanted}
    rows = [run_site(name, url, execute_tool, args.settle) for name, url in sites.items()]

    if args.json:
        print(json.dumps(rows, indent=2))
    else:
        print(f"{'site':14s} {'loaded':7s} {'banners':8s} {'headline':9s} {'secs':6s} choice")
        for r in rows:
            print(f"{r['site']:14s} {str(r['loaded']):7s} {r['banners']:<8d} "
                  f"{r['headline_chars']:<9d} {r['seconds']:<6} {r['choice']}")
            if r.get("note"):
                print(f"    note: {r['note']}")
    return 0 if all(r["loaded"] for r in rows) else 1


if __name__ == "__main__":
    raise SystemExit(main())
