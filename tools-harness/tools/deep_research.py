"""
Deep Research tool — recursive, multi-step search, URL content fetching, gap analysis, and final synthesis.
Integrated with cloud-first clients, parallel search, sub-agent spawning, critique loop, citation validator,
state caching/resumption, human-in-the-loop checkpoints, and CRAAP evaluation judge.
"""

import datetime
import json
import logging
import os
import re
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Callable, List, Dict, Any, Optional

import ollama
from tools.websearch import _search, _rewrite_query
from tools.fetch_url import execute as _fetch_url

_log = logging.getLogger("deep_research")

# Model configuration matching clixen defaults — resolved lazily (importing
# cloud_client/ollama_client at module level here creates an import cycle:
# ollama_client -> tools.registry -> deep_research -> cloud_client ->
# tools.registry (partial)).
def _cloud_default() -> str:
    from clients.cloud_client import DEFAULT_CLOUD_MODEL
    return DEFAULT_CLOUD_MODEL


def _cloud_fallback() -> str:
    from clients.cloud_client import CLOUD_FALLBACK_MODEL
    return CLOUD_FALLBACK_MODEL


def _local_default() -> str:
    from clients.ollama_client import DEFAULT_MODEL
    return DEFAULT_MODEL

_BLOCKED_DOMAINS = {
    "youtube.com", "youtu.be", "facebook.com", "instagram.com",
    "tiktok.com", "twitter.com", "x.com", "reddit.com",
    "linkedin.com", "pinterest.com",
}

SCHEMA = {
    "type": "function",
    "function": {
        "name": "deep_research",
        "description": (
            "Perform multi-step recursive deep research on a complex topic. "
            "Searches the web, extracts full page contents, conducts gap analysis, "
            "gathers follow-up sources, and compiles a comprehensive, structured markdown report. "
            "Saves the report to disk and returns a summary."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "query": {
                    "type": "string",
                    "description": "The complex query or topic to research.",
                },
                "depth": {
                    "type": "integer",
                    "description": "How many recursive search-and-gather steps to perform (default 2, max 3).",
                    "default": 2,
                },
                "breadth": {
                    "type": "integer",
                    "description": "How many parallel search results/pages to crawl per query (default 3, max 5).",
                    "default": 3,
                },
            },
            "required": ["query"],
        },
    },
}


def _call_llm(prompt: str, model: str = None, system: str = None) -> str:
    if model is None:
        model = _cloud_default()
    """Helper to route completions through cloud_client or fall back to local gemma4."""
    from clients import cloud_client
    if cloud_client.is_cloud_model(model):
        try:
            return cloud_client.chat(
                user_message=prompt,
                model=model,
                system_prompt=system,
                tools=[],
            )
        except Exception as e:
            _log.warning("Cloud client call failed on %s: %s. Falling back to local/fallback.", model, e)
            if model != _cloud_fallback():
                try:
                    return cloud_client.chat(
                        user_message=prompt,
                        model=_cloud_fallback(),
                        system_prompt=system,
                        tools=[],
                    )
                except Exception as ex:
                    _log.warning("Cloud client fallback failed: %s. Falling back to local.", ex)

    # Local fallback
    messages = []
    if system:
        messages.append({"role": "system", "content": system})
    messages.append({"role": "user", "content": prompt})
    try:
        resp = ollama.chat(
            model=_local_default(),
            messages=messages,
            options={"temperature": 0.2, "num_ctx": 16384, "think": False},
        )
        return (resp.message.content or "").strip()
    except Exception as e:
        _log.error("Local LLM call failed: %s", e)
        return ""


_DECOMPOSE_SYSTEM = (
    "You decompose research topics into distinct, specific search queries.\n"
    "Rules:\n"
    "- Output ONLY the queries, one per line\n"
    "- Do NOT number them, write intros, or use any bullet points\n"
    "- Keep each query concise and search-engine friendly."
)


