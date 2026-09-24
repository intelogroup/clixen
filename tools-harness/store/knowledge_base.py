"""
Persistent knowledge base — LanceDB + OpenAI text-embedding-3-small (cloud), nomic-embed-text (Ollama) as fallback; llama.cpp GGUF llama-server as the Ollama-down fallback (SAME nomic vector space, see _embed_gguf).

Lessons applied from malaria_thesis + zl_master_board/polo-ingest:
  1. Paragraph-aware chunking with overlap (not naive char splits)
  2. L2 normalization before storing (critical for cosine distance correctness)
  3. Over-fetch candidates (top_k * 4) then filter stale — don't under-fetch
  4. RRF (k=60) for merging multi-source result lists
  5. Rich schema: source, page, lang, method, enriched flag, created_at
  6. Dedup key = source::text[:100] (from polo-ingest rrf.py)
  7. TTL per source type — stale results excluded at search time
  8. is_cached() uses max_distance=0.65 (tuned from live distance observations)
"""
import hashlib
import logging
import time
from pathlib import Path
from typing import Optional

import lancedb
import numpy as np

log = logging.getLogger(__name__)
import pyarrow as pa
import requests

OLLAMA_BASE = "http://localhost:11434"
EMBED_MODEL = "nomic-embed-text"
OPENAI_EMBED_MODEL = "text-embedding-3-small"
EMBED_DIM = 768  # text-embedding-3-small truncated via `dimensions=` (MRL) to match existing lance schema

# Which backend produced the last embedding — set by _embed. Consumers (e.g.
# memory_tools thresholds) tune distance cutoffs per backend since their
# vector distributions differ.
EMBED_BACKEND: str = "ollama"

TTL = {
    "brave_search":     60 * 60 * 6,        # 6h — web changes fast
    "exa_search":       60 * 60 * 24,       # 24h — historical/tech queries, stable
    "serpapi_search":   60 * 60 * 6,        # 6h  — live sports/finance
    "context7_docs":    60 * 60 * 24 * 7,   # 7d — docs are stable
    "websearch_cache":  60 * 60 * 6,        # 6h — same bar as brave_search, web content moves
    "ocr_pdf":          float("inf"),
    "excel":            float("inf"),
    "audio_transcript": float("inf"),
    "manual":           float("inf"),
    "user_memory":      float("inf"),   # persistent cross-session memory — never stale
}

SCHEMA = pa.schema([
    pa.field("id",         pa.string()),
    pa.field("source",     pa.string()),     # brave_search | exa_search | context7_docs | ocr_pdf | ...
    pa.field("query",      pa.string()),     # original query / filename / url
    pa.field("content",    pa.string()),     # text chunk
    pa.field("url",        pa.string()),
    pa.field("page",       pa.int32()),      # page/sheet number, 0 if N/A
    pa.field("lang",       pa.string()),     # "en" | "fr" | ""
    pa.field("method",     pa.string()),     # how it was ingested: "direct" | "ocr" | "vision" | "whisper"
    pa.field("enriched",   pa.bool_()),      # True = Phase 2 re-processed
    pa.field("created_at", pa.float64()),
    pa.field("vector",     pa.list_(pa.float32(), EMBED_DIM)),
])

DB_PATH = None  # resolved lazily in __init__; see _resolve_db_path()

# 2026-08-04: knowledge DB moves out of the repo dir into the OS app-private
# data home (~/Library/Application Support/Clixen). Repo `data/` was the old
# home — never ~/Documents (iCloud-syncs by default) — see THREAT_MODEL.md.
# Tests still pass an explicit db_path, so the default only matters in prod.


def _resolve_db_path() -> str:
    import os as _os

    override = _os.environ.get("CLIXEN_KNOWLEDGE_PATH", "").strip()
    if override:
        return override
    from tools.vault_paths import db_path as _vault_db_path, migrate_legacy_data

    migrate_legacy_data()
    return _vault_db_path("knowledge.lance")


# ── Embedding ─────────────────────────────────────────────────────────────────

class EmbedBackendUnavailable(RuntimeError):
    """The backend a store's vectors were built with can't be reached right now.

    Raised instead of quietly embedding with the other backend: nomic-embed and
    text-embedding-3-small are different vector spaces, and comparing across them
    yields plausible-looking distances that mean nothing (see _embed).
    """


