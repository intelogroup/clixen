"""Deterministic document preparation plus one-model synthesis.

Document work does not use the generic ReAct graph. File discovery and extraction
are predictable operations; Gemma is reserved for the final interpretation.
"""

from __future__ import annotations

import re
import json
from pathlib import Path

from clients.cloud_client import DEFAULT_CLOUD_MODEL
from tools.registry import execute_tool, is_error_result
from tools.confirmation import request_confirmation
from agents.document_evidence import attach_citation_map, chunk_evidence, rank_evidence, find_grounding_warnings

from agents.local_agent_nodes import _chat


DOCUMENT_OPERATIONS = {
    "document_retrieve",
    "read_document",
    "fulltext_search",
    "semantic_file_search",
    "write_file",
    "edit_file",
    "quarantine_document",
    "forget_document_index",
    "delete_document",
}

_DOCUMENT_SUFFIXES = {
    ".pdf", ".docx", ".xlsx", ".pptx", ".rtf", ".html", ".htm",
    ".eml", ".msg", ".txt", ".md", ".csv", ".json", ".yaml", ".yml",
    ".png", ".jpg", ".jpeg", ".tif", ".tiff", ".bmp", ".webp", ".zip",
}

_ACADEMIC_QUERY_RE = re.compile(
    r"\b(?:paper|papers|research|literature|journal|study|studies|academic|scientific|"
    r"crossref|openalex|arxiv|pubmed|medline|scholar|doi|pmid|preprint|"
    r"wikipedia|wikidata|encyclopedia|company filing|sec filing|10-k|10-q|"
    r"opencorporates|corporate registry|news|gdelt)\b",
    re.I,
)

_WEB_QUERY_RE = re.compile(
    r"\b(?:search the web|search online|look online|look up|internet|web search|"
    r"latest|current|today|tonight|recent|news|price|weather|forecast|score|"
    r"schedule|live)\b",
    re.I,
)
_MIXED_WEB_RE = re.compile(
    r"\b(?:compare|contrast|against|versus|vs\.?|alongside)\b.*\b(?:current|latest|recent|today|web|online|news)\b|"
    r"\b(?:current|latest|recent|today|web|online|news)\b.*\b(?:compare|contrast|against|versus|vs\.?)\b",
    re.I,
)


def _numeric_refusal_hint(query: str, answer: str, evidence: str) -> str | None:
    """Find a simple evidence-backed calculation hidden behind a refusal."""
    if not re.search(r"\b(?:how much|what is|calculate|number|amount|revenue|total)\b", query, re.I):
        return None
    if not re.search(r"\b(?:not established|cannot determine|can't determine|unknown|not available)\b", answer, re.I):
        return None
    growth = re.search(
        r"(?:grew|increased|rose)\s+by\s+(?:exactly\s+)?(\d+(?:\.\d+)?)%\s+over\s+(?:Q2|quarter 2)",
        evidence, re.I,
    )
    base = re.search(
        r"(?:Q2|quarter 2)\s+(?:revenue|value|amount)\s*(?:is|=|:)\s*\$?([\d,]+)",
        evidence, re.I,
    ) or re.search(r"\|\s*(?:Q2|quarter 2)\s*\|\s*\$?([\d,]+)", evidence, re.I)
    if not growth or not base:
        return None
    percent, base_value = growth.group(1), int(base.group(1).replace(",", ""))
    result = base_value * (1 + float(percent) / 100)
    rendered = str(int(result)) if result.is_integer() else str(result)
    return (
        f"The evidence determines the answer: Q2 is {base_value} and Q3 grew by {percent}%. "
        f"Calculate {base_value} * (1 + {percent}/100) = {rendered}; cite the relevant evidence."
    )


def _research_source(query: str) -> str | None:
    lowered = query.lower()
    if re.search(r"\b(?:wikipedia|encyclopedia)\b", lowered):
        return "search_wikipedia"
    if "wikidata" in lowered:
        return "search_wikidata"
    if re.search(r"\b(?:sec filing|company filing|10-k|10-q|edgar)\b", lowered):
        return "search_sec_edgar"
    if re.search(r"\b(?:opencorporates|corporate registry)\b", lowered):
        return "search_opencorporates"
    if re.search(r"\b(?:news|gdelt|recent headlines)\b", lowered):
        return "search_gdelt_news"
    if re.search(r"\b(?:pubmed|medline|pmid|clinical|disease|drug|medical|biomedical)\b", lowered):
        return "search_pubmed"
    if re.search(r"\b(?:arxiv|preprint|computer science|physics|math|machine learning)\b", lowered):
        return "search_arxiv"
    if "openalex" in lowered or "scholar" in lowered:
        return "search_openalex"
    return "search_crossref"


