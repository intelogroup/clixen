"""
Ingest zl_master_board OCR chunks → raw.db + knowledge.lance.

Reads all rows from zl_docs_en and zl_docs_fr (5730 total), re-embeds with
nomic-embed-text (768-dim) and stores into clixen's own KB.

The source DBs use 1024-dim mxbai vectors — we read only the text fields.

Usage:
    python scripts/ingest_zl_master_board.py [--dry-run] [--limit N] [--no-kb]
    python scripts/ingest_zl_master_board.py --limit 20  # test run
"""
import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

import argparse
import lancedb
from pathlib import Path

ZL_DB_PATH = Path.home() / "Developer/zl_master_board/polo-ingest/data/zl_vector_db"
ZL_TABLES  = ["zl_docs_en", "zl_docs_fr"]


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--no-kb",   action="store_true",
                        help="Skip KnowledgeBase embedding (raw.db only)")
    parser.add_argument("--limit", type=int, default=0,
                        help="Max rows to process (0 = all)")
    args = parser.parse_args()

    if not ZL_DB_PATH.exists():
        print(f"zl_vector_db not found at {ZL_DB_PATH}")
        return

    zl_db = lancedb.connect(str(ZL_DB_PATH))

    total_rows = 0
    for tname in ZL_TABLES:
        tbl = zl_db.open_table(tname)
        total_rows += tbl.count_rows()
    print(f"Source: {ZL_DB_PATH}")
    print(f"Tables: {ZL_TABLES}")
    print(f"Total rows: {total_rows}")
    if args.limit:
        print(f"Limit: {args.limit} rows")
    if args.dry_run:
        print("[dry-run] No changes will be made.")

    if args.dry_run:
        for tname in ZL_TABLES:
            tbl = zl_db.open_table(tname)
            rows = tbl.to_pandas(limit=3) if hasattr(tbl, 'to_pandas') else []
            sample = tbl.search([0]*1024).limit(3).to_list()
            for r in sample:
                print(f"  [{tname}] lang={r.get('lang','')} page={r.get('page',0)} "
                      f"source={r.get('source','')[:40]} text={r.get('text','')[:60]!r}")
        return

    from store.raw_store import RawStore
    from store.knowledge_base import KnowledgeBase

    rs = RawStore()
    kb = KnowledgeBase() if not args.no_kb else None

    stored = skipped = kb_chunks = errors = 0
    processed = 0

    for tname in ZL_TABLES:
        tbl = zl_db.open_table(tname)
        n = tbl.count_rows()
        print(f"\nProcessing {tname} ({n} rows)…")

        # Paginate in batches of 200 to avoid OOM
        batch_size = 200
        offset = 0

        while True:
            if args.limit and processed >= args.limit:
                break

            batch = tbl.search([0]*1024).limit(batch_size).offset(offset).to_list()
            if not batch:
                break

            for row in batch:
                if args.limit and processed >= args.limit:
                    break

                text = (row.get("text") or "").strip()
                if len(text) < 10:
                    skipped += 1
                    processed += 1
                    continue

                lang     = row.get("lang", "")
                source_f = row.get("source", "")
                page     = int(row.get("page", 0))
                meta     = {"lang": lang, "page": page, "origin": tname}

                try:
                    raw_id, was_new = rs.store(
                        content=text,
                        source="ocr_pdf",
                        file_path=source_f,
                        query=source_f,
                        metadata=meta,
                    )
                except Exception as e:
                    errors += 1
                    processed += 1
                    continue

                if not was_new:
                    skipped += 1
                    processed += 1
                    continue

                stored += 1

                if kb is not None:
                    try:
                        chunk_ids = kb.store(
                            content=text,
                            source="ocr_pdf",
                            query=source_f,
                            page=page,
                            lang=lang,
                            method="ocr",
                            auto_chunk=True,
                        )
                        kb_chunks += len(chunk_ids)
                    except Exception as e:
                        errors += 1

                processed += 1
                if processed % 100 == 0:
                    print(f"  {processed} processed | {stored} stored | {skipped} skipped | {errors} errors")

            offset += batch_size
            if len(batch) < batch_size:
                break

    print(f"\nDone.")
    print(f"  Processed: {processed} | Stored: {stored} | Skipped: {skipped} | Errors: {errors}")
    print(f"  KB chunks embedded: {kb_chunks}")
    print(f"\nraw.db stats: {rs.stats()}")
    if kb:
        print(f"KB stats:     {kb.stats()}")


if __name__ == "__main__":
    main()