def _embed_openai(text: str) -> list[float]:
    import os

    global EMBED_BACKEND
    api_key = os.environ.get("OPENAI_API_KEY")
    if not api_key:
        raise EmbedBackendUnavailable("OPENAI_API_KEY is not set")
    r = requests.post(
        "https://api.openai.com/v1/embeddings",
        headers={"Authorization": f"Bearer {api_key}"},
        json={"model": OPENAI_EMBED_MODEL, "input": text[:4096], "dimensions": EMBED_DIM},
        timeout=10,
    )
    r.raise_for_status()
    vec = r.json()["data"][0]["embedding"]
    EMBED_BACKEND = "openai"
    return _normalize(vec)


def _embed_ollama(text: str) -> list[float]:
    global EMBED_BACKEND
    r = requests.post(
        f"{OLLAMA_BASE}/v1/embeddings",
        json={"model": EMBED_MODEL, "input": text[:4096]},
        timeout=30,
    )
    r.raise_for_status()
    vec = r.json()["data"][0]["embedding"]
    EMBED_BACKEND = "ollama"
    return _normalize(vec)


# ---------------------------------------------------------------------------
# GGUF fallback — the SAME nomic-embed-text-v1.5 vector space as Ollama, but
# served by the llama.cpp `llama-server` binary (brew install llama.cpp)
# instead of the Ollama daemon. Consulted only when Ollama itself is
# unreachable (see _embed), so the store's embed-backend stamp ("ollama")
# stays truthful: same model, same vector space, different runtime — NOT a
# third backend. A genuinely different HF model (e.g. Qwen3-Embedding) would
# need its own space/stamp and a full re-embed; keep it out of this path.
#
# Env knobs:
#   CLIXEN_GGUF_EMBED=0        disable the fallback entirely (default: on)
#   CLIXEN_GGUF_EMBED_URL      base URL of an already-running llama-server
#                              (default http://127.0.0.1:8091 — 8090 is used
#                              by the bge-reranker daemon on this machine)
#   CLIXEN_EMBED_GGUF          path to the GGUF file (default
#                              <repo>/models/<GGUF_FILENAME>, same gitignored
#                              dir that already holds bge-reranker)
# ---------------------------------------------------------------------------
GGUF_FILENAME = "nomic-embed-text-v1.5.Q8_0.gguf"  # ~144MB, Apache-2.0
GGUF_HF_URL = (
    "https://huggingface.co/nomic-ai/nomic-embed-text-v1.5-GGUF"
    f"/resolve/main/{GGUF_FILENAME}"
)
_GGUF_PROC: "subprocess.Popen | None" = None
_GGUF_VERIFIED: set = set()  # base_urls whose /v1/embeddings has been checked


def _gguf_enabled() -> bool:
    import os
    return os.environ.get("CLIXEN_GGUF_EMBED", "1").strip().lower() not in ("0", "off", "no")


def _gguf_model_path() -> Path:
    import os
    env = os.environ.get("CLIXEN_EMBED_GGUF")
    if env:
        return Path(env)
    return Path(__file__).resolve().parents[2] / "models" / GGUF_FILENAME


def _ensure_gguf_model(path: Path) -> Path:
    if path.is_file() and path.stat().st_size > 0:
        return path
    import os
    path.parent.mkdir(parents=True, exist_ok=True)
    # pid-suffixed temp file: two processes may race the first download; each
    # writes its own .part and the atomic replace keeps whichever lands first.
    tmp = path.with_name(f"{path.name}.part.{os.getpid()}")
    log.info("downloading GGUF embedding model %s -> %s", GGUF_HF_URL, path)
    with requests.get(GGUF_HF_URL, stream=True, timeout=60) as r:
        r.raise_for_status()
        with open(tmp, "wb") as f:
            for chunk in r.iter_content(1 << 20):
                f.write(chunk)
    tmp.replace(path)
    return path


def _gguf_health(base_url: str) -> bool:
    try:
        return requests.get(f"{base_url}/health", timeout=2).ok
    except Exception:
        return False


