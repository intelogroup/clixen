"""
Ingest Presto narration transcripts → raw.db + knowledge.lance.

Reads all JSON files from ~/Developer/presto/presto_backend/output/transcripts/,
stores the flat transcript text in RawStore (deduped by sha256) and KnowledgeBase
(embedded with nomic, chunked for long transcripts).

Usage:
    python scripts/ingest_presto_transcripts.py [--dry-run] [--no-kb]
"""
import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

import argparse
import json
from pathlib import Path

TRANSCRIPTS_DIR = Path.home() / "Developer/presto/presto_backend/output/transcripts"


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--no-kb", action="store_true",
                        help="Skip KnowledgeBase embedding (raw.db only)")
    args = parser.parse_args()

    files = sorted(TRANSCRIPTS_DIR.glob("*.json"))
    if not files:
        print(f"No JSON files found at {TRANSCRIPTS_DIR}")
        return

    print(f"Found {len(files)} transcript files")

    if args.dry_run:
        for f in files:
            d = json.loads(f.read_text())
            text = d.get("text", "").strip()
            print(f"  {f.name}: {len(text)} chars, lang={d.get('language','?')}")
        print("[dry-run] No changes made.")
        return

    from store.raw_store import RawStore
    from store.knowledge_base import KnowledgeBase

    rs = RawStore()
    kb = KnowledgeBase() if not args.no_kb else None

    stored = skipped = kb_chunks = 0

    for f in files:
        try:
            d = json.loads(f.read_text(encoding="utf-8"))
        except Exception as e:
            print(f"  SKIP {f.name}: {e}")
            continue

        text = d.get("text", "").strip()
        if len(text) < 50:
            print(f"  SKIP {f.name}: too short ({len(text)} chars)")
            skipped += 1
            continue

        lang = "en"  # all presto transcripts are English narrations
        file_id = f.stem
        meta = {
            "duration_sec": d.get("duration"),
            "language":     d.get("language"),
            "segment_count": len(d.get("segments", [])),
        }

        raw_id, was_new = rs.store(
            content=text,
            source="audio_transcript",
            file_path=str(f.resolve()),
            query=file_id,
            metadata=meta,
        )

        if not was_new:
            print(f"  SKIP {f.name}: already in raw.db")
            skipped += 1
            continue

        stored += 1
        print(f"  STORE {f.name}: {len(text)} chars → raw_id={raw_id}")

        if kb is not None:
            chunk_ids = kb.store(
                content=text,
                source="audio_transcript",
                query=file_id,
                lang=lang,
                method="whisper",
                auto_chunk=True,
            )
            kb_chunks += len(chunk_ids)
            print(f"    → {len(chunk_ids)} KB chunks embedded")

    print(f"\nDone. Stored: {stored}, Skipped: {skipped}, KB chunks: {kb_chunks}")
    print("\nraw.db stats:", rs.stats())
    if kb:
        print("KB stats:", kb.stats())


if __name__ == "__main__":
    main()
