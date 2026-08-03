"""harness.py helpers: local-filesystem action parsing (regex-based path/verb extraction)."""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

from tools.registry import execute_tool

@dataclass(frozen=True)
class LocalFsAction:
    tool_name: str
    arguments: dict
    is_write: bool = False


_PATH_TOKEN_RE = re.compile(r"(?:^|\s)(~/?[^\s,;]+|/[^\s,;]+|\./[^\s,;]+|\.\./[^\s,;]+|[a-zA-Z0-9._-]{1,255}/[^\s,;]+)")
_TRAILING_WORDS_RE = re.compile(
    r"\s+(?:and\s+)?(?:tell|show|answer|return|list|print|give|summari[sz]e|explain)\b.*$",
    re.I,
)


def _clean_local_path(text: str) -> str:
    value = (text or "").strip()
    value = _TRAILING_WORDS_RE.sub("", value).strip()
    # Remove leading whitespace that might have been caught by the lookbehind group
    value = value.lstrip()
    value = value.lstrip(" \t\r\n'\"`([{")
    value = value.rstrip(" \t\r\n'\"`)]}.,;:?!")
    return value


def _first_path(text: str) -> str | None:
    quoted = re.search(r"['\"`]([^'\"`]+)['\"`]", text)
    if quoted:
        return _clean_local_path(quoted.group(1))
    match = _PATH_TOKEN_RE.search(text)
    if match:
        # Extract only the capture group, which doesn't include the leading whitespace
        return _clean_local_path(match.group(1))
    return None


def _clean_location_phrase(raw: str) -> str | None:
    """Turn a captured location phrase ("my Downloads telegram_qa_sandbox folder") into an
    absolute path. Anchored to the home dir explicitly rather than left relative — write
    tools (rename/move/mkdir) resolve bare relative paths against the process cwd, not home,
    so a relative result here would silently land in the wrong place.
    ponytail: word-join heuristic, not a real parser; breaks on phrasing like "the folder
    called Projects" where a stray noun leads the capture.
    """
    words = [w for w in raw.strip().split() if w.lower() not in ("my", "the", "a", "an")]
    if words and re.fullmatch(r"folders?|directory|directories|dirs?", words[-1], re.I):
        words = words[:-1]
    if not words:
        return None
    joined = words[0] if len(words) == 1 else "/".join(words)
    cleaned = _clean_local_path(joined)
    if not cleaned:
        return None
    if cleaned.startswith("~") or cleaned.startswith("/"):
        return cleaned
    return str(Path.home() / cleaned)


def _path_after_preposition(text: str, prepositions: tuple[str, ...] = ("in", "under", "from", "at")) -> str | None:
    prep = "|".join(re.escape(p) for p in prepositions)
    match = re.search(rf"\b(?:{prep})\s+(.+)$", text, re.I)
    if not match:
        return None
    # Cut at the first clause boundary so trailing chatter isn't treated as part of the path.
    raw = re.split(r"\s+(?:and|to|if|see|whether|for|named|called)\b|[.?!]", match.group(1), maxsplit=1, flags=re.I)[0]
    return _clean_location_phrase(raw)


def _split_two_paths(text: str, verb_re: str) -> tuple[str, str] | None:
    match = re.search(rf"\b(?:{verb_re})\b\s+(?:the\s+)?(?:file|folder|directory)?\s*(.+?)\s+(?:to|as)\s+(.+)$", text, re.I)
    if not match:
        return None
    src_raw, dest_raw = match.group(1), match.group(2)
    # A trailing "in/under/at LOCATION [folder]" clause on the dest side names the shared
    # containing folder for a same-directory rename/move ("X to Y in my Downloads project
    # folder") — it applies to both src and dest, not just whichever word it's adjacent to.
    loc_match = re.search(r"^(.*?)\s+\b(?:in|under|at)\b\s+(.+)$", dest_raw, re.I)
    location = None
    if loc_match:
        dest_raw = loc_match.group(1)
        location = _clean_location_phrase(loc_match.group(2))
    src = _clean_local_path(src_raw)
    dest = _clean_local_path(dest_raw)
    if location:
        if src and "/" not in src and not src.startswith("~"):
            src = str(Path(location) / src)
        if dest and "/" not in dest and not dest.startswith("~"):
            dest = str(Path(location) / dest)
    if src and dest:
        return src, dest
    return None


def _find_glob_pattern(text: str) -> str:
    if re.search(r"\b(markdown|md)\b", text, re.I):
        return "*.md"
    if re.search(r"\bpython\b", text, re.I):
        return "*.py"
    named = re.search(r"\b(?:named|called)\s+[\"'“‘](.+?)[\"'”’]", text)
    if not named:
        named = re.search(r"\b(?:named|called)\s+(\S[^,;]*?)(?=\s+(?:in|under|from|and|to|if|see|verify|check|find|locate|download|google)|$)", text)
    if named:
        name = named.group(1).strip("'\"“”‘’?.!, ")
        return f"*{name}*" if "*" not in name else name
    glob = re.search(r"([*?][^\s,;]*|[^\s,;]*[*?][^\s,;]*)", text)
    return glob.group(1) if glob else "*"


