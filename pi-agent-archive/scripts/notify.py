"""
PI-quantum tracker - notification + briefing delivery (Telegram).

Sends the daily briefing to the owner's Telegram chat after a successful run,
and alerts on failures. Mirrors clixen's telegram_send.py contract.

CLI:
  python3 notify.py briefing <path-to-md>     # sends the briefing content as a message
  python3 notify.py alert "message"           # sends a failure/critical alert
"""
from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

import requests

ROOT = Path(__file__).resolve().parent.parent


def load_env() -> None:
    env_path = ROOT / ".env"
    if not env_path.exists():
        return
    for line in env_path.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        os.environ[k.strip()] = v.strip().strip('"')


def send(text: str, chat_id: str | None = None) -> str:
    token = os.environ.get("TELEGRAM_BOT_TOKEN", "")
    cid = chat_id or os.environ.get("TELEGRAM_OWNER_CHAT_ID", "")
    base = os.environ.get("TELEGRAM_BASE_URL", "https://api.telegram.org/bot")
    if not token or not cid:
        return "[telegram error] TELEGRAM_BOT_TOKEN / TELEGRAM_OWNER_CHAT_ID not set"
    url = f"{base}{token}/sendMessage"
    if len(text) > 4000:
        text = text[:4000] + "\n…(truncated)"
    last_exc = None
    for attempt in range(2):
        try:
            r = requests.post(url, json={"chat_id": cid, "text": text}, timeout=10)
            res = r.json()
            if res.get("ok"):
                return f"sent (message_id={res['result']['message_id']})"
            return f"[telegram error] {res}"
        except Exception as e:
            last_exc = e
            if attempt == 0:
                import time
                time.sleep(2)
    return f"[telegram error] {last_exc}"


def send_briefing(path: str) -> str:
    p = Path(path)
    if not p.exists():
        return f"[error] briefing not found: {path}"
    md = p.read_text(errors="ignore")
    # strip markdown links to plain text for Telegram
    import re
    md = re.sub(r"\[([^\]]+)\]\(([^)]+)\)", r"\1 (\2)", md)
    header = f"== PI-quantum tracker briefing — {p.stem} ==\n\n"
    return send(header + md)


def main() -> None:
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest="cmd", required=True)
    b = sub.add_parser("briefing")
    b.add_argument("path")
    a = sub.add_parser("alert")
    a.add_argument("message")
    args = ap.parse_args()
    load_env()
    if args.cmd == "briefing":
        print(send_briefing(args.path))
    elif args.cmd == "alert":
        print(send(f"[PI-quantum ALERT] {args.message}"))


if __name__ == "__main__":
    try:
        main()
    except BaseException as e:
        print(f"notify error: {e}", file=sys.stderr)
        raise SystemExit(1)