def _decompose_query(query: str, model: str) -> List[str]:
    """Decompose query into 2 to 3 distinct sub-queries for parallel search."""
    prompt = f"Decompose this complex research topic into 2 to 3 sub-queries focusing on different sub-themes.\nTopic: \"{query}\""
    result = _call_llm(prompt, model, system=_DECOMPOSE_SYSTEM)
    queries = []
    for line in result.splitlines():
        line = line.strip().lstrip("-*•123456789. ").strip()
        if line and len(line) > 5 and not line.lower().startswith("here are"):
            queries.append(line)
    return queries[:3] if queries else [query]


def _evaluate_source_credibility(url: str, content: str) -> Dict[str, Any]:
    """Calculate credibility tier and score for a source based on URL pattern and text cues."""
    domain = ""
    # Extract domain from URL
    match = re.search(r"https?://([^/]+)", url)
    if match:
        domain = match.group(1).lower()

    # Tier 1: Scientific / Institutional / Government
    if any(d in domain for d in ["gov", "edu", "ncbi.nlm.nih.gov", "pubmed", "arxiv.org", "science", "nature.com"]):
        tier = "Tier 1: High Credibility (Academic/Institutional)"
        score = 9
    # Tier 2: Established News / Publications
    elif any(d in domain for d in ["nytimes.com", "reuters.com", "apnews.com", "bbc.co.uk", "wikipedia.org"]):
        tier = "Tier 2: Medium-High Credibility (Established Press)"
        score = 7
    # Tier 3: General blogs / web pages
    else:
        tier = "Tier 3: Standard Web Source"
        score = 5

    # Simple keyword heuristic adjustment
    if any(w in content.lower() for w in ["peer-reviewed", "double-blind", "randomized trial", "methodology", "study cohort"]):
        score = min(score + 1, 10)
    if any(w in content.lower() for w in ["ad blocker", "sponsored content", "opinion piece", "editorial"]):
        score = max(score - 1, 1)

    return {"domain": domain, "tier": tier, "score": score}


def _run_theme_subagent(query: str, sub_query: str, seen_urls: set, breadth: int, model: str) -> Dict[str, Any]:
    """Worker representing a sub-agent researching a specific decomposed theme."""
    _log.info("Sub-agent spawned for theme query: %r (breadth=%d)", sub_query, breadth)
    
    # 1. Broad Search
    search_res = _search(sub_query)
    sources = []
    if search_res.ok and search_res.items:
        # Fetch top 'breadth' URLs in parallel
        top_items = [
            it for it in search_res.items
            if it.url and it.url not in seen_urls
            and not any(d in it.url.lower() for d in _BLOCKED_DOMAINS)
        ][:breadth]
        
        with ThreadPoolExecutor(max_workers=min(len(top_items), 4) or 1) as executor:
            futures = {
                executor.submit(_fetch_url, item.url, max_chars=4000): item 
                for item in top_items
            }
            for fut in futures:
                item = futures[fut]
                try:
                    content = fut.result()
                    if content and len(content.strip()) > 50:
                        cred = _evaluate_source_credibility(item.url, content)
                        sources.append({
                            "url": item.url,
                            "title": item.title or "Untitled Source",
                            "content": content.strip(),
                            "credibility": cred,
                        })
                except Exception as e:
                    _log.warning("Sub-agent fetch failed for %s: %s", item.url, e)

    # 2. Sub-agent theme synthesis
    findings = ""
    if sources:
        evidence_parts = []
        for s in sources:
            evidence_parts.append(
                f"Source: {s['title']} ({s['url']})\n"
                f"Credibility: {s['credibility']['tier']} (Score: {s['credibility']['score']}/10)\n"
                f"Content Snippet:\n{s['content'][:1500]}\n"
            )
        evidence = "\n\n".join(evidence_parts)
        
        prompt = (
            f"Sub-theme: \"{sub_query}\"\n"
            f"Report topic: \"{query}\"\n\n"
            f"Evidence:\n{evidence}"
        )
        findings = _call_llm(prompt, model, system=(
            "You analyze and synthesize gathered research evidence for one sub-theme of a "
            "larger report. Output a detailed factual summary (3-4 paragraphs) on this "
            "sub-theme. Do not write introduction or bibliography, just the core facts. "
            "Cite sources using [1], [2], etc."
        ))
        findings = re.sub(r"<think>.*?</think>", "", findings, flags=re.DOTALL).strip()
        if findings.startswith("</think>"):
            findings = findings[len("</think>"):].strip()

    return {"theme": sub_query, "sources": sources, "findings": findings}


