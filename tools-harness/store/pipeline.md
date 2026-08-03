# Data Pipeline — Storage Taxonomy

Three storage tiers. Each piece of data lives in exactly one primary tier.
The rule: **SQLite is source of truth. LanceDB is the search index. Temp is throwaway.**

---

## The Three Tiers

### Tier 1 — Temp (in-memory / /tmp)
- Never persisted across restarts
- No SQLite row, no LanceDB vector
- Cleaned up immediately after use

### Tier 2 — SQLite (raw store)
- Source of truth for all ingested content
- Original extracted text, metadata, full content
- Cheap to write, queryable by exact fields
- No embeddings here — just rows
- Location: `data/raw.db`

### Tier 3 — LanceDB (semantic index)
- Only data you need to SEARCH SEMANTICALLY
- Chunks (not full docs) + vectors
- Points back to SQLite via `raw_id` foreign key
- Expensive to write (embedding cost) — be selective
- Location: `data/knowledge.lance`

---

## Data Type Classification

### Chat / Conversation

| Data | Tier | TTL | Rationale |
|---|---|---|---|
| Current session messages | Temp | session | Only needed for context window |
| Tool call results (in-flight) | Temp | request | Injected then discarded |
| Conversation summaries | SQLite | forever | Low cost, useful for logging |
| Conversation summaries (if searchable) | LanceDB | forever | Only if you want "remember when we discussed X" |

**Decision:** Chat history → SQLite only by default. Opt-in to embed if building memory feature.

---

### PDFs

| Data | Tier | TTL | Rationale |
|---|---|---|---|
| Original PDF bytes | Filesystem | forever | Source file, never duplicate |
| Extracted text (pdfplumber) | SQLite | forever | Raw text, re-processable |
| OCR output (Surya/Tesseract) | SQLite | forever | Expensive to re-run |
| Page images (during OCR) | Temp | request | /tmp, deleted after OCR |
| Text chunks | LanceDB | forever | For semantic search |

**Decision:** PDF → extract text → SQLite (full) + LanceDB (chunks). Never re-OCR if SQLite row exists.

---

### Voice / Audio

| Data | Tier | TTL | Rationale |
|---|---|---|---|
| Raw audio file | Filesystem | forever | Source file |
| Converted WAV (16kHz mono) | Temp | request | /tmp, just for whisper-cpp |
| Whisper transcript (full) | SQLite | forever | Expensive to re-run |
| Transcript chunks | LanceDB | forever | For semantic search |
| TTS output WAV | Temp | session | Spoken once, not needed again |

**Decision:** Audio → whisper → SQLite (full transcript) + LanceDB (chunks). TTS output never stored.

---

### Video

| Data | Tier | TTL | Rationale |
|---|---|---|---|
| Raw video file | Filesystem | forever | Source file |
| Extracted frames (JPEGs) | Temp | request | /tmp, deleted after Gemma vision call |
| Gemma vision description | SQLite | forever | What Gemma said about the video |
| Description chunks | LanceDB | forever | For semantic search |

**Decision:** Video → frames (temp) → Gemma → description → SQLite + LanceDB.

---

### Excel / DOCX / Structured Files

| Data | Tier | TTL | Rationale |
|---|---|---|---|
| Original file | Filesystem | forever | Source file |
| Extracted rows/text | SQLite | forever | Structured, queryable by column |
| Text representation | LanceDB | forever | For semantic search |

**Decision:** Always SQLite first (with column structure intact). Embed the text summary, not raw CSV rows.

---

### Tavily Search Results

| Data | Tier | TTL | Rationale |
|---|---|---|---|
| Full raw API response (JSON) | SQLite | 6h | Full response for debugging/audit |
| Trimmed result string | LanceDB | 6h | For semantic cache lookup |

**Decision:** Full response in SQLite (cheap), trimmed+embedded in LanceDB (for cache hit). Both expire at 6h.
SQLite TTL enforced by `expires_at` column + cleanup job.
LanceDB TTL enforced by `created_at` + staleness filter at search time.

---

### Context7 Docs

| Data | Tier | TTL | Rationale |
|---|---|---|---|
| Full doc response | SQLite | 7d | Full content for re-chunking |
| Doc chunks | LanceDB | 7d | For semantic search |