def _prepare_research_evidence(query: str) -> tuple[str, list[dict[str, str]]]:
    """Fetch one bounded academic result set before the single Gemma call."""
    source = _research_source(query)
    if not source:
        return "", []
    if source in {"search_crossref", "search_openalex", "search_wikidata", "search_sec_edgar"}:
        args = {"query": query, "limit": 6}
    elif source in {"search_wikipedia", "search_gdelt_news"}:
        args = {"query": query, "limit": 6}
    elif source == "search_opencorporates":
        args = {"query": query}
    else:
        args = {"query": query, "max_results": 6}
    raw = execute_tool(source, args)
    content = str(raw)
    if not content.strip() or is_error_result(content):
        return f"[ACADEMIC SOURCE: {source}]\n{content}", []
    chunks = chunk_evidence(source, content, max_chars=2200)
    for index, chunk in enumerate(chunks, 1):
        chunk["citation"] = f"E{index}"
        chunk["locator"] = chunk.get("locator") or "metadata result"
    blocks = [
        f"[{chunk['citation']} | SOURCE: {chunk['source']} | LOCATOR: {chunk['locator']}]\n{chunk['text']}"
        for chunk in chunks
    ]
    return "\n\n".join(blocks), chunks


def _prepare_web_evidence(query: str) -> tuple[str, list[dict[str, str]]]:
    """Use the cloud web-search connector once for an explicitly web-shaped request."""
    raw = execute_tool("ask_web_search", {"query": query})
    content = str(raw)
    if not content.strip() or is_error_result(content):
        return f"[WEB SEARCH]\n{content}", []
    chunks = chunk_evidence("cloud web search", content, max_chars=2200)
    for index, chunk in enumerate(chunks, 1):
        chunk["citation"] = f"E{index}"
        chunk["locator"] = chunk.get("locator") or "web result"
    blocks = [
        f"[{chunk['citation']} | SOURCE: {chunk['source']} | LOCATOR: {chunk['locator']}]\n{chunk['text']}"
        for chunk in chunks
    ]
    return "\n\n".join(blocks), chunks


def _without_document_paths(query: str) -> str:
    suffixes = "pdf|docx|xlsx|pptx|rtf|html?|eml|msg|txt|md|csv|json|ya?ml|toml|png|jpe?g|tiff?|bmp|webp|zip|py"
    return re.sub(
        r"['\"]?(?:/|~/)[^,\n'\"]+?\.(?:%s)" % suffixes,
        " ",
        query,
        flags=re.I,
    ).strip()


def _candidate_paths(query: str, project_root: str | None) -> tuple[list[Path], list[str]]:
    """Return existing document paths and explicitly named missing paths."""
    candidates: list[Path] = []
    missing: list[str] = []
    root = Path(project_root) if project_root else Path.cwd()
    suffixes = "pdf|docx|xlsx|pptx|rtf|html?|eml|msg|txt|md|csv|json|ya?ml|toml|png|jpe?g|tiff?|bmp|webp|zip|py"
    quoted = re.findall(r"['\"]([^'\"]+\.(?:%s))['\"]" % suffixes, query, re.I)
    absolute = re.findall(
        r"((?:/|~/)[^,\n'\"]+?\.(?:%s))"
        r"(?=\s*(?:,|[.;]|$)|\s+(?:and|then|please|with|for|to|or|from|on|using|cite|summarize|read|find|against|versus|vs)\b)"
        % suffixes,
        query,
        re.I,
    )
    simple = re.findall(r"(?:[^\s'\"]+\.(?:%s))" % suffixes, query, re.I)
    full_paths = [*quoted, *absolute]
    simple = [token for token in simple if not any(token in full for full in full_paths)]
    tokens = list(dict.fromkeys([*full_paths, *simple]))
    for token in tokens:
        token = token.rstrip(".,:;)")
        path = Path(token).expanduser()
        if not path.is_absolute():
            path = root / path
        if path.is_file() and (path.suffix.lower() in _DOCUMENT_SUFFIXES or path.suffix.lower() == ".py"):
            if path not in candidates:
                candidates.append(path)
        elif path.suffix.lower() in _DOCUMENT_SUFFIXES or path.suffix.lower() == ".py":
            missing.append(str(path))
    return candidates, missing