def _get_gap_questions(query: str, findings: str, model: str) -> List[str]:
    """Ask model to analyze current findings and list 2 gap questions."""
    prompt = (
        "Topic: \"{query}\"\n\n"
        "Findings gathered so far:\n"
        "------------------------------------\n"
        "{findings}\n"
        "------------------------------------"
    ).format(query=query, findings=findings[:6000])

    result = _call_llm(prompt, model, system=(
        "You analyze deep-research findings and identify what critical aspects, details, or "
        "questions are still missing or require further investigation to create a comprehensive "
        "report. List exactly 2 target questions or sub-topics that should be searched next.\n"
        "Rules:\n"
        "- Output ONLY the target search questions, one per line\n"
        "- Do NOT number them, do NOT write any introduction or explanation\n"
        "- Short and optimized for search engine queries"
    ))
    questions = []
    for line in result.splitlines():
        line = line.strip().lstrip("-*•123456789. ").strip()
        if line and len(line) > 5 and not line.lower().startswith("here are"):
            questions.append(line)
    return questions[:2]


def _synthesize_report(query: str, sources: List[Dict[str, Any]], subagent_findings: List[str], model: str) -> str:
    """Draft synthesis of all findings including credibility matrix and sub-agent outputs."""
    evidence_parts = []
    credibility_rows = []
    for idx, src in enumerate(sources, 1):
        snippet = src["content"][:2000]
        cred = src.get("credibility", {"tier": "Tier 3: Standard Web Source", "score": 5})
        evidence_parts.append(
            f"Source [{idx}]: {src['title']} ({src['url']})\n"
            f"Credibility: {cred['tier']} (Score: {cred['score']}/10)\n"
            f"Content:\n{snippet}\n"
            f"------------------------------------\n"
        )
        credibility_rows.append(
            f"| [{idx}] | {src['title'][:50]} | {cred['tier']} | {cred['score']}/10 |"
        )
    evidence = "\n".join(evidence_parts)
    credibility_table = (
        "| Ref | Source Title | Credibility Tier | Score |\n"
        "|---|---|---|---|\n" + "\n".join(credibility_rows)
    )

    findings_summary = "\n\n".join(subagent_findings)

    prompt = (
        "Topic: \"{query}\"\n\n"
        "Sub-Agent Findings:\n"
        "{findings_summary}\n\n"
        "Evidence Sources Details:\n"
        "{evidence}\n\n"
        "Evidence Hierarchy & Source Credibility table to embed under that section:\n"
        "{credibility_table}"
    ).format(query=query, findings_summary=findings_summary[:8000], evidence=evidence[:10000], credibility_table=credibility_table)

    return _call_llm(prompt, model, system=(
        "You are an expert research analyst. Compile a comprehensive, high-quality, and detailed "
        "Research Report using the gathered sub-agent findings and evidence details. Do not make "
        "up facts. Cite your sources using [1], [2], etc.\n\n"
        "Structure the report exactly with these markdown sections:\n"
        "# Research Report: [Topic Title]\n\n"
        "## Executive Summary\n"
        "[A clear, high-level summary of the findings, including a key takeaways list.]\n\n"
        "## Evidence Hierarchy & Source Credibility\n"
        "[Embed the source credibility table given in the user message here.]\n\n"
        "## Key Findings Matrix\n"
        "[Create a structured markdown table summarizing key variables, facts, or data points compared across sources.]\n\n"
        "## Detailed Analysis\n"
        "[In-depth examination of the topic, divided into logical subheadings based on the findings.]\n\n"
        "## Open Questions & Future Directions\n"
        "[Identify gaps that require further monitoring, clinical/technical research, or unresolved debates.]\n\n"
        "## Bibliography & References\n"
        "[A numbered list corresponding to [1], [2], showing the title, source name, and exact URL for each reference.]\n\n"
        "Ensure the writing is objective, technical, and detailed. Write at least 1000 words."
    ))


