"""
Local file upload store.

Bucket layout:  tools-harness/uploads/<category>/<YYYY-MM-DD>/<uuid><ext>
DB:             tools-harness/store/uploads.db

Categories (each has a .bucket.json manifest):
  pdf · documents · spreadsheets · presentations · images · audio
  video · code · data · text · archives · other

Public API
----------
init()                          — create DB + all bucket dirs (idempotent)
bucket_dir(mime, ext)           -> Path  — correct category / today's date dir
save(...)                       -> dict  — persist file metadata
get(file_id)                    -> dict | None
list_for_chat(chat_id)          -> list[dict]
set_extracted(file_id, text)    — cache extracted text
delete(file_id)                 — remove DB row + file from disk
"""

import json
import sqlite3
import os
from store import dbclose
from datetime import date
from pathlib import Path

_HERE = Path(__file__).parent
DB_PATH = _HERE / "uploads.db"
BUCKET_ROOT = _HERE.parent / "uploads"

# ---------------------------------------------------------------------------
# MIME → category routing
# ---------------------------------------------------------------------------

_MIME_MAP: dict[str, str] = {
    "application/pdf": "pdf",
    # documents
    "application/msword": "documents",
    "application/vnd.openxmlformats-officedocument.wordprocessingml.document": "documents",
    "application/vnd.oasis.opendocument.text": "documents",
    "application/rtf": "documents",
    "text/rtf": "documents",
    # spreadsheets
    "application/vnd.ms-excel": "spreadsheets",
    "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet": "spreadsheets",
    "application/vnd.oasis.opendocument.spreadsheet": "spreadsheets",
    "text/csv": "spreadsheets",
    "text/tab-separated-values": "spreadsheets",
    # presentations
    "application/vnd.ms-powerpoint": "presentations",
    "application/vnd.openxmlformats-officedocument.presentationml.presentation": "presentations",
    "application/vnd.oasis.opendocument.presentation": "presentations",
    # data
    "application/json": "data",
    "application/xml": "data",
    "text/xml": "data",
    "application/x-yaml": "data",
    "text/yaml": "data",
    "application/toml": "data",
    # text
    "text/plain": "text",
    "text/markdown": "text",
    "text/x-markdown": "text",
    # archives
    "application/zip": "archives",
    "application/x-zip-compressed": "archives",
    "application/x-tar": "archives",
    "application/gzip": "archives",
    "application/x-gzip": "archives",
    "application/x-bzip2": "archives",
    "application/x-7z-compressed": "archives",
    "application/x-rar-compressed": "archives",
    "application/vnd.rar": "archives",
    # code (explicit MIME types)
    "text/x-python": "code",
    "application/x-python-code": "code",
    "text/javascript": "code",
    "application/javascript": "code",
    "text/typescript": "code",
    "text/x-go": "code",
    "text/x-rustsrc": "code",
    "text/x-c": "code",
    "text/x-c++src": "code",
    "text/x-java-source": "code",
    "text/x-ruby": "code",
    "text/x-sh": "code",
    "application/x-sh": "code",
    "text/x-sql": "code",
}

_EXT_MAP: dict[str, str] = {
    # pdf
    ".pdf": "pdf",
    # documents
    ".doc": "documents", ".docx": "documents", ".odt": "documents", ".rtf": "documents",
    # spreadsheets
    ".xls": "spreadsheets", ".xlsx": "spreadsheets", ".ods": "spreadsheets",
    ".csv": "spreadsheets", ".tsv": "spreadsheets",
    # presentations
    ".ppt": "presentations", ".pptx": "presentations", ".odp": "presentations",
    # images
    ".png": "images", ".jpg": "images", ".jpeg": "images", ".gif": "images",
    ".webp": "images", ".svg": "images", ".bmp": "images", ".ico": "images",
    ".tiff": "images", ".tif": "images", ".heic": "images", ".heif": "images", ".avif": "images",
    # audio
    ".mp3": "audio", ".wav": "audio", ".ogg": "audio", ".flac": "audio",
    ".m4a": "audio", ".aac": "audio", ".opus": "audio", ".weba": "audio",
    # video
    ".mp4": "video", ".mov": "video", ".avi": "video", ".mkv": "video",
    ".webm": "video", ".m4v": "video", ".wmv": "video",
    # code
    ".py": "code", ".js": "code", ".ts": "code", ".jsx": "code", ".tsx": "code",
    ".go": "code", ".rs": "code", ".c": "code", ".cpp": "code", ".h": "code",
    ".java": "code", ".rb": "code", ".php": "code", ".swift": "code", ".kt": "code",
    ".sh": "code", ".bash": "code", ".zsh": "code", ".fish": "code",
    ".sql": "code", ".r": "code", ".jl": "code", ".lua": "code",
    ".tf": "code", ".vim": "code",
    # data
    ".json": "data", ".xml": "data", ".yaml": "data", ".yml": "data",
    ".toml": "data", ".ndjson": "data", ".jsonl": "data",
    # text
    ".txt": "text", ".md": "text", ".mdx": "text",
    ".log": "text", ".env": "text", ".ini": "text", ".cfg": "text",
    # archives
    ".zip": "archives", ".tar": "archives", ".gz": "archives", ".tgz": "archives",
    ".bz2": "archives", ".7z": "archives", ".rar": "archives",
}


