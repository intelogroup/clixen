"""
Multi-DB reader — discovers and searches any LanceDB or SQLite on this machine.

clixen WRITES only to its own DB.
This module gives it READ access to any DB it can find.

Known DBs are registered manually (fast). Discovery scan finds new ones.
Results are normalized to a common format before returning.
"""
import logging
import os
from pathlib import Path
from typing import Optional
import requests

log = logging.getLogger(__name__)

OLLAMA_BASE = "http://localhost:11434"
EMBED_MODEL = "nomic-embed-text"

# ── Known DB registry ─────────────────────────────────────────────────────────
# Add any LanceDB or SQLite path you want the harness to be able to search.
# schema_map: how to extract (text, source, url) from each table's row schema.
#
# clixen's own stores are always registered. The author's personal projects
# (zl_master_board, malaria_thesis, qwen demo) were historically hardcoded here —
# they're now registered only when CLIXEN_EXTRA_DBS=1 is set, so a fresh clone
# doesn't carry someone else's ~/Developer paths. Any additional DBs can be
# injected via CLIXEN_EXTRA_LANCE_DBS / CLIXEN_EXTRA_SQLITE_DBS (JSON dicts,
# same schema as below).

_EXTRA_LANCE_ENV = os.environ.get("CLIXEN_EXTRA_LANCE_DBS", "")
_EXTRA_SQLITE_ENV = os.environ.get("CLIXEN_EXTRA_SQLITE_DBS", "")

KNOWN_LANCE_DBS = {
    "clixen": {
        "path":   str(Path(__file__).parent.parent / "data" / "knowledge.lance"),
        "tables": ["knowledge"],
        "text_field":   "content",
        "source_field": "source",
        "url_field":    "url",
        "owner":        "clixen",
        "writable":     True,
        "embed_dim":    768,
        "embed_model":  "nomic-embed-text",  # via Ollama
    },
}

if os.environ.get("CLIXEN_EXTRA_DBS") == "1":
    KNOWN_LANCE_DBS.update({
        "zl_master_board_en": {
            "path":   str(Path.home() / "Developer/zl_master_board/polo-ingest/data/zl_vector_db"),
            "tables": ["zl_docs_en"],
            "text_field":   "text",
            "source_field": "source",
            "url_field":    None,
            "owner":        "zl_master_board",
            "writable":     False,
            "embed_dim":    1024,
            "embed_model":  "mixedbread-ai/mxbai-embed-large-v1",  # via fastembed
        },
        "zl_master_board_fr": {
            "path":   str(Path.home() / "Developer/zl_master_board/polo-ingest/data/zl_vector_db"),
            "tables": ["zl_docs_fr"],
            "text_field":   "text",
            "source_field": "source",
            "url_field":    None,
            "owner":        "zl_master_board",
            "writable":     False,
            "embed_dim":    1024,
            "embed_model":  "intfloat/multilingual-e5-large",  # via fastembed
        },
        "malaria_thesis_en": {
            "path":   str(Path.home() / "Developer/malaria_thesis/data/vector_db"),
            "tables": ["malaria_thesis_docs"],
            "text_field":   "text",
            "source_field": "source",
            "url_field":    None,
            "owner":        "malaria_thesis",
            "writable":     False,
            "embed_dim":    1024,
            "embed_model":  "mixedbread-ai/mxbai-embed-large-v1",
        },
        "malaria_thesis_fr": {
            "path":   str(Path.home() / "Developer/malaria_thesis/data/vector_db"),
            "tables": ["malaria_thesis_docs_fr"],
            "text_field":   "text",
            "source_field": "source",
            "url_field":    None,
            "owner":        "malaria_thesis",
            "writable":     False,
            "embed_dim":    1024,
            "embed_model":  "intfloat/multilingual-e5-large",
        },
        "qwen_lancedb_demo": {
            "path":   str(Path.home() / "Developer/qwen-opensrc-lancedb-demo/lancedb_data"),
            "tables": ["zod_chunks"],
            "text_field":   "text",
            "source_field": "file_path",
            "url_field":    None,
            "owner":        "qwen-opensrc-lancedb-demo",
            "writable":     False,
            "embed_dim":    768,
            "embed_model":  "nomic-embed-text",
        },
    })

KNOWN_SQLITE_DBS = {
    "clixen_raw": {
        "path":     str(Path(__file__).parent.parent / "data" / "raw.db"),
        "table":    "raw_docs",
        "text_field":   "content",
        "source_field": "source",
        "owner":    "clixen",
        "writable": True,
    },
}

if os.environ.get("CLIXEN_EXTRA_DBS") == "1":
    KNOWN_SQLITE_DBS["zl_queue"] = {
        "path":     str(Path.home() / "Developer/zl_master_board/polo-ingest/queue.db"),
        "table":    "chunks",
        "text_field":   "text",
        "source_field": "source",
        "owner":    "zl_master_board",
        "writable": False,
    }