def _stop_gguf_server() -> None:
    global _GGUF_PROC
    proc = _GGUF_PROC
    if proc is not None and proc.poll() is None:
        proc.terminate()
        try:
            proc.wait(timeout=5)
        except Exception:
            proc.kill()


def _verify_gguf_server(base_url: str) -> None:
    """Confirm base_url answers in the nomic 768-dim space before trusting it.

    /health only proves *some* llama-server is there — on this very machine
    port 8090 is a bge-reranker daemon whose /v1/embeddings answers an
    all-zeros vector. Trusting that would silently corrupt the store, the
    exact failure mode this whole module exists to prevent (see _embed).
    """
    if base_url in _GGUF_VERIFIED:
        return
    r = requests.post(
        f"{base_url}/v1/embeddings",
        json={"model": "nomic-embed-text-v1.5", "input": "ping"},
        timeout=10,
    )
    r.raise_for_status()
    body = r.json()
    data = body.get("data") or []
    vec = data[0].get("embedding") if data else None
    served = Path(str(body.get("model") or "")).name.lower()
    want = _gguf_model_path().name.lower()
    if not vec or len(vec) != EMBED_DIM:
        raise EmbedBackendUnavailable(
            f"{base_url} served {served or 'no model!'} at "
            f"{len(vec) if vec else 0} dims (want {EMBED_DIM} nomic) — "
            "not a nomic-embed-text server; set CLIXEN_GGUF_EMBED_URL"
        )
    if "nomic" not in served and served != want:
        raise EmbedBackendUnavailable(
            f"{base_url} serves {body.get('model')!r}, not nomic-embed-text "
            "— set CLIXEN_GGUF_EMBED_URL"
        )
    if float(np.linalg.norm(np.array(vec, dtype=np.float32))) < 0.1:
        raise EmbedBackendUnavailable(
            f"{base_url} served a degenerate (near-zero) vector — refusing to "
            "write it into the store; set CLIXEN_GGUF_EMBED_URL"
        )
    _GGUF_VERIFIED.add(base_url)