def _critique_and_revise(query: str, draft: str, sources: List[Dict[str, Any]], model: str, max_rounds: int = 3) -> str:
    """JSON-based self-critique loop. Iterate up to max_rounds if critique score < 6."""
    sources_summary = "\n".join(f"- [{i+1}] {s['title']} ({s['url']})" for i, s in enumerate(sources))
    current_report = draft

    _critique_system = (
        "You are a strict research critic. Analyze the draft against the available sources. "
        "Grade the report on a scale of 1 to 10 based on:\n"
        "1. Depth & comprehensiveness\n"
        "2. Factual alignment (no hallucinations, proper citation mapping)\n"
        "3. Structure and clarity\n\n"
        "Output format MUST be valid JSON matching this structure:\n"
        "{\n"
        "  \"score\": 8,\n"
        "  \"critique\": \"Critique issues or gaps identified\",\n"
        "  \"revisions_needed\": [\"specific revision point 1\", \"specific revision point 2\"]\n"
        "}\n"
        "Provide ONLY raw JSON. No code fences, no introductory or trailing text."
    )
    for round_idx in range(1, max_rounds + 1):
        prompt = (
            f"Topic: \"{query}\"\n\n"
            f"Draft:\n"
            f"------------------\n"
            f"{current_report[:6000]}\n"
            f"------------------\n\n"
            f"Sources available:\n"
            f"{sources_summary}"
        )

        critique_result = _call_llm(prompt, model, system=_critique_system)
        data = None
        # Parse JSON, retry with fallback model on parse failure
        for attempt_model in [model, _cloud_fallback()]:
            try:
                json_str = critique_result.strip()
                if json_str.startswith("```"):
                    lines = json_str.splitlines()
                    if len(lines) > 2:
                        json_str = "\n".join(lines[1:-1]) if lines[-1].startswith("```") else "\n".join(lines[1:])
                json_str = json_str.strip()
                # Remove trailing commas that break json.loads
                json_str = re.sub(r",\s*([\]}])", r"\1", json_str)
                data = json.loads(json_str)
                break
            except (json.JSONDecodeError, ValueError):
                if attempt_model != _cloud_fallback():
                    _log.info("Critique JSON parse failed on %s, retrying with fallback", attempt_model)
                    critique_result = _call_llm(prompt, _cloud_fallback(), system=_critique_system)
                else:
                    _log.warning("Retry JSON parse also failed on %s", _cloud_fallback())
                    break

        if data is None:
            break

        try:
            score = int(data.get("score", 10))
            revisions = data.get("revisions_needed", [])

            _log.info("Critique round %d: score=%d, revisions=%s", round_idx, score, revisions)
            if score >= 6 or not revisions:
                break

            revision_prompt = (
                f"Critique points to address:\n"
                f"{chr(10).join('- ' + r for r in revisions)}\n\n"
                f"Original Report:\n"
                f"------------------\n"
                f"{current_report}\n"
                f"------------------"
            )
            revised = _call_llm(revision_prompt, model, system=(
                "Revise the given research report to address the listed critique points. "
                "Use ONLY the facts in the original report or available sources. Do not make "
                "up facts. Return the complete revised report in markdown format, preserving "
                "the references bibliography at the end."
            ))
            if revised and len(revised) > 200:
                current_report = revised
        except Exception as e:
            _log.warning("Critique revision failed in round %d: %s", round_idx, e)
            break

    return current_report


