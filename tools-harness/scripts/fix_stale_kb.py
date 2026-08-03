"""
Fix stale KB rows — delete audio_transcript junk entries with empty lang field.

All 9 stale rows are short live-mic noise: "(crickets chirping)", "Hello", etc.
Not worth re-ingesting. Just purge them.

Usage:
    python scripts/fix_stale_kb.py [--dry-run]
"""
import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

import argparse
from store.knowledge_base import KnowledgeBase


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--dry-run", action="store_true",
                        help="Show what would be deleted without deleting")
    args = parser.parse_args()

    kb = KnowledgeBase()
    df = kb.table.to_pandas()

    stale = df[df["lang"] == ""]
    print(f"Total rows: {len(df)}")
    print(f"Stale rows (lang=''): {len(stale)}")

    if stale.empty:
        print("Nothing to do.")
        return

    print("\nStale rows:")
    for _, row in stale.iterrows():
        preview = row["content"][:60].replace("\n", " ")
        print(f"  [{row['source']}] {preview!r}")

    if args.dry_run:
        print("\n[dry-run] No changes made.")
        return

    kb.table.delete("lang = ''")
    after = kb.table.count_rows()
    print(f"\nDeleted {len(stale)} rows. Rows remaining: {after}")
    print("Done.")


if __name__ == "__main__":
    main()
