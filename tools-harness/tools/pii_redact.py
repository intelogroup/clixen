"""Local PII/PHI detection — spaCy NER + regex, zero network calls.

Mirrors misaki_tokenizer.py's lazy-singleton-loader shape: a module-level
loaded guard, a one-time setup function, heavy imports deferred until first
use. `en_core_web_sm` is already a resolved dependency (pinned via the direct
wheel URL misaki's G2P needs) — this reuses that exact model, no new download.

Default mode is "flag" not "strip": auto-redaction on a lawyer's/doctor's
document is a liability if it silently mangles the wrong span. Callers who
want text mutated must opt in with mode="strip".
"""

import hashlib
import json
import re
from datetime import datetime, timezone
from pathlib import Path

REDACT_DOCUMENT_SCHEMA = {
    "type": "function",
    "function": {
        "name": "redact_document",
        "description": (
            "Redact PII/PHI in a .docx file in place (writes a new file), local-only. "
            "PDF is report-only (mode='flag') — a text-layer replace on a PDF does not "
            "remove the underlying content stream, so PDF 'strip' is refused rather than "
            "producing a document that looks redacted but still leaks the original text. "
            "Every call appends a SHA256 audit record (no PHI) to logs/redaction_audit.jsonl."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "file_path": {"type": "string", "description": ".docx or .pdf path"},
                "mode": {
                    "type": "string",
                    "enum": ["flag", "strip"],
                    "default": "flag",
                    "description": "flag: report entities only. strip: docx only, writes redacted copy.",
                },
                "output_path": {
                    "type": "string",
                    "description": "Where to write the redacted .docx (mode=strip only). Defaults to '<stem>.redacted.docx'.",
                },
            },
            "required": ["file_path"],
        },
    },
}

_AUDIT_LOG = Path(__file__).resolve().parents[1] / "logs" / "redaction_audit.jsonl"


def _sha256(path: Path) -> str:
    h = hashlib.sha256()
    h.update(path.read_bytes())
    return h.hexdigest()


def _audit(**fields) -> None:
    _AUDIT_LOG.parent.mkdir(parents=True, exist_ok=True)
    record = {"ts": datetime.now(timezone.utc).isoformat(), **fields}
    with _AUDIT_LOG.open("a") as f:
        f.write(json.dumps(record) + "\n")


def redact_document(file_path: str, mode: str = "flag", output_path: str | None = None) -> dict:
    """Redact a .docx (strip) or scan a .pdf/.docx (flag) for PII/PHI.

    PDF never strips — see REDACT_DOCUMENT_SCHEMA docstring for why.
    """
    src = Path(file_path).expanduser().resolve()
    if not src.exists():
        return {"error": f"file not found: {src}"}
    suffix = src.suffix.lower()

    if suffix == ".pdf":
        if mode == "strip":
            return {"error": "PDF strip refused — text-layer replace does not remove original content stream. Use mode='flag'."}
        import pdfplumber

        with pdfplumber.open(src) as pdf:
            text = "\n".join(page.extract_text() or "" for page in pdf.pages)
        result = redact_pii(text, mode="flag")
        _audit(file=str(src), mode="flag", suffix=".pdf", input_sha256=_sha256(src),
               entity_count=len(result["entities"]),
               labels=sorted({e["label"] for e in result["entities"]}))
        return result

    if suffix != ".docx":
        return {"error": f"unsupported suffix {suffix}, expected .docx or .pdf"}

    import docx

    doc = docx.Document(src)
    all_entities = []
    for para in doc.paragraphs:
        for run in para.runs:
            if not run.text.strip():
                continue
            result = redact_pii(run.text, mode=mode)
            all_entities.extend(result["entities"])
            if mode == "strip":
                run.text = result["redacted_text"]

    if mode == "flag":
        _audit(file=str(src), mode="flag", suffix=".docx", input_sha256=_sha256(src),
               entity_count=len(all_entities),
               labels=sorted({e["label"] for e in all_entities}))
        return {"entities": all_entities}

    out = Path(output_path).expanduser().resolve() if output_path else src.with_suffix(".redacted.docx")
    if out.suffix.lower() != ".docx":
        return {"error": f"output_path must end in .docx, got {out.suffix}"}
    doc.save(out)
    _audit(file=str(src), mode="strip", suffix=".docx", input_sha256=_sha256(src),
           output_file=str(out), output_sha256=_sha256(out),
           entity_count=len(all_entities),
           labels=sorted({e["label"] for e in all_entities}))
    return {"output_path": str(out), "entity_count": len(all_entities),
            "labels": sorted({e["label"] for e in all_entities})}


REDACT_PII_SCHEMA = {
    "type": "function",
    "function": {
        "name": "redact_pii",
        "description": (
            "Scan text for PII/PHI (names, dates, orgs, locations via local NER, "
            "plus SSN/email/phone via regex) — entirely local, no network calls. "
            "Use mode='flag' to report spans without altering text, or mode='strip' "
            "to replace each span with [REDACTED:LABEL]."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "text": {"type": "string", "description": "Text to scan"},
                "mode": {
                    "type": "string",
                    "enum": ["flag", "strip"],
                    "description": "flag: report only (default). strip: replace spans in returned text.",
                    "default": "flag",
                },
            },
            "required": ["text"],
        },
    },
}

_NLP = None

_REGEX_PATTERNS = {
    "SSN": re.compile(r"\b\d{3}-\d{2}-\d{4}\b"),
    "EMAIL": re.compile(r"\b[\w.+-]+@[\w-]+\.[\w.-]+\b"),
    "PHONE": re.compile(r"\b(?:\+?1[-.\s]?)?\(?\d{3}\)?[-.\s]?\d{3}[-.\s]?\d{4}\b"),
}


def _load_nlp():
    global _NLP
    if _NLP is None:
        import spacy

        _NLP = spacy.load("en_core_web_sm")
    return _NLP


def redact_pii(text: str, mode: str = "flag") -> dict:
    """Detect PERSON/DATE/GPE/ORG entities (spaCy) + SSN/EMAIL/PHONE (regex).

    mode="flag" (default): text returned unchanged, entities reported only.
    mode="strip": entity spans replaced with [REDACTED:LABEL] in the returned text.
    """
    nlp = _load_nlp()
    doc = nlp(text)
    entities = [
        {"text": ent.text, "label": ent.label_, "start": ent.start_char, "end": ent.end_char}
        for ent in doc.ents
        if ent.label_ in ("PERSON", "DATE", "GPE", "ORG")
    ]
    for label, pattern in _REGEX_PATTERNS.items():
        for m in pattern.finditer(text):
            entities.append({"text": m.group(0), "label": label, "start": m.start(), "end": m.end()})

    entities.sort(key=lambda e: e["start"])

    redacted_text = text
    if mode == "strip" and entities:
        out, cursor = [], 0
        for ent in entities:
            if ent["start"] < cursor:
                continue  # overlapping span, keep first
            out.append(text[cursor:ent["start"]])
            out.append(f"[REDACTED:{ent['label']}]")
            cursor = ent["end"]
        out.append(text[cursor:])
        redacted_text = "".join(out)

    return {"redacted_text": redacted_text, "entities": entities}