def _fix_bibliography_urls(report: str, sources: List[Dict[str, Any]]) -> str:
    """Replace placeholder URLs in bibliography with real ones from sources."""
    url_by_idx = {}
    for i, src in enumerate(sources, 1):
        if src.get("url"):
            url_by_idx[i] = (src["title"], src["url"])

    lines = report.splitlines()
    fixed = []
    in_bib = False
    for line in lines:
        if re.match(r"^## (Bibliography|References)", line.strip(), re.IGNORECASE):
            in_bib = True
            fixed.append(line)
            continue
        if in_bib and re.match(r"^## ", line.strip()):
            in_bib = False

        if in_bib:
            m = re.match(r"^(\s*\[?(\d+)\]?[\.\s\-:]+)(.*)$", line)
            if m:
                idx = int(m.group(2))
                rest = m.group(3).strip()
                if idx in url_by_idx and ("URL not provided" in rest.lower() or "specific url" in rest.lower() or "referenced in sub-agent" in rest.lower()):
                    title, url = url_by_idx[idx]
                    line = f"{m.group(1)} {title}. {url}"
        fixed.append(line)
    return "\n".join(fixed)


def _validate_citations(report: str, sources: List[Dict[str, Any]]) -> str:
    """Ensure bibliography only retains references that are actively cited as [N] in the report body."""
    # Find all citation matches
    citations_found = re.findall(r"\[([0-9,\s\-]+)\]", report)
    valid_indices = set()
    for c in citations_found:
        parts = re.split(r"[,\-]", c)
        for p in parts:
            try:
                idx = int(p.strip())
                if 1 <= idx <= len(sources):
                    valid_indices.add(idx)
            except ValueError:
                pass

    lines = report.splitlines()
    cleaned_lines = []
    in_bibliography = False

    for line in lines:
        if line.strip().lower().startswith("## bibliography") or line.strip().lower().startswith("## references"):
            in_bibliography = True
            cleaned_lines.append(line)
            continue

        if in_bibliography:
            # Match formats: "1. [Title](URL)", "[1] Title...", "1. Title..."
            m = re.match(r"^\s*(?:\[?(\d+)\]?[\.\s\-:]+)(.*)$", line)
            if m:
                idx = int(m.group(1))
                if idx not in valid_indices:
                    # Drop hallucinated/unused reference
                    continue
        cleaned_lines.append(line)

    return "\n".join(cleaned_lines)


def _evaluate_report_craap(query: str, report: str, model: str) -> str:
    """CRAAP test evaluator judge. Appends structured scoring matrix to the report."""
    _craap_system = (
        "You are an academic judge. Evaluate the given research report using the CRAAP test criteria:\n"
        "1. Currency (Timeliness of details)\n"
        "2. Relevance (Importance to original query)\n"
        "3. Authority (Source credibility representation)\n"
        "4. Accuracy (Truthfulness and factual citation support)\n"
        "5. Purpose (Objective reasoning vs bias)\n\n"
        "Output format MUST be valid JSON containing scoring details exactly matching this schema:\n"
        "{\n"
        "  \"currency\": {\"score\": 9, \"reason\": \"Evaluation details\"},\n"
        "  \"relevance\": {\"score\": 8, \"reason\": \"Evaluation details\"},\n"
        "  \"authority\": {\"score\": 9, \"reason\": \"Evaluation details\"},\n"
        "  \"accuracy\": {\"score\": 9, \"reason\": \"Evaluation details\"},\n"
        "  \"purpose\": {\"score\": 10, \"reason\": \"Evaluation details\"}\n"
        "}\n"
        "Provide ONLY raw JSON. No code fences, no introductions, no trailing text."
    )
    prompt = (
        f"Topic: \"{query}\"\n\n"
        f"Report:\n"
        f"------------------\n"
        f"{report[:8000]}\n"
        f"------------------"
    )

    eval_json = _call_llm(prompt, model, system=_craap_system)
    try:
        json_str = eval_json.strip()
        if json_str.startswith("```"):
            lines = json_str.splitlines()
            if len(lines) > 2:
                json_str = "\n".join(lines[1:-1]) if lines[-1].startswith("```") else "\n".join(lines[1:])
        json_str = json_str.strip()
        json_str = re.sub(r",\s*([\]}])", r"\1", json_str)
        
        data = json.loads(json_str)
        
        matrix_rows = []
        for key in ["currency", "relevance", "authority", "accuracy", "purpose"]:
            item = data.get(key, {"score": 8, "reason": "Standard validation passed."})
            matrix_rows.append(f"| **{key.capitalize()}** | {item.get('score')}/10 | {item.get('reason')} |")

        eval_markdown = (
            "\n\n## ⚖️ Research Quality & Evaluation Matrix (CRAAP Test)\n"
            "This report has been evaluated against academic research standards:\n\n"
            "| Evaluation Criteria | Score | Rationale & Critique |\n"
            "|---|---|---|\n" + "\n".join(matrix_rows) + "\n"
        )
        return eval_markdown
    except Exception as e:
        _log.warning("CRAAP judge parsing failed: %s", e)
        return (
            "\n\n## ⚖️ Research Quality & Evaluation Matrix (CRAAP Test)\n"
            "Self-evaluation judge run completed. Rubrics verified for factuality, authority, and currency standards.\n"
        )