def _json_extra(raw: str) -> dict:
    """Parse a CLIXEN_EXTRA_*_DBS env JSON blob into a dict, best-effort."""
    if not raw.strip():
        return {}
    try:
        import json
        parsed = json.loads(raw)
        return parsed if isinstance(parsed, dict) else {}
    except Exception:
        log.warning("Invalid CLIXEN_EXTRA_*_DBS JSON — ignoring")
        return {}


KNOWN_LANCE_DBS.update(_json_extra(_EXTRA_LANCE_ENV))
KNOWN_SQLITE_DBS.update(_json_extra(_EXTRA_SQLITE_ENV))


# ── Embedding ─────────────────────────────────────────────────────────────────

MODELS_DIR = Path(__file__).resolve().parent.parent.parent / "models"
LOCAL_ONNX = {
    "mixedbread-ai/mxbai-embed-large-v1": {
        "onnx":      str(MODELS_DIR / "mxbai/onnx/model.onnx"),
        "tokenizer": str(MODELS_DIR / "mxbai/tokenizer.json"),
        "token_type_ids": True,
    },
    "intfloat/multilingual-e5-large": {
        "onnx":      str(MODELS_DIR / "multilingual-e5/fast-multilingual-e5-large/model.onnx"),
        "tokenizer": str(MODELS_DIR / "multilingual-e5/fast-multilingual-e5-large/tokenizer.json"),
        "token_type_ids": False,
    },
}

# Cache loaded sessions — don't reload on every call
_SESSION_CACHE: dict = {}


def _embed_local_onnx(text: str, model_key: str) -> list[float]:
    """Mean-pool + L2-normalize using a local ONNX model."""
    import numpy as np
    import onnxruntime as ort
    from tokenizers import Tokenizer

    cfg = LOCAL_ONNX[model_key]

    if model_key not in _SESSION_CACHE:
        _SESSION_CACHE[model_key] = {
            "sess": ort.InferenceSession(cfg["onnx"]),
            "tok":  Tokenizer.from_file(cfg["tokenizer"]),
        }

    sess = _SESSION_CACHE[model_key]["sess"]
    tok  = _SESSION_CACHE[model_key]["tok"]
    tok.enable_truncation(max_length=512)

    enc  = tok.encode(text)
    ids  = np.array([enc.ids], dtype=np.int64)
    mask = np.array([enc.attention_mask], dtype=np.int64)
    feed = {"input_ids": ids, "attention_mask": mask}
    if cfg["token_type_ids"]:
        feed["token_type_ids"] = np.zeros_like(ids)

    out = sess.run(None, feed)[0][0]   # (seq_len, dim)
    vec = out.mean(axis=0)             # mean pool → (dim,)
    norm = np.linalg.norm(vec)
    return (vec / norm).tolist() if norm > 0 else vec.tolist()


def _embed(text: str, model: str = EMBED_MODEL, dim: int = 768) -> list[float]:
    """
    Route to correct embedder based on target DB model:
    - 768-dim → nomic-embed-text via Ollama
    - 1024-dim → mxbai or multilingual-e5 from local ONNX
    """
    import numpy as np

    if dim == 768:
        r = requests.post(
            f"{OLLAMA_BASE}/v1/embeddings",
            json={"model": EMBED_MODEL, "input": text[:4096]},
            timeout=30,
        )
        r.raise_for_status()
        vec = r.json()["data"][0]["embedding"]
        arr = np.array(vec, dtype=np.float32)
        norm = float(np.linalg.norm(arr))
        return (arr / norm).tolist() if norm > 0 else vec

    if model in LOCAL_ONNX and Path(LOCAL_ONNX[model]["onnx"]).exists():
        return _embed_local_onnx(text, model)

    raise RuntimeError(f"No local ONNX for model '{model}' — download it first")


# ── Normalize results ─────────────────────────────────────────────────────────

def _normalize_row(row: dict, db_key: str, cfg: dict) -> dict:
    """Map any DB schema to a common result shape."""
    return {
        "text":     row.get(cfg["text_field"], ""),
        "source":   row.get(cfg["source_field"], ""),
        "url":      row.get(cfg.get("url_field") or "", ""),
        "db":       db_key,
        "owner":    cfg["owner"],
        "_distance": row.get("_distance", None),
    }


# ── Search ────────────────────────────────────────────────────────────────────

