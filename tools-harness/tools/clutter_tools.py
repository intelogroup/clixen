"""
Clutter Fixer — guided chat tools for cleaning up messy directories.

Three tools for a multi-turn conversation:
  1. analyze_directory(path)  → full inventory + duplicate detection + topic hints
  2. suggest_organization(path, scheme) → dry-run preview of new file layout
  3. apply_organization(mapping, delete_paths) → execute the plan

Design: the agent walks the user through: analyze → choose scheme → preview → confirm → execute.
No fire-and-forget — the user makes decisions at each step.
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import re
import shutil
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path

log = logging.getLogger("clutter_tools")

# File size display thresholds
_KB = 1024
_MB = 1024 * _MB if False else 1024 * 1024  # fix
_MB = 1024 * 1024
_GB = 1024 * 1024 * 1024

# Junk/trash files that are candidates for deletion
_JUNK_EXTENSIONS: set[str] = {
    ".dmg", ".pkg", ".nupkg", ".deb", ".rpm",
    ".torrent", ".crdownload", ".part",
    ".tmp", ".temp", ".cache", ".log",
    ".DS_Store", "Thumbs.db",
}

# Common topic keywords extracted from filenames
_TOPIC_PATTERNS: dict[str, re.Pattern] = {
    "tax": re.compile(r"\b(tax|irs|w-?2|w2|1099|deduction|filing|refund)\b", re.I),
    "invoice": re.compile(r"\b(invoice|receipt|bill|payment|statement)\b", re.I),
    "resume": re.compile(r"\b(resume|cv|cover.?letter|job|career)\b", re.I),
    "photo": re.compile(r"\b(photo|image|pic|screenshot|capture|snap)\b", re.I),
    "contract": re.compile(r"\b(contract|agreement|nda|terms|legal|sign)\b", re.I),
    "bank": re.compile(r"\b(bank|statement|account|transaction|balance)\b", re.I),
    "insurance": re.compile(r"\b(insurance|policy|claim|coverage|premium)\b", re.I),
    "medical": re.compile(r"\b(medical|health|doctor|hospital|prescription|lab)\b", re.I),
    "school": re.compile(r"\b(school|college|university|course|class|assignment|homework|grade)\b", re.I),
    "subscription": re.compile(r"\b(subscription|renewal|membership|plan)\b", re.I),
    "code": re.compile(r"\b(code|source|script|\.py|\.js|\.ts|\.go|\.rs|\.cpp|\.java|\.rb)\b", re.I),
    "pdf": re.compile(r"\.pdf$", re.I),
    "spreadsheet": re.compile(r"\.(xlsx|xls|csv|numbers)$", re.I),
    "document": re.compile(r"\.(docx?|pages|rtf|odt)$", re.I),
    "presentation": re.compile(r"\.(pptx?|key|odp)$", re.I),
    "archive": re.compile(r"\.(zip|tar|gz|7z|rar|bz2|xz)$", re.I),
    "video": re.compile(r"\.(mp4|mov|avi|mkv|webm|flv)$", re.I),
    "audio": re.compile(r"\.(mp3|wav|aac|flac|ogg|m4a)$", re.I),
    "installer": re.compile(r"\.(dmg|pkg|exe|msi|appimage)$", re.I),
}


def _fmt_size(size_bytes: int) -> str:
    if size_bytes >= _GB:
        return f"{size_bytes / _GB:.1f} GB"
    if size_bytes >= _MB:
        return f"{size_bytes / _MB:.1f} MB"
    if size_bytes >= _KB:
        return f"{size_bytes / _KB:.1f} KB"
    return f"{size_bytes} B"


def _file_hash(path: Path, quick: bool = True) -> str:
    """Hash a file for duplicate detection. quick=True uses size + first 4KB."""
    try:
        size = path.stat().st_size
        if quick:
            if size < 8192:
                return hashlib.md5(path.read_bytes()).hexdigest()
            with open(path, "rb") as f:
                head = f.read(4096)
                f.seek(-4096, 2)
                tail = f.read(4096)
            return hashlib.md5(head + str(size).encode() + tail).hexdigest()
        else:
            return hashlib.md5(path.read_bytes()).hexdigest()
    except (OSError, PermissionError):
        return ""


def _detect_topic(filename: str) -> list[str]:
    """Detect topic hints from a filename."""
    topics = []
    for topic, pattern in _TOPIC_PATTERNS.items():
        if pattern.search(filename):
            topics.append(topic)
    return topics or ["other"]


def _month_label(ts: float) -> str:
    dt = datetime.fromtimestamp(ts, tz=timezone.utc)
    return dt.strftime("%Y-%m")


def _date_label(ts: float) -> str:
    dt = datetime.fromtimestamp(ts, tz=timezone.utc)
    return dt.strftime("%Y-%m-%d")


# ── Schemas ──────────────────────────────────────────────────────────────────

ANALYZE_SCHEMA = {
    "type": "function",
    "function": {
        "name": "analyze_directory",
        "description": (
            "Full inventory of a directory: file count by extension, by size, by month, "
            "oldest files, largest files, detected topics, and DUPLICATE candidates. "
            "Returns structured data for the agent to present to the user. "
            "Use this first before suggesting any reorganization."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "path": {
                    "type": "string",
                    "description": "Absolute path to the directory to analyze (e.g. ~/Downloads, ~/Desktop)",
                },
                "max_files": {
                    "type": "integer",
                    "description": "Max files to scan (default 2000, cap 10000 to avoid memory issues)",
                    "default": 2000,
                },
            },
            "required": ["path"],
        },
    },
}

SUGGEST_SCHEMA = {
    "type": "function",
    "function": {
        "name": "suggest_organization",
        "description": (
            "Generate a dry-run preview of how files would be organized under a scheme. "
            "Returns a mapping of current_path → new_path without moving anything. "
            "Use this AFTER analyze_directory to let the user preview before committing. "
            "Also returns stats: how many files, how many folders would be created, and "
            "which files are junk candidates (DMG, pkg, tmp, etc.)."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "path": {
                    "type": "string",
                    "description": "Absolute path to the directory to organize",
                },
                "scheme": {
                    "type": "string",
                    "enum": ["monthly", "by_topic", "topic_monthly", "topic_descriptive"],
                    "description": (
                        "Organization scheme:\n"
                        "- monthly: files grouped by YYYY-MM folder\n"
                        "- by_topic: files grouped by detected topic (tax, invoice, photo, etc.)\n"
                        "- topic_monthly: topic/ → month-year/ subfolders\n"
                        "- topic_descriptive: topic/ → descriptive_subcat/ with dated filenames"
                    ),
                },
            },
            "required": ["path", "scheme"],
        },
    },
}

APPLY_SCHEMA = {
    "type": "function",
    "function": {
        "name": "apply_organization",
        "description": (
            "Execute the organization plan: move files to their new paths, "
            "create folders, delete specified paths. NEVER call this without the user's "
            "explicit confirmation. Always run suggest_organization first and present "
            "the preview to the user before applying."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "path": {
                    "type": "string",
                    "description": "Base directory being organized",
                },
                "scheme": {
                    "type": "string",
                    "description": "Same scheme passed to suggest_organization",
                },
                "delete_paths": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "List of absolute file paths to delete (junk files, duplicates the user chose to remove)",
                },
            },
            "required": ["path", "scheme"],
        },
    },
}

SCHEMAS = [ANALYZE_SCHEMA, SUGGEST_SCHEMA, APPLY_SCHEMA]


# ── Executors ─────────────────────────────────────────────────────────────────


def analyze_directory(args: dict) -> str:
    path = Path(args["path"]).expanduser().resolve()
    max_files = min(args.get("max_files", 2000), 10000)

    if not path.exists():
        return f"[clutter] directory not found: {path}"
    if not path.is_dir():
        return f"[clutter] not a directory: {path}"

    try:
        all_files = []
        total_size = 0
        ext_counts: dict[str, int] = defaultdict(int)
        ext_sizes: dict[str, int] = defaultdict(int)
        month_counts: dict[str, int] = defaultdict(int)
        month_sizes: dict[str, int] = defaultdict(int)
        topics: dict[str, int] = defaultdict(int)
        hash_map: dict[str, list[Path]] = defaultdict(list)

        for entry in path.rglob("*"):
            if not entry.is_file():
                continue
            if len(all_files) >= max_files:
                break

            try:
                stat = entry.stat()
                size = stat.st_size
                mtime = stat.st_mtime
            except OSError:
                continue

            ext = entry.suffix.lower() or "(no ext)"
            ext_counts[ext] += 1
            ext_sizes[ext] += size
            month_counts[_month_label(mtime)] += 1
            month_sizes[_month_label(mtime)] += size
            total_size += size

            for t in _detect_topic(entry.name):
                topics[t] += 1

            # Quick hash for duplicate detection
            fhash = _file_hash(entry, quick=True)
            if fhash:
                hash_map[fhash].append(entry)

            all_files.append((entry, size, mtime))

        # Largest files (top 10)
        largest = sorted(all_files, key=lambda x: -x[1])[:10]

        # Oldest files (top 10)
        oldest = sorted(all_files, key=lambda x: x[2])[:10]

        # Duplicate groups (2+ files with same hash)
        dupe_groups = [
            (len(paths), paths[0].name, _fmt_size(paths[0].stat().st_size),
             [str(p.relative_to(path)) for p in paths])
            for paths in hash_map.values() if len(paths) >= 2
        ]
        dupe_groups.sort(key=lambda x: -x[0])  # most duplicates first
        dupe_groups = dupe_groups[:15]  # cap at 15 groups

        # Junk files
        junk_files = []
        for fpath, size, _ in all_files:
            if fpath.suffix.lower() in _JUNK_EXTENSIONS:
                junk_files.append((str(fpath.relative_to(path)), _fmt_size(size)))
        junk_files = junk_files[:20]

        # Build report
        report = [
            f"Directory: {path}",
            f"Total files: {len(all_files)}  Total size: {_fmt_size(total_size)}",
            f"",
            f"By Extension (top 10):",
        ]
        ext_sorted = sorted(ext_counts.items(), key=lambda x: -x[1])[:10]
        for ext, count in ext_sorted:
            report.append(f"  {ext:12s} {count:5d} files  ({_fmt_size(ext_sizes[ext])})")

        report.append(f"\nBy Month (newest first, top 12):")
        month_sorted = sorted(month_counts.items(), reverse=True)[:12]
        for month, count in month_sorted:
            report.append(f"  {month}  {count:5d} files  ({_fmt_size(month_sizes[month])})")

        report.append(f"\nDetected Topics:")
        topic_sorted = sorted(topics.items(), key=lambda x: -x[1])[:12]
        for topic, count in topic_sorted:
            report.append(f"  {topic:15s} {count} files")

        report.append(f"\nLargest Files:")
        for fpath, size, _ in largest:
            report.append(f"  {_fmt_size(size):>8s}  {fpath.relative_to(path)}")

        report.append(f"\nOldest Files:")
        for fpath, _, mtime in oldest:
            dt = datetime.fromtimestamp(mtime)
            report.append(f"  {dt.strftime('%Y-%m-%d')}  {fpath.relative_to(path)}")

        if dupe_groups:
            report.append(f"\nDuplicate Candidates ({len(dupe_groups)} groups):")
            for count, fname, size, paths in dupe_groups[:10]:
                report.append(f"  [{count}x] {fname} ({size})")
                for p in paths[:3]:
                    report.append(f"         {p}")
                if len(paths) > 3:
                    report.append(f"         ... and {len(paths)-3} more")

        if junk_files:
            report.append(f"\nJunk File Candidates ({len(junk_files)}):")
            for fname, size in junk_files[:10]:
                report.append(f"  {size:>8s}  {fname}")

        return "\n".join(report)

    except Exception as e:
        log.error("analyze_directory failed: %s", e, exc_info=True)
        return f"[clutter] analysis failed: {e}"


def suggest_organization(args: dict) -> str:
    path = Path(args["path"]).expanduser().resolve()
    scheme = args["scheme"]

    if not path.exists():
        return f"[clutter] directory not found: {path}"

    try:
        all_files = []
        for entry in path.rglob("*"):
            try:
                if entry.is_file() and not entry.name.startswith("."):
                    all_files.append(entry)
            except (OSError, PermissionError):
                continue
            if len(all_files) > 3000:
                break

        # Build mapping: current_path → new_relative_path
        mapping: list[tuple[str, str]] = []
        folders_needed: set[str] = set()
        topic_primary: dict[str, int] = defaultdict(int)

        for fpath in all_files:
            stat = fpath.stat()
            mtime = stat.st_mtime
            month = _month_label(mtime)
            date = _date_label(mtime)
            topics = _detect_topic(fpath.name)
            primary_topic = topics[0] if topics else "other"
            topic_primary[primary_topic] += 1
            stem = fpath.stem
            ext = fpath.suffix

            if scheme == "monthly":
                new_path = f"{month}/{fpath.name}"
            elif scheme == "by_topic":
                new_path = f"{primary_topic}/{fpath.name}"
            elif scheme == "topic_monthly":
                new_path = f"{primary_topic}/{month}/{fpath.name}"
            elif scheme == "topic_descriptive":
                sub = primary_topic.replace(" ", "_")
                new_name = f"{stem}_{date}{ext}"
                new_path = f"{sub}/{new_name}"
            else:
                return f"[clutter] unknown scheme: {scheme}"

            if new_path != str(fpath.relative_to(path)):
                mapping.append((str(fpath.relative_to(path)), new_path))
                folders_needed.add(str(Path(new_path).parent))

        if not mapping:
            return (
                f"No changes needed for scheme '{scheme}' — all files are already organized.\n"
                f"If you want a different scheme, try: monthly, by_topic, topic_monthly, or topic_descriptive."
            )

        # Build preview
        lines = [
            f"Organization Preview — scheme: {scheme}",
            f"Base directory: {path}",
            f"Files that would move: {len(mapping)}",
            f"Folders that would be created: {len(folders_needed)}",
            f"",
            f"Topic distribution:",
        ]
        for topic, count in sorted(topic_primary.items(), key=lambda x: -x[1]):
            lines.append(f"  {topic:15s} {count} files")

        lines.append(f"\nFolder structure (top 20):")
        for folder in sorted(folders_needed)[:20]:
            count = sum(1 for _, np in mapping if str(Path(np).parent) == folder)
            lines.append(f"  {folder}/  ({count} files)")
        if len(folders_needed) > 20:
            lines.append(f"  ... and {len(folders_needed) - 20} more folders")

        lines.append(f"\nSample moves (first 15):")
        for old, new in mapping[:15]:
            lines.append(f"  {old:50s} → {new}")
        if len(mapping) > 15:
            lines.append(f"  ... and {len(mapping) - 15} more files")

        # Also find junk/duplicates for the user to review
        junk = []
        for fpath, size, _ in [(f, f.stat().st_size, 0) for f in all_files]:
            if fpath.suffix.lower() in _JUNK_EXTENSIONS:
                junk.append((str(fpath.relative_to(path)), _fmt_size(size)))
        if junk:
            lines.append(f"\nJunk files found ({len(junk)} — ask user if they want these deleted):")
            for fname, size in junk[:10]:
                lines.append(f"  {size:>8s}  {fname}")

        return "\n".join(lines)

    except Exception as e:
        log.error("suggest_organization failed: %s", e, exc_info=True)
        return f"[clutter] suggestion failed: {e}"


def apply_organization(args: dict) -> str:
    path = Path(args["path"]).expanduser().resolve()
    scheme = args["scheme"]
    delete_paths = args.get("delete_paths", [])

    if not path.exists():
        return f"[clutter] directory not found: {path}"

    moved = 0
    deleted = 0
    freed_bytes = 0
    errors: list[str] = []

    # Build the same mapping as suggest_organization
    try:
        all_files = []
        for entry in path.rglob("*"):
            try:
                if entry.is_file() and not entry.name.startswith("."):
                    all_files.append(entry)
            except (OSError, PermissionError):
                continue
            if len(all_files) > 3000:
                break

        for fpath in all_files:
            stat = fpath.stat()
            mtime = stat.st_mtime
            month = _month_label(mtime)
            date = _date_label(mtime)
            topics = _detect_topic(fpath.name)
            primary_topic = topics[0] if topics else "other"
            stem = fpath.stem
            ext = fpath.suffix

            if scheme == "monthly":
                new_rel = f"{month}/{fpath.name}"
            elif scheme == "by_topic":
                new_rel = f"{primary_topic}/{fpath.name}"
            elif scheme == "topic_monthly":
                new_rel = f"{primary_topic}/{month}/{fpath.name}"
            elif scheme == "topic_descriptive":
                new_name = f"{stem}_{date}{ext}"
                new_rel = f"{primary_topic.replace(' ', '_')}/{new_name}"
            else:
                return f"[clutter] unknown scheme: {scheme}"

            if new_rel == str(fpath.relative_to(path)):
                continue

            dest = path / new_rel
            try:
                dest.parent.mkdir(parents=True, exist_ok=True)
                shutil.move(str(fpath), str(dest))
                moved += 1
            except Exception as e:
                errors.append(f"{fpath.relative_to(path)}: {e}")

        # Delete user-confirmed paths
        for del_path in delete_paths:
            dp = Path(del_path).expanduser().resolve()
            try:
                if dp.exists():
                    sz = dp.stat().st_size if dp.is_file() else 0
                    if dp.is_file():
                        dp.unlink()
                    elif dp.is_dir():
                        shutil.rmtree(dp)
                    freed_bytes += sz
                    deleted += 1
            except Exception as e:
                errors.append(f"delete {del_path}: {e}")

        result = [
            f"Organization applied — scheme: {scheme}",
            f"Files moved: {moved}",
        ]
        if deleted:
            result.append(f"Files/folders deleted: {deleted}")
            result.append(f"Space freed: {_fmt_size(freed_bytes)}")
        if errors:
            result.append(f"Errors: {len(errors)}")
            for e in errors[:5]:
                result.append(f"  - {e}")

        return "\n".join(result)

    except Exception as e:
        log.error("apply_organization failed: %s", e, exc_info=True)
        return f"[clutter] apply failed: {e}"