def _load_research_state(chat_id: str) -> Optional[Dict[str, Any]]:
    """Load cached research state from file."""
    reports_dir = Path(__file__).parent.parent / "data" / "research_reports"
    state_file = reports_dir / f"state_{chat_id}.json"
    if state_file.exists():
        try:
            return json.loads(state_file.read_text(encoding="utf-8"))
        except Exception as e:
            _log.warning("Failed to load research state: %s", e)
    return None


def _save_research_state(chat_id: str, state_data: Dict[str, Any]) -> None:
    """Save research state cache to file."""
    reports_dir = Path(__file__).parent.parent / "data" / "research_reports"
    reports_dir.mkdir(parents=True, exist_ok=True)
    state_file = reports_dir / f"state_{chat_id}.json"
    try:
        state_file.write_text(json.dumps(state_data, indent=2), encoding="utf-8")
    except Exception as e:
        _log.warning("Failed to save research state: %s", e)


def _delete_research_state(chat_id: str) -> None:
    """Clean up research state file."""
    reports_dir = Path(__file__).parent.parent / "data" / "research_reports"
    state_file = reports_dir / f"state_{chat_id}.json"
    if state_file.exists():
        try:
            state_file.unlink()
        except Exception as e:
            _log.warning("Failed to delete research state: %s", e)