def _ensure_gguf_server(base_url: str) -> None:
    """A llama-server answering on base_url, spawned here if nobody runs one.

    If something else already serves the port (a system daemon, another clixen
    process), we verify it is actually a nomic embedding server (_verify) —
    a foreign llama-server on the port is rejected, not used. Only a process
    we spawned gets terminated at atexit. The port-race case (someone binds
    between our health check and spawn) resolves the same way: our child dies
    of a bind error, the winner's server answers the poll.
    """
    global _GGUF_PROC
    import shutil
    import subprocess
    from urllib.parse import urlsplit

    if base_url in _GGUF_VERIFIED:
        return
    if not _gguf_health(base_url):
        if _GGUF_PROC is None or _GGUF_PROC.poll() is not None:
            binary = shutil.which("llama-server")
            if not binary:
                raise EmbedBackendUnavailable(
                    "llama-server not found on PATH (brew install llama.cpp)"
                )
            model = _ensure_gguf_model(_gguf_model_path())
            port = str(urlsplit(base_url).port or 8091)
            _GGUF_PROC = subprocess.Popen(
                [
                    binary,
                    "--model", str(model),
                    "--embedding",
                    "--host", "127.0.0.1",
                    "--port", port,
                    "-c", "2048",  # nomic-embed-text-v1.5's training context
                ],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
            import atexit
            atexit.register(_stop_gguf_server)
        deadline = time.time() + 120  # generous: first run may still be downloading
        while time.time() < deadline:
            if _gguf_health(base_url):
                break
            if _GGUF_PROC.poll() is not None:
                raise EmbedBackendUnavailable(
                    f"llama-server exited {_GGUF_PROC.returncode} before /health "
                    "was ready (port conflict? try CLIXEN_GGUF_EMBED_URL)"
                )
            time.sleep(0.5)
        else:
            raise EmbedBackendUnavailable("llama-server did not become ready within 120s")
    _verify_gguf_server(base_url)


def _embed_gguf(text: str) -> list[float]:
    """nomic-embed-text-v1.5 GGUF via llama.cpp — the SAME space as _embed_ollama.

    Reached only when Ollama itself is unreachable. Sets EMBED_BACKEND =
    "ollama" deliberately: this IS the ollama vector space, so the store stamp
    must keep saying "ollama".
    """
    import os
    global EMBED_BACKEND
    if not _gguf_enabled():
        raise EmbedBackendUnavailable("GGUF embed fallback disabled (CLIXEN_GGUF_EMBED=0)")
    base = os.environ.get("CLIXEN_GGUF_EMBED_URL", "http://127.0.0.1:8091").rstrip("/")
    _ensure_gguf_server(base)
    r = requests.post(
        f"{base}/v1/embeddings",
        json={"model": "nomic-embed-text-v1.5", "input": text[:4096]},
        timeout=30,
    )
    r.raise_for_status()
    vec = r.json()["data"][0]["embedding"]
    EMBED_BACKEND = "ollama"
    return _normalize(vec)


def _embed(text: str, backend: str | None = None) -> list[float]:
    """Embed `text`. With `backend` set, use exactly that one and fail if it's down.

    The fallback below is only safe for a store with no vectors in it yet. Falling
    from one backend to the other against existing vectors silently corrupts every
    comparison: both models are pinned to 768 dims (EMBED_DIM), so a cross-space
    search raises nothing and returns confident nonsense — measured 2026-09-10 on
    the live memory store, where a fact searched by its own exact text came back at
    distance 1.49 behind four unrelated rows, and recall_block() had been returning
    "" for every query for as long as Ollama had been down.
    """
    if backend == "openai":
        return _embed_openai(text)
    if backend == "ollama":
        try:
            return _embed_ollama(text)
        except EmbedBackendUnavailable:
            raise
        except Exception as e:
            # Same vector space through llama.cpp — this is a runtime fallback,
            # not the cross-backend switch the docstring above warns about.
            try:
                return _embed_gguf(text)
            except Exception as ge:
                raise EmbedBackendUnavailable(
                    f"Ollama embedding unavailable: {e}; GGUF fallback failed: {ge}"
                ) from e

    import os

    if os.environ.get("OPENAI_API_KEY"):
        try:
            return _embed_openai(text)
        except Exception:
            log.debug("OpenAI embedding failed, falling back to local Ollama", exc_info=True)
    try:
        return _embed_ollama(text)
    except EmbedBackendUnavailable:
        raise
    except Exception:
        log.debug("Ollama unreachable, trying GGUF llama-server fallback", exc_info=True)
        return _embed_gguf(text)


def _normalize(vec: list[float]) -> list[float]:
    """L2 normalization — lesson from malaria_thesis/vector_search.py line 128."""
    arr = np.array(vec, dtype=np.float32)
    norm = np.linalg.norm(arr)
    if norm == 0:
        return vec
    return (arr / norm).tolist()


def _content_id(content: str) -> str:
    return hashlib.sha256(content.encode()).hexdigest()[:16]


# ── RRF ───────────────────────────────────────────────────────────────────────

def _rrf(result_lists: list[list[dict]], top_k: int = 10, k: int = 60) -> list[dict]:
    """
    Reciprocal Rank Fusion — ported from polo-ingest/store/rrf.py.
    k=60 is empirically near-optimal (Cormack et al. 2009).
    Dedup key = source::text[:100] — avoids near-duplicate chunks ranking twice.
    """
    scores: dict[str, float] = {}
    docs:   dict[str, dict]  = {}

    for result_list in result_lists:
        for rank, doc in enumerate(result_list):
            key = f"{doc['source']}::{doc['content'][:100]}"
            scores[key] = scores.get(key, 0.0) + 1.0 / (k + rank + 1)
            docs[key] = doc

    ranked = sorted(scores.items(), key=lambda x: x[1], reverse=True)
    return [docs[key] for key, _ in ranked[:top_k]]


# ── KnowledgeBase ─────────────────────────────────────────────────────────────

class KnowledgeBase:
    def __init__(self, db_path: str | None = None):
        if db_path is None:
            db_path = _resolve_db_path()
            from tools.vault_paths import ensure_data_dir
            ensure_data_dir()
        Path(db_path).parent.mkdir(parents=True, exist_ok=True)
        self.db = lancedb.connect(db_path)
        self._stamp_path = Path(f"{db_path}.embed_backend")
        self._init_table()
        self.embed_backend = self._resolve_embed_backend()

    def _resolve_embed_backend(self) -> str | None:
        """Which vector space this store's rows live in, or None for an empty store
        that hasn't committed to one yet.

        Kept in a sibling file rather than a column: _init_table drops and recreates
        the table whenever SCHEMA's field set changes, so adding a field here would
        wipe every stored memory (see tools/memory_tools._build_tags).
        """
        stamped = ""
        try:
            stamped = self._stamp_path.read_text().strip()
        except OSError:
            pass
        if stamped in ("openai", "ollama"):
            return stamped
        if self.table.count_rows() == 0:
            return None
        probed = self._probe_embed_backend()
        if probed:
            self._write_embed_backend(probed)
            log.warning("stamped %s as embedded with %r (probed)", self._stamp_path.name, probed)
        return probed

    def _probe_embed_backend(self, sample_size: int = 12) -> str | None:
        """Work out which backend produced this store's vectors by re-embedding a sample
        of rows and seeing which model reproduces what's on disk.

        Measured, not assumed: this store looked like a nomic store by history, but 51 of
        its 75 rows turned out to be OpenAI vectors written while Ollama was down. A guess
        here strands the majority of a user's memory, so an unreachable backend returns
        None (stay unstamped, decide later) rather than defaulting to the other one.
        """
        import random

        # Spread the sample over the whole table: rows come back in insertion order, and
        # the backend that wrote the oldest rows is exactly the one likely to have been
        # replaced since. A first-N sample read this store as 100% nomic when two thirds
        # of it is OpenAI.
        pool = [
            r for r in self.table.search().limit(500).to_list()
            if r.get("content") and r.get("vector") is not None
        ]
        if not pool:
            return None
        rows = random.sample(pool, min(sample_size, len(pool)))

        best, best_matches = None, 0
        for backend in ("openai", "ollama"):
            matches, reachable = 0, True
            for r in rows:
                try:
                    fresh = np.array(_embed(r["content"], backend=backend), dtype=np.float32)
                except Exception:
                    reachable = False
                    break  # no verdict available from this backend right now
                if float(np.linalg.norm(np.array(r["vector"], dtype=np.float32) - fresh)) < 0.35:
                    matches += 1
            if reachable and matches > best_matches:
                best, best_matches = backend, matches
        return best

    def _write_embed_backend(self, backend: str) -> None:
        try:
            self._stamp_path.write_text(backend + "\n")
        except OSError as e:
            log.warning("could not record embed backend for this store: %s", e)

    def _init_table(self):
        if "knowledge" not in self.db.list_tables().tables:
            self.db.create_table("knowledge", schema=SCHEMA)
        else:
            tbl = self.db.open_table("knowledge")
            existing_fields = {f.name for f in tbl.schema}
            expected_fields = {f.name for f in SCHEMA}
            if existing_fields != expected_fields:
                # Schema mismatch — drop and recreate with current schema.
                # Any existing rows are lost; this only happens once on upgrade.
                self.db.drop_table("knowledge")
                self.db.create_table("knowledge", schema=SCHEMA)
        self.table = self.db.open_table("knowledge")

    # ── Write ──────────────────────────────────────────────────────────────

    def store(
        self,
        content: str,
        source: str,
        query: str = "",
        url: str = "",
        page: int = 0,
        lang: str = "",
        method: str = "direct",
        enriched: bool = False,
        auto_chunk: bool = False,
    ) -> list[str]:
        """
        Embed and store content. Returns list of stored chunk IDs.
        Set auto_chunk=True to split long content via paragraph-aware chunker.
        """
        from store.chunker import chunk_text
        chunks = chunk_text(content) if auto_chunk and len(content) > 500 else [content]

        ids = []
        for chunk in chunks:
            cid = _content_id(chunk)
            # Raises EmbedBackendUnavailable rather than writing a vector from the
            # other backend into this store — one mixed row is unsearchable forever,
            # while a refused write is just a write to retry when the backend is back.
            vector = _embed(chunk, backend=self.embed_backend)
            if self.embed_backend is None:
                self.embed_backend = EMBED_BACKEND
                self._write_embed_backend(EMBED_BACKEND)
            self.table.add([{
                "id":         cid,
                "source":     source,
                "query":      query,
                "content":    chunk,
                "url":        url,
                "page":       page,
                "lang":       lang,
                "method":     method,
                "enriched":   enriched,
                "created_at": time.time(),
                "vector":     vector,
            }])
            ids.append(cid)
        return ids

    # ── Read ───────────────────────────────────────────────────────────────

    def search(
        self,
        query: str,
        top_k: int = 5,
        source_filter: Optional[str] = None,
    ) -> list[dict]:
        """
        Semantic search with staleness filtering.
        source_filter applies as a native LanceDB prefilter (not a post-hoc
        Python pass) — restricts the candidate set before vector scoring,
        not after.
        Over-fetches by 4x (lesson: don't under-fetch then run out after stale filter).
        """
        try:
            qvec = _embed(query, backend=self.embed_backend)
        except EmbedBackendUnavailable as e:
            # Nothing in this store is searchable until its backend is back. Empty is
            # the honest answer; the alternative is cross-space distances that look
            # like real matches. Callers already treat [] as "no hit".
            log.warning("search unavailable — %s (store embedded with %s)", e, self.embed_backend)
            return []
        search = self.table.search(qvec)
        if source_filter:
            search = search.where(f"source = '{source_filter}'", prefilter=True)
        candidates = search.limit(top_k * 4).to_list()

        now = time.time()
        fresh = []
        for r in candidates:
            ttl = TTL.get(r["source"], 3600)
            if (now - r["created_at"]) <= ttl:
                fresh.append(r)
            if len(fresh) >= top_k:
                break

        return fresh

    def search_multi(
        self,
        query: str,
        sources: list[str],
        top_k: int = 5,
    ) -> list[dict]:
        """
        Search across multiple source types and merge with RRF.
        Use when you want results from e.g. both tavily_search and ocr_pdf.
        """
        result_lists = [
            self.search(query, top_k=top_k, source_filter=src)
            for src in sources
        ]
        return _rrf(result_lists, top_k=top_k)

    def is_cached(
        self,
        query: str,
        source: str,
        max_distance: float = 0.65,
    ) -> Optional[str]:
        """
        Returns cached content if a fresh, semantically similar result exists.
        Distance 0.65 = same topic, different wording (tuned from live data).
        """
        hits = self.search(query, top_k=1, source_filter=source)
        if not hits:
            return None
        if hits[0].get("_distance", 2.0) < max_distance:
            return hits[0]["content"]
        return None

    # ── Stats ──────────────────────────────────────────────────────────────

    def stats(self) -> dict:
        count = self.table.count_rows()
        if count == 0:
            return {"total": 0, "by_source": {}}
        try:
            df = self.table.to_pandas()
            return {
                "total":     count,
                "by_source": df["source"].value_counts().to_dict(),
                "by_method": df["method"].value_counts().to_dict(),
                "by_lang":   df["lang"].value_counts().to_dict(),
            }
        except ImportError:
            # pylance not installed — fall back to arrow (no lance dependency)
            tbl = self.table.to_arrow()
            from collections import Counter
            src = Counter(tbl.column("source").to_pylist())
            meth = Counter(tbl.column("method").to_pylist())
            lang = Counter(tbl.column("lang").to_pylist())
            return {
                "total":     count,
                "by_source": dict(src),
                "by_method": dict(meth),
                "by_lang":   dict(lang),
            }

    def validate(self, known_pairs: list[tuple[str, str, bool]] = None) -> dict:
        """
        Sanity-check the KB.
        known_pairs: [(query_a, query_b, should_be_similar), ...]
        Similar pairs should have distance < 0.65, dissimilar >= 0.65.
        Ported from malaria_thesis/vector_search.py validate().
        """
        results = {"stats": self.stats(), "similarity_checks": []}

        if known_pairs:
            for q_a, q_b, expected_similar in known_pairs:
                va = _embed(q_a)
                vb = _embed(q_b)
                # Cosine distance from normalized vectors
                dist = float(1.0 - np.dot(va, vb))
                is_similar = dist < 0.65
                passed = is_similar == expected_similar
                results["similarity_checks"].append({
                    "a": q_a[:50],
                    "b": q_b[:50],
                    "distance": round(dist, 4),
                    "expected_similar": expected_similar,
                    "passed": passed,
                })

        return results
