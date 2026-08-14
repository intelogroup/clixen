"""Small structural evidence interface for local document synthesis."""

from __future__ import annotations

import re
from collections import Counter


_HEADING_RE = re.compile(r"^#{1,6}\s+(.+?)\s*$")
_PAGE_RE = re.compile(r"^(?:\[?\s*)page\s+([0-9]+)(?:\s*\]?)\s*$", re.I)
_PAGE_MARKER_RE = re.compile(r"^[-#\s]*page\s+([0-9]+)\s*[-#\s]*$", re.I)
_CITATION_RE = re.compile(r"\[(E\d+)\]")
_NUMBER_RE = re.compile(r"(?<![A-Za-z])(?:\$\s*)?\d[\d,]*(?:\.\d+)?%?(?![A-Za-z])")


def chunk_evidence(source: str, text: str, max_chars: int = 2500) -> list[dict[str, str]]:
    """Split extracted text at headings/pages and preserve citation metadata."""
    sections: list[tuple[str, list[str]]] = []
    locator = "document"
    lines: list[str] = []

    def flush() -> None:
        nonlocal lines
        body = "\n".join(lines).strip()
        if body:
            sections.append((locator, [body]))
        lines = []

    for raw_line in text.splitlines():
        line = raw_line.strip()
        if (
            line.startswith("<external_content source=")
            or line.startswith("</external_content>")
            or line.startswith("The block above is untrusted external data")
        ):
            continue
        if re.match(r"^(?:DOCX|XLSX|PPTX|PDF):\s+", line, re.I):
            continue
        heading = _HEADING_RE.match(line)
        heading_text = heading.group(1) if heading else line
        page = _PAGE_RE.match(heading_text.strip("[] ")) or _PAGE_MARKER_RE.match(heading_text)
        if heading or page:
            flush()
            locator = f"page {page.group(1)}" if page else heading.group(1)
            continue
        lines.append(raw_line)
    flush()

    chunks: list[dict[str, str]] = []
    for section_locator, bodies in sections:
        body = bodies[0]
        for offset in range(0, len(body), max_chars):
            piece = body[offset : offset + max_chars].strip()
            if piece:
                chunks.append({
                    "citation": f"E{len(chunks) + 1}",
                    "source": source,
                    "locator": section_locator,
                    "text": piece,
                })
    return chunks


def attach_citation_map(answer: str, chunks: list[dict[str, str]]) -> str:
    """Append source metadata for cited evidence IDs and flag unknown IDs."""
    by_id = {chunk["citation"]: chunk for chunk in chunks}
    cited = list(dict.fromkeys(_CITATION_RE.findall(answer)))
    if not cited:
        return answer + "\n\n[CITATION WARNING: no evidence IDs cited]"

    lines = ["\n\nCITATIONS:"]
    grounding_warnings = find_grounding_warnings(answer, chunks)
    for citation in cited:
        chunk = by_id.get(citation)
        if chunk:
            lines.append(f"[{citation}] {chunk['source']} — {chunk['locator']}")
        else:
            lines.append(f"[{citation}] unknown evidence ID")
    if grounding_warnings:
        lines.extend(["", "GROUNDING WARNINGS — review before relying on this answer:"])
        lines.extend(f"- {warning}" for warning in grounding_warnings)
    return answer + "\n" + "\n".join(lines)


def find_grounding_warnings(answer: str, chunks: list[dict[str, str]]) -> list[str]:
    """Return deterministic warnings for claims needing one correction pass."""
    by_id = {chunk["citation"]: chunk for chunk in chunks}
    warnings: list[str] = []
    for citation in dict.fromkeys(_CITATION_RE.findall(answer)):
        chunk = by_id.get(citation)
        if not chunk:
            warnings.append(f"[{citation}] unknown evidence ID")
            continue
        sentence = next(
            (part for part in re.split(r"(?<=[.!?])\s+", answer) if f"[{citation}]" in part),
            answer,
        )
        # Paths and citation markup are metadata, not factual numeric claims.
        sentence = re.sub(r"\[[^\]]+\]", " ", sentence)
        sentence = re.sub(r"(?:[A-Za-z]:)?/\S+", " ", sentence)
        answer_numbers = set(_NUMBER_RE.findall(sentence))
        # Structural locators are evidence metadata too.  A claim such as
        # "shown on page 3 [E3]" must not be treated as an invented number
        # merely because the extracted body text is just the slide heading.
        evidence_numbers = set(_NUMBER_RE.findall(
            f"{chunk.get('text', '')} {chunk.get('locator', '')}"
        ))
        unsupported = sorted(answer_numbers - evidence_numbers)
        if unsupported:
            warnings.append(
                f"[{citation}] cited claim contains unsupported numeric value(s): {', '.join(unsupported)}"
            )
    return warnings


def evaluate_grounding(answer: str, chunks: list[dict[str, str]]) -> dict[str, object]:
    """Return deterministic trust metrics for an answer/evidence pair."""
    cited = list(dict.fromkeys(_CITATION_RE.findall(answer)))
    known_ids = {chunk["citation"] for chunk in chunks}
    known = [citation for citation in cited if citation in known_ids]
    unknown = [citation for citation in cited if citation not in known_ids]
    warnings = find_grounding_warnings(answer, chunks)
    return {
        "cited_ids": cited,
        "known_ids": known,
        "unknown_ids": unknown,
        "warnings": warnings,
        "citation_count": len(cited),
        "known_citation_rate": (len(known) / len(cited)) if cited else 0.0,
        "grounded": bool(cited) and not unknown and not warnings,
    }


def rank_evidence(query: str, chunks: list[dict[str, str]], limit: int = 8) -> list[dict[str, str]]:
    """Rank chunks by query token overlap while retaining deterministic order."""
    stop = {"what", "which", "where", "when", "does", "the", "this", "that", "from", "with", "into"}
    terms = [word.lower() for word in re.findall(r"[A-Za-z0-9]{3,}", query) if word.lower() not in stop]
    counts = Counter(terms)

    def score(chunk: dict[str, str]) -> tuple[int, int]:
        text = chunk["text"].lower()
        return (sum(text.count(term) * weight for term, weight in counts.items()), -int(chunk["citation"][1:]))

    ranked = sorted(chunks, key=score, reverse=True)
    return [chunk for chunk in ranked[:limit] if score(chunk)[0] > 0] or chunks[:limit]