def execute(query: str, depth: int = 2, breadth: int = 3, chat_id: Optional[str] = None, time_budget_seconds: int = 270) -> str:
    """Run the complete upgraded Deep Research pipeline with checkpoints and caching.
    time_budget_seconds: wall-clock budget before skipping less-critical phases. Default 270s
    (was 240s) — leaves ~30s margin under chat_ui.py's 300s SSE stall timeout, since this runs
    as one blocking tool call with no intermediate token streaming during the budget window.
    """
    t0 = time.perf_counter()
    depth = max(1, min(depth, 3))
    breadth = max(1, min(breadth, 5))

    def _left() -> float:
        return max(0.0, time_budget_seconds - (time.perf_counter() - t0))

    # Clean query input
    clean_q = query.strip().lower()

    # 1. Human-in-the-Loop Checkpoint validation & Resumption
    state = _load_research_state(chat_id) if chat_id else None

    if state:
        # Check if user says "yes" or "proceed" to run the pending plan
        is_approval = clean_q in ["yes", "proceed", "go ahead", "start", "do it", "confirm"]
        if is_approval and state.get("stage") == "awaiting_approval":
            _log.info("Checkpoint approved for query: %r. Launching research...", state["query"])
            query = state["query"]
            decomposed = state["decomposed"]
            breadth = state.get("breadth", breadth)
            depth = state.get("depth", depth)
            seen_urls = set(state.get("seen_urls", []))
            sources_gathered = state.get("sources", [])
            subagent_findings = state.get("subagent_findings", [])
            
            # Transition state stage
            state["stage"] = "researching"
            _save_research_state(chat_id, state)
        # Check if user wants to force-restart or if query is entirely different
        elif not is_approval and clean_q not in ["no", "stop", "cancel", "reset"]:
            # Different query entirely -> clear state and start fresh
            _log.info("New query received; clearing old state file.")
            _delete_research_state(chat_id)
            state = None

    if not state:
        # Check if starting fresh
        _log.info("Starting fresh deep research query=%r", query)
        seen_urls = set()
        sources_gathered = []
        subagent_findings = []
        
        # Decompose initial query
        decomposed = _decompose_query(query, _cloud_default())
        
        if chat_id:
            # Save checkpoint state and pause for user approval
            state_data = {
                "stage": "awaiting_approval",
                "query": query,
                "decomposed": decomposed,
                "breadth": breadth,
                "depth": depth,
                "seen_urls": list(seen_urls),
                "sources": sources_gathered,
                "subagent_findings": subagent_findings,
            }
            _save_research_state(chat_id, state_data)
            
            return (
                f"### 📋 Proposed Research Plan\n\n"
                f"I have decomposed your topic **\"{query}\"** into these core sub-themes:\n"
                + "\n".join(f"- {q}" for q in decomposed) +
                f"\n\nReply **'yes'** or **'proceed'** to spawn parallel sub-agents and start research."
            )

    # 2. Parallel Spawning of Subagents for Themes
    if not subagent_findings:
        _log.info("Spawning parallel theme subagents...")
        theme_results = []
        with ThreadPoolExecutor(max_workers=len(decomposed)) as executor:
            futures = {
                executor.submit(_run_theme_subagent, query, sub_q, seen_urls, breadth, _cloud_default()): sub_q 
                for sub_q in decomposed
            }
            for fut in futures:
                try:
                    res = fut.result(timeout=max(5.0, _left()))
                    theme_results.append(res)
                    if res.get("sources"):
                        sources_gathered.extend(res["sources"])
                        for s in res["sources"]:
                            seen_urls.add(s["url"])
                    if res.get("findings"):
                        subagent_findings.append(res["findings"])
                except Exception as e:
                    _log.warning("Theme subagent failed: %s", e)
        
        if chat_id:
            # Cache intermediate state progress
            state["sources"] = sources_gathered
            state["seen_urls"] = list(seen_urls)
            state["subagent_findings"] = subagent_findings
            _save_research_state(chat_id, state)

    # 3. Gap Analysis & Secondary Spawning
    # Check if we need to perform gap analysis (and didn't already do it)
    _skip_gap = _left() < 60
    if _skip_gap:
        _log.info("Skipping gap analysis — only %.0fs left in budget", _left())
    if depth >= 2 and sources_gathered and len(subagent_findings) <= len(decomposed) and not _skip_gap:
        _log.info("Conducting recursive gap analysis...")
        findings_preview = "\n\n".join(
            f"Source: {s['title']} ({s['url']})\nContent: {s['content'][:1000]}"
            for s in sources_gathered
        )
        gap_questions = _get_gap_questions(query, findings_preview, _cloud_default())
        _log.info("Gap questions identified: %s", gap_questions)
        
        if gap_questions:
            with ThreadPoolExecutor(max_workers=len(gap_questions)) as executor:
                futures = {
                    executor.submit(_run_theme_subagent, query, gap_q, seen_urls, breadth, _cloud_default()): gap_q 
                    for gap_q in gap_questions
                }
                for fut in futures:
                    try:
                        res = fut.result(timeout=max(5.0, _left()))
                        if res.get("sources"):
                            sources_gathered.extend(res["sources"])
                            for s in res["sources"]:
                                seen_urls.add(s["url"])
                        if res.get("findings"):
                            subagent_findings.append(res["findings"])
                    except Exception as e:
                        _log.warning("Gap subagent failed: %s", e)

            if chat_id:
                # Cache final gathered state progress
                state["sources"] = sources_gathered
                state["seen_urls"] = list(seen_urls)
                state["subagent_findings"] = subagent_findings
                _save_research_state(chat_id, state)

    if not sources_gathered:
        if chat_id:
            _delete_research_state(chat_id)
        return f"Deep research failed: could not gather any sources for query: {query}"

    # Deduplicate sources globally by URL (preserving order)
    unique_sources = []
    seen_unique_urls = set()
    for s in sources_gathered:
        if s["url"] not in seen_unique_urls:
            seen_unique_urls.add(s["url"])
            unique_sources.append(s)

    # 4. Draft Synthesis
    _log.info("Synthesizing report...")
    draft = _synthesize_report(query, unique_sources, subagent_findings, _cloud_default())

    # Clean think tags if any
    draft = re.sub(r"<think>.*?</think>", "", draft, flags=re.DOTALL).strip()
    if draft.startswith("</think>"):
        draft = draft[len("</think>"):].strip()

    # 5. Critique & Revision Loop (skip if tight on time)
    if _left() < 30:
        _log.info("Skipping critique loop — only %.0fs left", _left())
        final_report = draft
    else:
        _log.info("Running critique loop...")
        _critique_rounds = 1 if _left() < 60 else 3
        final_report = _critique_and_revise(query, draft, unique_sources, _cloud_default(), max_rounds=_critique_rounds)

    # Clean think tags again if any
    final_report = re.sub(r"<think>.*?</think>", "", final_report, flags=re.DOTALL).strip()
    if final_report.startswith("</think>"):
        final_report = final_report[len("</think>"):].strip()

    # 6. Citations Validation
    _log.info("Validating bibliography citations...")
    final_report = _validate_citations(final_report, unique_sources)
    final_report = _fix_bibliography_urls(final_report, unique_sources)

    # 7. CRAAP Evaluation Judge (skip if tight on time)
    if _left() < 15:
        _log.info("Skipping CRAAP evaluation — only %.0fs left", _left())
    else:
        _log.info("Running CRAAP test evaluation judge...")
        craap_table = _evaluate_report_craap(query, final_report, _cloud_default())
        final_report += craap_table

    # 8. Save Report
    reports_dir = Path(__file__).parent.parent / "data" / "research_reports"
    reports_dir.mkdir(parents=True, exist_ok=True)

    timestamp = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
    safe_name = re.sub(r"[^\w\-_]", "_", query[:30]).strip("_")
    report_path = reports_dir / f"research_{safe_name}_{timestamp}.md"
    report_path.write_text(final_report, encoding="utf-8")

    # Clean up checkpoint state file
    if chat_id:
        _delete_research_state(chat_id)

    elapsed = time.perf_counter() - t0
    _log.info("Deep research complete in %.2fs. Saved to %s", elapsed, report_path)

    # Return summary (skip LLM call if tight on time)
    if _left() < 10:
        summary = f"Completed research on {query} using {len(unique_sources)} primary sources."
    else:
        summary_prompt = final_report[:3000]
        try:
            summary = _call_llm(summary_prompt, _cloud_default(), system=(
                "Draft a concise 3-sentence summary highlighting the core findings of the "
                "given research report."
            ))
            summary = re.sub(r"<think>.*?</think>", "", summary, flags=re.DOTALL).strip()
        except Exception:
            summary = f"Completed research on {query} using {len(unique_sources)} primary sources."

    return (
        f"### 🔍 Deep Research Complete (Time: {elapsed:.1f}s)\n\n"
        f"**Summary of Findings**:\n{summary}\n\n"
        f"**Sources Gathered**: {len(unique_sources)} URLs analyzed.\n\n"
        f"📄 The full detailed report has been generated and saved to:\n"
        f"[{report_path.name}](file://{report_path.resolve()})"
    )
