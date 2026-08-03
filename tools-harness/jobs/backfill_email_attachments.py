"""
One-off backfill: download every inbox attachment (any type) received since a
given date into ~/Documents/email-attachments/<Month>/w<N>/, same foldering as
jobs.handlers.email_attachment_archive. Marks every processed message ID as
seen on the "email.attachment_archive" workflow instance so the hourly job
doesn't redo this work.

Run:
    cd tools-harness
    python -m jobs.backfill_email_attachments --since 2026/01/01
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

from dotenv import load_dotenv
load_dotenv(Path(__file__).parent.parent / ".env")

sys.path.insert(0, str(Path(__file__).parent.parent))

from store import workflow_store
from tools.gmail import _gmail_service, _find_any_attachments, download_attachment, execute_with_google_auth_retry
from jobs.handlers.email_attachment_archive import _month_week_dir


def _get_instance() -> dict:
    for w in workflow_store.list_workflow_instances():
        if w["automation_id"] == "email.attachment_archive":
            return w
    raise RuntimeError("email.attachment_archive workflow instance not found — run seed_builtins() first")


def backfill(since: str) -> None:
    inst = _get_instance()
    config = dict(inst.get("config") or {})
    seen_ids = set(config.get("seen_ids", []))

    svc = _gmail_service()
    query = f"in:inbox has:attachment after:{since}"

    saved = 0
    page_token = None
    while True:
        result = execute_with_google_auth_retry(
            lambda pt=page_token: svc.users().messages().list(
                userId="me", q=query, maxResults=100, pageToken=pt
            ).execute()
        )
        messages = result.get("messages", [])
        for m in messages:
            mid = m["id"]
            if mid in seen_ids:
                continue
            msg = execute_with_google_auth_retry(
                lambda message_id=mid: svc.users().messages().get(
                    userId="me", id=message_id, format="full"
                ).execute()
            )
            payload = msg.get("payload", {})
            attachments = _find_any_attachments(payload)
            if attachments:
                dest_dir = _month_week_dir(msg.get("internalDate", ""))
                for att in attachments:
                    path = download_attachment(
                        message_id=mid,
                        attachment_id=att["attachment_id"],
                        filename=att["filename"],
                        dest_dir=str(dest_dir),
                    )
                    if path:
                        saved += 1
                        print(f"  saved {att['filename']} -> {dest_dir}")
            seen_ids.add(mid)

        page_token = result.get("nextPageToken")
        if not page_token:
            break

    seen_list = list(seen_ids)[-5000:]
    workflow_store.update_workflow_instance(inst["id"], config={**config, "seen_ids": seen_list})
    print(f"\nbackfill complete: {saved} attachments saved, {len(seen_ids)} messages marked seen")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--since", default="2026/01/01", help="Gmail date filter, format YYYY/MM/DD")
    args = parser.parse_args()
    backfill(args.since)


if __name__ == "__main__":
    main()