def parse_local_fs_action(query: str) -> LocalFsAction | None:
    """Map clear local-agent filesystem requests to deterministic tool calls."""
    text = query.strip()
    lower = text.lower()
    if not text:
        return None

    # Destructive/mutating commands first so "delete file X" is not mistaken for a read.
    # "create a (text) file ... folder" names a FILE as the object — the "folder" mention is
    # just its destination, not a mkdir request. Only treat this as mkdir when the object
    # noun itself is folder/directory, not file.
    _creates_a_file = re.search(r"\b(?:create|make)\b(?:\s+\w+){0,2}\s+file\b", lower)
    if re.search(r"\b(create|make|mkdir)\b", lower) and re.search(r"\b(folder|directory|dir)\b", lower) and not _creates_a_file:
        called = re.search(r"\bcalled\s+(.+?)\s+\bin\s+(.+)$", text, re.I)
        if called:
            name = _clean_local_path(called.group(1))
            parent = _clean_local_path(called.group(2))
            if name and parent:
                return LocalFsAction("create_directory", {"path": str(Path(parent).expanduser() / name)}, True)
        path = _path_after_preposition(text, ("directory", "folder", "dir", "at")) or _first_path(text)
        if path:
            return LocalFsAction("create_directory", {"path": path}, True)

    if re.search(r"\b(rename|move|mv)\b", lower):
        paths = _split_two_paths(text, r"rename|move|mv")
        if paths:
            return LocalFsAction("rename_file", {"src": paths[0], "dest": paths[1]}, True)

    if re.search(r"\b(copy|cp)\b", lower):
        paths = _split_two_paths(text, r"copy|cp")
        if paths:
            recursive = bool(re.search(r"\b(folder|directory|dir|recursive|all contents)\b", lower))
            return LocalFsAction("copy_file", {"src": paths[0], "dest": paths[1], "recursive": recursive}, True)

    if re.search(r"\b(delete|remove|rm)\b", lower):
        path = _first_path(text) or _path_after_preposition(text, ("file", "folder", "directory", "dir"))
        if path:
            recursive = bool(re.search(r"\b(folder|directory|dir|recursive|all contents)\b", lower))
            return LocalFsAction("delete_file", {"path": path, "recursive": recursive}, True)

    if re.search(r"\b(biggest|largest|large files?|disk usage)\b", lower):
        path = _path_after_preposition(text, ("in", "under", "from", "at")) or str(Path.home())
        n_match = re.search(r"\b(\d+)\s+(?:biggest|largest)\b|\b(?:biggest|largest|top)\s+(\d+)\b", text, re.I)
        n = int(n_match.group(1) or n_match.group(2)) if n_match else 5
        return LocalFsAction("find_largest", {"path": path, "n": n}, False)

    if re.search(r"\b(tree|layout|structure)\b", lower):
        path = _path_after_preposition(text, ("of", "for", "in", "under", "at")) or _first_path(text)
        if path:
            return LocalFsAction("file_tree", {"path": path, "max_depth": 3}, False)

    if re.search(r"\b(info|metadata|size|modified)\b", lower) and _first_path(text):
        return LocalFsAction("file_info", {"path": _first_path(text)}, False)

    if re.search(r"\b(search|grep|find text|look for)\b", lower) and not re.search(r"\b(files?|folders?|directories?)\b", lower):
        pattern_match = re.search(r"\b(?:search|grep|look for)\s+(.+?)\s+(?:in|under|from)\s+(.+)$", text, re.I)
        if pattern_match:
            pattern = pattern_match.group(1).strip(" '\"`")
            path = _clean_local_path(pattern_match.group(2))
            return LocalFsAction("grep_files", {"pattern": pattern, "path": path}, False)

    if re.search(r"\b(find|locate|verify|check)\b", lower) and re.search(r"\b(files?|folders?|directories?|documents?|pdfs?|markdown|python|\*)\b", lower):
        path = _path_after_preposition(text, ("under", "in", "from", "at"))
        if not path:
            path = str(Path.home() / "Downloads") if re.search(r"\b(downloads?)\b", lower) else str(Path.home())
        return LocalFsAction("find_files", {"pattern": _find_glob_pattern(text), "path": path}, False)

    if re.search(r"\b(list|show|what'?s in|what is in|ls)\b", lower) and re.search(r"\b(files?|folders?|directory|dir|contents?)\b", lower):
        path = _path_after_preposition(text, ("in", "of", "under", "at")) or _first_path(text) or str(Path.home())
        return LocalFsAction("list_directory", {"path": path}, False)

    if re.search(r"\b(read|open|cat|show)\b", lower):
        path = _first_path(text)
        if path:
            return LocalFsAction("read_document", {"path": path}, False)

    return None


def execute_local_fs_action(action: LocalFsAction) -> str:
    return execute_tool(action.tool_name, action.arguments)