def _category(mime: str, ext: str) -> str:
    """Return the bucket category for a given mime type + file extension."""
    # Exact MIME match first
    cat = _MIME_MAP.get(mime)
    if cat:
        return cat
    # MIME prefix fallback (image/*, audio/*, video/*)
    prefix = mime.split("/")[0]
    if prefix in ("image", "audio", "video"):
        return prefix  # 'images', 'audio', 'video' — adjust plural
    if prefix == "image":
        return "images"
    # Extension fallback
    cat = _EXT_MAP.get(ext.lower())
    if cat:
        return cat
    return "other"


# ---------------------------------------------------------------------------
# DB schema
# ---------------------------------------------------------------------------

_CREATE = """
CREATE TABLE IF NOT EXISTS files (
    id            TEXT PRIMARY KEY,
    original_name TEXT NOT NULL,
    stored_path   TEXT NOT NULL,
    mime_type     TEXT NOT NULL,
    size_bytes    INTEGER NOT NULL,
    bucket        TEXT NOT NULL DEFAULT 'other',
    chat_id       TEXT,
    extracted_text TEXT,
    created_at    TEXT DEFAULT (datetime('now'))
)
"""

_MIGRATE_BUCKET_COL = """
ALTER TABLE files ADD COLUMN bucket TEXT NOT NULL DEFAULT 'other'
"""


def _conn() -> sqlite3.Connection:
    conn = dbclose.connect(str(DB_PATH))
    conn.row_factory = sqlite3.Row
    return conn


def init() -> None:
    """Create all bucket directories and DB table (idempotent)."""
    buckets = [
        "pdf", "documents", "spreadsheets", "presentations",
        "images", "audio", "video", "code", "data", "text", "archives", "other",
    ]
    for b in buckets:
        (BUCKET_ROOT / b).mkdir(parents=True, exist_ok=True)

    with _conn() as c:
        c.execute(_CREATE)
        # Add 'bucket' column to existing DBs that predate this change
        try:
            c.execute(_MIGRATE_BUCKET_COL)
        except Exception:
            pass  # column already exists


def bucket_dir(mime: str = "", ext: str = "") -> Path:
    """
    Return the upload directory for the given MIME type + extension,
    creating <category>/<YYYY-MM-DD>/ if needed.
    """
    cat = _category(mime, ext)
    # Normalise image/* prefix → 'images'
    if cat == "image":
        cat = "images"
    d = BUCKET_ROOT / cat / date.today().isoformat()
    d.mkdir(parents=True, exist_ok=True)
    return d


def save(
    file_id: str,
    original_name: str,
    stored_path: str,
    mime_type: str,
    size_bytes: int,
    chat_id: str = None,
    bucket: str = "other",
) -> dict:
    with _conn() as c:
        c.execute(
            "INSERT INTO files(id,original_name,stored_path,mime_type,size_bytes,chat_id,bucket)"
            " VALUES(?,?,?,?,?,?,?)",
            (file_id, original_name, stored_path, mime_type, size_bytes, chat_id, bucket),
        )
    return get(file_id)


def get(file_id: str) -> dict | None:
    with _conn() as c:
        row = c.execute("SELECT * FROM files WHERE id=?", (file_id,)).fetchone()
        return dict(row) if row else None


def list_for_chat(chat_id: str) -> list[dict]:
    with _conn() as c:
        rows = c.execute(
            "SELECT id,original_name,mime_type,size_bytes,bucket,created_at"
            " FROM files WHERE chat_id=? ORDER BY created_at DESC",
            (chat_id,),
        ).fetchall()
        return [dict(r) for r in rows]


def list_by_bucket(bucket: str) -> list[dict]:
    """Return all files in a given bucket category."""
    with _conn() as c:
        rows = c.execute(
            "SELECT id,original_name,mime_type,size_bytes,chat_id,created_at"
            " FROM files WHERE bucket=? ORDER BY created_at DESC",
            (bucket,),
        ).fetchall()
        return [dict(r) for r in rows]


def set_extracted(file_id: str, text: str) -> None:
    with _conn() as c:
        c.execute(
            "UPDATE files SET extracted_text=? WHERE id=?", (text, file_id)
        )


def delete(file_id: str) -> None:
    rec = get(file_id)
    if not rec:
        return
    try:
        Path(rec["stored_path"]).unlink(missing_ok=True)
    except Exception:
        pass
    with _conn() as c:
        c.execute("DELETE FROM files WHERE id=?", (file_id,))