def _discover_paths(project_root: str | None) -> list[Path]:
    """Discover supported documents below the explicit workspace root only."""
    if not project_root:
        return []
    root = Path(project_root).expanduser().resolve()
    if not root.is_dir():
        return []
    paths = []
    for path in root.rglob("*"):
        if path.is_file() and path.suffix.lower() in _DOCUMENT_SUFFIXES:
            paths.append(path)
    return sorted(paths)[:32]


def _prepare_evidence(query: str, project_root: str | None, chat_id: str | None = None) -> tuple[str, list[dict[str, str]]]:
    from clients.cancellation import check_aborted
    check_aborted()
    paths, missing = _candidate_paths(query, project_root)
    if not paths and not missing:
        paths = _discover_paths(project_root)
    blocks: list[str] = []
    evidence_chunks: list[dict[str, str]] = []

    if missing:
        return "\n".join(f"[SOURCE NOT FOUND: {path}]" for path in missing), []

    if not paths and project_root and chat_id:
        from tools.document_session import recall
        cached = recall(chat_id, project_root, limit=8)
        if cached:
            for index, item in enumerate(cached, 1):
                item["citation"] = f"E{index}"
            selected = rank_evidence(query, cached, limit=8)
            if selected:
                return "\n\n".join(
                    f"[{item['citation']} | SOURCE: {item['source']} | LOCATOR: {item.get('locator', 'document')}]\n{item['text']}"
                    for item in selected
                ), selected

    if not paths and _ACADEMIC_QUERY_RE.search(query):
        research_evidence, research_chunks = _prepare_research_evidence(query)
        if research_evidence:
            return research_evidence, research_chunks

    if not paths and _WEB_QUERY_RE.search(query):
        web_evidence, web_chunks = _prepare_web_evidence(query)
        if web_evidence:
            return web_evidence, web_chunks

    # Open-ended workspace questions use the indexed retrieval seam. This is
    # the scale path: large documents and large folders contribute only the
    # bounded evidence hits, never their full extracted contents.
    if not paths and project_root:
        raw = execute_tool(
            "document_retrieve",
            {"query": query, "workspace": project_root, "limit": 8, "search_mode": "hybrid"},
        )
        try:
            retrieved = json.loads(str(raw))
        except (TypeError, json.JSONDecodeError):
            retrieved = []
        if isinstance(retrieved, list) and retrieved:
            evidence_chunks = [item for item in retrieved if isinstance(item, dict) and item.get("source")]
            for index, item in enumerate(evidence_chunks, 1):
                item["citation"] = f"E{index}"
                blocks = [
                f"[{item['citation']} | SOURCE: {item['source']} | LOCATOR: {item.get('locator', 'indexed result')}]\n"
                f"{item.get('text', '')}"
                    for item in evidence_chunks
                ]
                from tools.document_session import remember
                remember(chat_id, project_root, evidence_chunks)
                return "\n\n".join(blocks), evidence_chunks

    for path in paths[:8]:
        check_aborted()
        # Keep the single synthesis call bounded. Larger documents need indexed
        # retrieval/chunk selection in a later slice, not a larger prompt.
        # Read enough of an explicit document for tail evidence to be found;
        # chunk/rank below still bounds what reaches Gemma.
        result = execute_tool("read_document", {"path": str(path), "max_chars": 50000})
        content = str(result)
        if is_error_result(content):
            blocks.append(f"[SOURCE: {path}]\n[EXTRACTION ERROR]\n{content}")
        else:
            chunks = chunk_evidence(str(path), content)
            # chunk_evidence numbers within one source; renumber globally so
            # citations remain unique across a multi-document request.
            for chunk in chunks:
                chunk["citation"] = f"E{len(evidence_chunks) + 1}"
                evidence_chunks.append(chunk)
            blocks.extend(
                f"[{chunk['citation']} | SOURCE: {chunk['source']} | LOCATOR: {chunk['locator']}]\n"
                f"{chunk['text']}"
                for chunk in chunks
            )

    mixed_web_chunks: list[dict[str, str]] = []
    if paths and _MIXED_WEB_RE.search(query):
        public_query = _without_document_paths(query)
        web_evidence, mixed_web_chunks = _prepare_web_evidence(public_query)
        if web_evidence:
            blocks.append(web_evidence)
            for chunk in mixed_web_chunks:
                chunk["citation"] = f"E{len(evidence_chunks) + 1}"
                evidence_chunks.append(chunk)

    if not blocks:
        result = execute_tool("fulltext_search", {"query": query, "top_k": 8})
        content = str(result)
        if not is_error_result(content):
            blocks.append(f"[SEARCH EVIDENCE]\n{content}")

    if not blocks:
        return "[NO LOCAL EVIDENCE FOUND]", []
    # File paths are discovery metadata, not semantic query terms. Leaving a
    # ZIP filename such as "Pathoma Slides" in the ranking query can outrank
    # the actual requested fact (for example, "Gastroschisis").
    ranking_query = _without_document_paths(query)
    selected = rank_evidence(ranking_query, evidence_chunks, limit=8)
    # Explicitly named multi-document requests are comparison-capable only if
    # every named source reaches synthesis. Lexical ranking may otherwise drop
    # a valid but semantically different document (for example, a contract and
    # a notes file), making Gemma compare incomplete evidence.
    if len(paths) > 1:
        selected_ids = {chunk["citation"] for chunk in selected}
        for path in paths[:8]:
            source_chunks = [chunk for chunk in evidence_chunks if chunk["source"] == str(path)]
            if source_chunks and not any(chunk["citation"] in selected_ids for chunk in source_chunks):
                selected.append(source_chunks[0])
                selected_ids.add(source_chunks[0]["citation"])
        selected = selected[:8]
    if mixed_web_chunks and not any(chunk["source"] == "cloud web search" for chunk in selected):
        selected.append(mixed_web_chunks[0])
        selected = selected[:8]
    selected_ids = {chunk["citation"] for chunk in selected}
    selected_blocks = [block for block in blocks if any(f"[{citation} |" in block for citation in selected_ids)]
    if chat_id and project_root and evidence_chunks:
        check_aborted()
        from tools.document_session import remember
        remember(chat_id, project_root, evidence_chunks)
        # Enroll documents actually touched by a real session in the durable
        # semantic index. This is best-effort: answering must not fail because
        # the optional embedding model is unavailable.
        for path in paths[:8]:
            job_id = None
            try:
                from tools.document_index_queue import enqueue, complete
                job_id = enqueue(path, workspace=project_root)
                from tools.semantic_files import index_directory
                index_directory(str(path.parent), glob=path.name, refresh=False)
                if job_id is not None:
                    complete(job_id, success=True)
            except Exception:
                if job_id is not None:
                    try:
                        complete(job_id, success=False)
                    except Exception:
                        pass
                pass
    return "\n\n".join(selected_blocks), selected