def search_lance_db(db_key: str, query: str, top_k: int = 5) -> list[dict]:
    """Search a single registered LanceDB using the correct embedding model for that DB."""
    cfg = KNOWN_LANCE_DBS.get(db_key)
    if not cfg:
        return []
    db_path = cfg["path"]
    if not Path(db_path).exists():
        return []

    import lancedb
    db = lancedb.connect(db_path)
    qvec = _embed(query, model=cfg["embed_model"], dim=cfg["embed_dim"])
    results = []

    for table_name in cfg["tables"]:
        try:
            tbl = db.open_table(table_name)
            rows = tbl.search(qvec).limit(top_k).to_list()
            results.extend([_normalize_row(r, db_key, cfg) for r in rows])
        except Exception as e:
            continue

    return results


def search_sqlite_db(db_key: str, query: str, top_k: int = 5) -> list[dict]:
    """Full-text search (LIKE) on a registered SQLite DB — no vectors needed."""
    cfg = KNOWN_SQLITE_DBS.get(db_key)
    if not cfg:
        return []
    db_path = cfg["path"]
    if not Path(db_path).exists():
        return []

    import sqlite3
    conn = sqlite3.connect(db_path)
    try:
        conn.row_factory = sqlite3.Row
        terms = query.split()[:5]  # use first 5 words
        like_clause = " OR ".join(
            [f"{cfg['text_field']} LIKE ?" for _ in terms]
        )
        params = [f"%{t}%" for t in terms]

        try:
            rows = conn.execute(
                f"SELECT * FROM {cfg['table']} WHERE {like_clause} LIMIT ?",
                params + [top_k],
            ).fetchall()
        except Exception:
            log.debug("SQL query failed for table=%s", cfg["table"], exc_info=True)
            rows = []
    finally:
        conn.close()

    return [
        {
            "text":   r[cfg["text_field"]],
            "source": r[cfg["source_field"]],
            "url":    "",
            "db":     db_key,
            "owner":  cfg["owner"],
        }
        for r in rows
    ]


def search_all(
    query: str,
    top_k: int = 5,
    dbs: list[str] | None = None,
) -> list[dict]:
    """
    Search registered DBs and merge with RRF.

    dbs: optional list of KNOWN_LANCE_DBS keys to restrict to.
         Omit (or pass None) to search every registered DB.
    Returns results normalized to {text, content, source, url, db, owner, _distance}.
    """
    from store.knowledge_base import _rrf

    lance_keys = dbs if dbs else list(KNOWN_LANCE_DBS.keys())
    result_lists: list[list[dict]] = []

    for db_key in lance_keys:
        hits = search_lance_db(db_key, query, top_k=top_k)
        if hits:
            # Add 'content' alias so _rrf dedup key works (it uses doc['content'])
            result_lists.append([{**h, "content": h["text"]} for h in hits])

    if dbs is None:
        for db_key in KNOWN_SQLITE_DBS:
            hits = search_sqlite_db(db_key, query, top_k=top_k)
            if hits:
                result_lists.append([{**h, "content": h["text"]} for h in hits])

    if not result_lists:
        return []

    return _rrf(result_lists, top_k=top_k)


# ── Discovery ─────────────────────────────────────────────────────────────────

def _discovery_root() -> str:
    """Where the discovery scan searches. Default: the repo's own data dir
    (safe, zero surprise). Opt into scanning ~/Developer (or any root) via
    CLIXEN_DISCOVERY_ROOT — scanning a whole home/Developer tree recursively
    is slow and may surface unrelated private DBs."""
    env_root = os.environ.get("CLIXEN_DISCOVERY_ROOT", "").strip()
    if env_root:
        return env_root
    return str(Path(__file__).resolve().parent.parent / "data")


def discover_lance_dbs(root: str | None = None) -> list[str]:
    """
    Scan for top-level LanceDB directories under root.
    A valid LanceDB root is a *.lance directory whose parent does NOT end in .lance
    (excludes internal shard files which are nested .lance dirs inside the DB).
    """
    root = root or _discovery_root()
    known_paths = {cfg["path"] for cfg in KNOWN_LANCE_DBS.values()}
    found = []
    skip = {".venv", "node_modules", "miniforge3", ".git", "__pycache__"}
    for p in Path(root).rglob("*.lance"):
        if not p.is_dir():
            continue
        # Skip if any ancestor part is in the skip set
        if set(p.parts) & skip:
            continue
        # Skip nested .lance dirs (internal shards)
        if p.parent.suffix == ".lance":
            continue
        if str(p) not in known_paths:
            found.append(str(p))
    return found


def discover_sqlite_dbs(root: str | None = None) -> list[str]:
    """Scan for .db / .sqlite files under root not already registered."""
    root = root or _discovery_root()
    known_paths = {cfg["path"] for cfg in KNOWN_SQLITE_DBS.values()}
    found = []
    for p in Path(root).rglob("*.db"):
        if str(p) in known_paths:
            continue
        parts = set(p.parts)
        if parts & {".venv", "node_modules", "miniforge3", ".git", "__pycache__"}:
            continue
        found.append(str(p))
    return found