---

### Gemma/LLM Responses

| Data | Tier | TTL | Rationale |
|---|---|---|---|
| Raw response text | Temp | request | Default — not saved |
| Response (if important) | SQLite | forever | Explicit opt-in: `save=True` |
| Response (if searchable) | LanceDB | forever | Explicit opt-in: `save=True, embed=True` |

**Decision:** LLM responses are NOT auto-saved. Caller opts in explicitly.

---

## SQLite Schema (raw.db)

```sql
CREATE TABLE raw_docs (
    id          TEXT PRIMARY KEY,   -- sha256[:16] of content
    source      TEXT NOT NULL,      -- tavily_search | ocr_pdf | whisper | excel | ...
    file_path   TEXT,               -- original file path if applicable
    query       TEXT,               -- original query / filename
    url         TEXT,
    content     TEXT NOT NULL,      -- full extracted content (not chunked)
    metadata    TEXT,               -- JSON blob: page count, lang, method, etc.
    created_at  REAL NOT NULL,
    expires_at  REAL                -- NULL = forever, unix ts = TTL deadline
);

CREATE INDEX idx_source ON raw_docs(source);
CREATE INDEX idx_expires ON raw_docs(expires_at);
```

LanceDB rows have `raw_id TEXT` pointing back to `raw_docs.id`.

---

## Write Flow (per data type)

```
Input arrives
    │
    ├─ Is it a source file? (PDF/audio/video/xlsx)
    │       YES → store file_path in SQLite → extract content
    │
    ├─ Does SQLite already have this content? (sha256 match)
    │       YES → skip extraction, use cached content
    │
    ├─ Write full content to SQLite (raw_docs)
    │
    ├─ Should this be searchable? (all except Temp tier)
    │       YES → chunk → embed → write to LanceDB (with raw_id FK)
    │
    └─ Return raw_id + chunk_ids
```

## Read Flow (query)

```
Query comes in
    │
    ├─ Check LanceDB semantic cache (is_cached)
    │       HIT (fresh) → return content, done
    │
    ├─ MISS → hit external API (Tavily / Context7)
    │
    ├─ Write to SQLite (full response)
    ├─ Write chunks to LanceDB
    │
    └─ Return to model
```

---

## What goes where — quick reference

| Data | Filesystem | SQLite | LanceDB |
|---|---|---|---|
| Original files | YES | path only | no |
| PDF extracted text | no | YES (full) | chunks |
| OCR output | no | YES (full) | chunks |
| Audio WAV (converted) | /tmp only | no | no |
| Whisper transcript | no | YES (full) | chunks |
| TTS output WAV | /tmp only | no | no |
| Video frames | /tmp only | no | no |
| Gemma vision output | no | opt-in | opt-in |
| Excel extracted text | no | YES (full) | chunks |
| Tavily results | no | YES + 6h TTL | trimmed + 6h TTL |
| Context7 docs | no | YES + 7d TTL | chunks + 7d TTL |
| Chat history | no | opt-in | opt-in |
| LLM responses | no | opt-in | opt-in |

---

## Status

Implemented: `store/raw_store.py` (Tier 2, `data/raw.db`) and `store/knowledge_base.py`
(Tier 3, `data/knowledge.lance`) both exist and match the paths/roles above.

## Known open questions (not yet resolved, not blocking)

1. **Dedup on ingest** — sha256 of content as primary key means re-ingesting the
   same file is a no-op. Is that always correct, or do we want versioning
   (same file, updated content)?

2. **SQLite cleanup job** — run on startup (delete where expires_at < now()) or
   on a cron? Cron adds complexity, startup is simpler.

3. **LanceDB delete** — LanceDB supports `.delete(where=...)`. When SQLite row
   expires, should we also delete the LanceDB vectors? Or just let the staleness
   filter handle it at query time?

4. **File path tracking** — if original file moves/deletes, SQLite row becomes
   orphaned. Track file hash too, or just accept this?

5. **Cross-project shared KB** — zl_master_board, mee-app etc. would all write
   to the same `data/raw.db` + `data/knowledge.lance`. Single writer assumed.
   Need WAL mode on SQLite for concurrent reads.