def run_document_agent(
    query: str,
    model: str = DEFAULT_CLOUD_MODEL,
    chat_id: str | None = None,
    project_root: str | None = None,
) -> str:
    """Prepare local evidence, then make exactly one no-tools model call."""
    scratch_block = ""
    scratch_path = ""
    if chat_id:
        from tools.document_workspace import scratchpad_block, session_dir
        scratch_path = str(session_dir(chat_id, project_root))
        scratch_block = scratchpad_block(chat_id, project_root)
    retrieval_query = query
    conversation_context = ""
    if chat_id:
        try:
            from store.conversation import get as get_conversation, trim_to_budget
            history = get_conversation(chat_id)
            recent = trim_to_budget(history, model, query, chat_id=chat_id)[-6:]
            prior_user_turns = [turn["content"] for turn in recent if turn.get("role") == "user"]
            if prior_user_turns:
                conversation_context = "\n".join(prior_user_turns[-3:])
                retrieval_query = f"{query}\nPrior document context:\n{conversation_context}"
        except Exception:
            conversation_context = ""
    evidence, evidence_chunks = _prepare_evidence(retrieval_query, project_root, chat_id=chat_id)
    from clients.cancellation import check_aborted
    check_aborted()
    prompt = (
        "Answer the user's document request using only the local evidence below. "
        "Do not invent facts. Cite evidence using [E1], [E2], etc. If evidence "
        "is missing, say so.\n\n"
        f"USER REQUEST:\n{query}\n"
        f"CONVERSATION CONTEXT:\n{conversation_context or '[none]'}\n\nLOCAL EVIDENCE:\n{evidence}"
    )
    if scratch_path:
        prompt += (
            f"\n\nPRIVATE SCRATCH WORKSPACE:\n{scratch_path}\n"
            "Use this only for intermediate artifacts when a document tool supports it. "
            "Do not treat it as an export destination and do not overwrite user files without approval."
        )
    if scratch_block:
        prompt += f"\n\n{scratch_block}"
    response = _chat(
        model=model,
        messages=[{"role": "user", "content": prompt}],
        tools=[],
        temperature=None if model.startswith("gemma4") else 0.0,
    )
    answer = (getattr(response.message, "content", "") or "").strip() or "No answer generated."
    warnings = find_grounding_warnings(answer, evidence_chunks)
    refusal_hint = _numeric_refusal_hint(query, answer, evidence)
    if warnings or refusal_hint:
        correction_prompt = (
            "Correct the draft answer below using only the supplied local evidence. "
            "Remove unsupported claims, amounts, dates, and citations. If the evidence "
            "does not establish a fact, say that it is not established. Preserve valid "
            "claims and cite only known IDs. For a formula-backed numeric question, "
            "perform the arithmetic instead of repeating a refusal. Return only the corrected answer.\n\n"
            f"GROUNDING WARNINGS:\n{chr(10).join(warnings) or '[none]'}\n"
            f"DERIVATION HINT:\n{refusal_hint or '[none]'}\n\n"
            f"DRAFT ANSWER:\n{answer}\n\nLOCAL EVIDENCE:\n{evidence}"
        )
        try:
            correction = _chat(
                model=model,
                messages=[{"role": "user", "content": correction_prompt}],
                tools=[],
                temperature=0.0,
            )
            corrected = (getattr(correction.message, "content", "") or "").strip()
            if corrected:
                answer = corrected
            # A bounded correction model can repeat the refusal. When the
            # deterministic guard already proved a simple formula, do not let
            # that second refusal erase a calculable, cited result.
            if refusal_hint and re.search(
                r"\b(?:not established|cannot determine|can't determine|unknown|not available)\b",
                answer, re.I,
            ):
                value_match = re.search(r"=\s*([\d]+(?:\.\d+)?)", refusal_hint)
                if value_match:
                    relevant_ids = [
                        chunk["citation"] for chunk in evidence_chunks
                        if re.search(r"(?:Q2|quarter 2)|(?:grew|increased|rose)", chunk.get("text", ""), re.I)
                    ]
                    citations = " ".join(f"[{citation}]" for citation in relevant_ids[:3])
                    answer = f"The derived value is {value_match.group(1)}. {citations}".strip()
        except Exception:
            # Keep the original answer plus the visible warning if the bounded
            # correction pass is unavailable.
            pass
    answer = attach_citation_map(answer, evidence_chunks)
    return answer + _export_notice(query, answer)


def _export_notice(query: str, content: str) -> str:
    """Prepare an approval request for explicit PDF exports; never write here."""
    if not re.search(r"\b(export|save|create|generate)\b.*\bpdf\b|\bpdf\b.*\b(export|save|create|generate)\b", query, re.I | re.S):
        return ""
    match = re.search(r"(?:to|as|at|named)\s+([^\s'\"]+\.pdf)\b", query, re.I)
    if not match:
        return "\n\nPDF export not performed. Provide an explicit output path for approval."
    output_path = str(Path(match.group(1)).expanduser())
    args = {"content": content, "output_path": output_path, "theme": "light"}
    token = request_confirmation("create_pdf", args, f"create_pdf(output_path={output_path!r})")
    return (
        f"\n\nPDF export is awaiting approval. Nothing was written. Token: {token}. "
        "Approve or deny it through the local-agent confirmation endpoint."
    )
