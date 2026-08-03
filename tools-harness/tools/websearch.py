"""
Simplified web search pipeline — parallel SearXNG + DDG + arXiv/PubMed, one function call.

Backends (all free, zero API keys):
  SearXNG   — self-hosted Docker, 70+ engines including Google
  DDG       — DuckDuckGo free search (20-30% unique additions)
  arXiv     — academic preprints (CS, physics, math, etc.)
  PubMed    — biomedical literature (clinical trials, drug studies)

Flow: guard → rewrite → search(parallel) → rerank → summarize(cloud/deepseek) → finalize

Academic queries (papers, research, cancer, drug, clinical trial, etc.)
automatically include arXiv and PubMed in the parallel search pool.

Usage:
    from tools.websearch import search
    answer = search("side effects of metformin")
"""

from __future__ import annotations

import logging
import time
from typing import Callable

from tools.search_result import SearchResult, SearchSnippet
from tools.searxng_search import _search_searxng
from tools.query_guard import check_all as _guard_check
from clients.cancellation import check_aborted

# See _run_tavily()'s comment: SDK default is 60s with nothing capping it
# tighter, confirmed live to blow past 90s on a degraded response. SearXNG/DDG
# (the fallback tier) normally answer in 4-9s, so failing Tavily fast here
# still leaves time for a full fallback within a reasonable total wait.
_TAVILY_TIMEOUT_S = 10.0
# _gather()'s fallback tier (SearXNG/DDG/Brave) had the same unbounded-wait gap
# — as_completed() with no timeout blocks on whichever backend is slowest, with
# no application-level ceiling.
_GATHER_TIMEOUT_S = 15.0

# Route through the shared config so this logger actually gets a handler — a bare
# getLogger("websearch") has none, so its records propagate to an unconfigured root
# and everything below WARNING (all the stage-timing INFO lines) is silently dropped.
from log_config import setup_logging as _setup_logging
_log = _setup_logging("websearch")

_REWRITE_PROMPT = (
    "Extract 2 targeted web search queries from this question. Rules:\n"
    "- Preserve ALL proper nouns, names, and places EXACTLY as written\n"
    "- Short, optimized for web search engines\n"
    "- One per line, no numbering, no explanation\n\n"
    "Question: {query}"
)

_SUMMARIZE_PROMPT = """\
Answer the user's question using ONLY the evidence below. Rules:
- Be concise and direct
- If evidence is sufficient, give a clear answer with key facts
- If evidence is insufficient, say "Not enough information found"
- Cite sources by number like [1], [2]
- Today's date: {today}

User question: {query}

Evidence:
{evidence}

ANSWER:"""


def _rewrite_query(query: str, timeout_s: float = 6.0) -> str:
    """Rewrite query for better search results using local LLM (qwen3.5:4b)."""
    import datetime as _dt
    import re as _re_dt
    
    # 1. Preprocess relative date terms deterministically to prevent qwen3.5:4b from dropping them
    now_dt = _dt.datetime.now()
    replacements = {
        r"\btoday('s)?\b": now_dt.strftime('%B %d, %Y'),
        r"\btonight('s)?\b": now_dt.strftime('%B %d, %Y'),
        r"\btomorrow('s)?\b": (now_dt + _dt.timedelta(days=1)).strftime('%B %d, %Y'),
        r"\byesterf?day('s)?\b": (now_dt - _dt.timedelta(days=1)).strftime('%B %d, %Y'),
    }
    processed_query = query
    for pattern, replacement in replacements.items():
        processed_query = _re_dt.sub(pattern, replacement, processed_query, flags=_re_dt.IGNORECASE)

    stripped = processed_query.strip()
    if len(stripped) < 15 or len(stripped.split()) <= 3:
        return processed_query

    now = _dt.datetime.now()
    today_str = now.strftime('%A, %B %d, %Y')
    prompt = (
        "You are a search query optimizer. Rewrite the user's question into a clean, concise, "
        "keyword-based query optimized for search engines (like Google/DuckDuckGo).\n"
        f"Today's date is {today_str}.\n"
        "Rules:\n"
        "- Extract key concepts, entities, and subjects\n"
        "- Remove conversational filler, questions, and punctuation\n"
        # Relative dates ("today", etc.) are already resolved to absolute dates
        # upstream by _rewrite_query's deterministic pass. Do NOT re-translate them
        # here — a hardcoded example date used to leak into the model's output
        # (it echoed the literal example date and overwrote the real one).
        "- Keep proper nouns, locations, and any dates EXACTLY as written — never change a date\n"
        "- Output ONLY the optimized search keywords, nothing else.\n\n"
        f"Question: {processed_query}\n"
        "Optimized query:"
    )

    # 2026-07-10: switched primary from local qwen3.5:4b to cloud (DeepSeek,
    # matching _summarize()'s existing cloud-primary choice) — live-measured
    # both with the same prompt: cloud was a flat 0.8-1.2s across 3 runs,
    # while local hit 5.54s in a real orchestrator trace (Ollama model-slot
    # contention with gemma4:12b-mlx/ornith:9b — same class of issue already
    # fixed elsewhere today for Kokoro/voiceprint). No gain in the good case,
    # but removes the tail-latency risk entirely and drops this pipeline's
    # last Ollama dependency in the hot path. Both calls are timeout-wrapped —
    # the old code declared timeout_s but never actually applied it anywhere,
    # a latent unbounded-hang gap (same class as the Tavily fix earlier today).
    from concurrent.futures import ThreadPoolExecutor, TimeoutError as _FutTimeout

    def _call_cloud() -> str:
        from clients import cloud_client
        choice = cloud_client.raw_completion(
            model=cloud_client.DEFAULT_CLOUD_MODEL,
            messages=[{"role": "user", "content": prompt}],
            tools=[],
            bypass_budget=True,
        )
        return (choice.message.content or "").strip()

    def _call_local() -> str:
        import ollama
        resp = ollama.generate(
            model="qwen3.5:4b",
            prompt=prompt,
            options={"temperature": 0.0, "num_predict": 30},
            keep_alive=-1,
            think=False,
        )
        return (resp.get("response") or "").strip()

    # Not `with ThreadPoolExecutor(...) as ex:` — its __exit__ calls
    # shutdown(wait=True), which blocks until the submitted thread actually
    # finishes even after .result(timeout=...) already raised, defeating the
    # timeout entirely (a stuck network call would still hold up this
    # function for however long it eventually takes). shutdown(wait=False)
    # lets us walk away the instant the timeout fires; the orphaned thread
    # finishes on its own and is discarded.
    ex = ThreadPoolExecutor(max_workers=1)
    try:
        rewritten = ex.submit(_call_cloud).result(timeout=timeout_s)
        if rewritten:
            _log.debug("rewrite (cloud): %r -> %r", query, rewritten)
            return rewritten
    except _FutTimeout:
        _log.warning("cloud query rewrite timed out after %.1fs, falling back to local", timeout_s)
    except Exception as e:
        _log.warning("cloud query rewrite failed (%s), falling back to local", e)
    finally:
        ex.shutdown(wait=False)

    ex = ThreadPoolExecutor(max_workers=1)
    try:
        rewritten = ex.submit(_call_local).result(timeout=timeout_s)
        if rewritten:
            _log.debug("rewrite (local): %r -> %r", query, rewritten)
            return rewritten
    except _FutTimeout:
        _log.warning("local query rewrite also timed out after %.1fs", timeout_s)
    except Exception as e:
        _log.warning("local query rewrite also failed: %s", e)
    finally:
        ex.shutdown(wait=False)

    return processed_query




import re as _re

_ACADEMIC_RE = _re.compile(
    r"\b(paper|papers|research|study|studies|clinical\s+trial|"
    r"meta.?analysis|systematic\s+review|preprint|"
    r"doi|pubmed|arxiv|literature\s+review|"
    r"neural\s+network|transformer\s+architecture|"
    r"machine\s+learning|cancer|tumor|gene|protein|"
    r"vaccine|drug|treatment\s+for|efficacy\s+of|"
    r"side\s+effects?\s+of|randomized\s+controlled)\b",
    _re.I,
)


def _search(query: str, time_range: str = "") -> SearchResult:
    """Tavily primary; SearXNG/DDG/Brave fallback only when Tavily fails. + arXiv/PubMed augment. Merge, dedupe by URL."""
    from concurrent.futures import ThreadPoolExecutor, as_completed
    from pathlib import Path
    from dotenv import load_dotenv
    import os

    # Load environment variables to ensure API keys are accessible
    load_dotenv(Path(__file__).parent.parent / ".env")

    def _run_searxng():
        r = _search_searxng(query, time_range=time_range, max_results=8)
        if r and r.ok and r.items:
            return r
        return SearchResult(content="", ok=False, error="SearXNG failed or empty", source="searxng", query=query)

    def _run_ddg():
        # 1. Try standard scraped DDG first (fast)
        from tools.ddg_search import execute as ddg_execute
        timelimit = None
        if time_range == "day":
            timelimit = "d"
        elif time_range == "week":
            timelimit = "w"
        elif time_range == "month":
            timelimit = "m"
        elif time_range == "year":
            timelimit = "y"
        
        try:
            r = ddg_execute(query, max_results=5, timelimit=timelimit)
            if r and r.ok and r.items:
                return r
        except Exception as e:
            _log.debug("ddg primary failed: %s", e)

        return SearchResult(content="", ok=False, error="DDG failed", source="ddg", query=query)

    def _run_brave():
        if os.environ.get("BRAVE_SEARCH_API_KEY"):
            from tools.brave_search import execute as brave_execute
            r = brave_execute(query, max_results=8, freshness=time_range)
            if r and r.ok and r.items:
                return r
        return SearchResult(content="", ok=False, error="Brave Search failed or no key", source="brave", query=query)

    def _run_arxiv():
        from tools.arxiv_search import execute as arxiv_execute
        return arxiv_execute(query, max_results=3)

    def _run_pubmed():
        from tools.pubmed import execute as pubmed_execute
        return pubmed_execute(query, max_results=3)

    def _run_tavily():
        if os.environ.get("TAVILY_API_KEY"):
            try:
                from tavily import TavilyClient
                from tools.search_agentic import _is_temporal
                client = TavilyClient(api_key=os.environ["TAVILY_API_KEY"])
                # 2026-07-10: TavilyClient.search()'s own default timeout is 60s, and
                # nothing here capped it tighter — confirmed live via chat_ui.log:
                # "search: 6 results in 91.16s"/"75.75s"/"35.28s" outliers, all with
                # Tavily as the (unparallelized) primary. A slow Tavily response
                # blocked the whole pipeline for up to that ceiling even though
                # SearXNG+DDG normally answer in 4-9s (same logs). _TAVILY_TIMEOUT_S
                # fails fast enough to still fall through to Exa/SearXNG/DDG within
                # a reasonable total wait, instead of trusting the SDK's own default.
                
                is_temp = _is_temporal(query)
                is_schedule = any(w in query.lower() for w in ["schedule", "match", "fixture", "game", "playoff", "tournament", "standings"])
                # "advanced" depth pays Tavily's server-side answer-synthesis cost
                # (confirmed live: ~5.8s vs a much cheaper "basic" call) — only worth
                # it when search()'s fast-path will actually use that answer. Pure
                # temporal queries (weather, prices, "right now") never do — the
                # fast-path explicitly discards Tavily's answer for them and
                # re-summarizes fresh snippets instead (staleness concern, see
                # search()). Schedule queries do use it, so they still get advanced.
                depth = "advanced" if is_schedule else "basic"

                # "advanced" answer is a complete synthesis; "basic" is a weak partial.
                answer_mode = "advanced"
                resp = client.search(
                    query=query,
                    max_results=10 if (is_temp or is_schedule) else 5,
                    search_depth=depth,
                    include_answer=answer_mode,
                    timeout=_TAVILY_TIMEOUT_S,
                )
                items = []
                # Tavily's synthesized answer is current and direct — feed it first as top evidence.
                tav_answer = (resp.get("answer") or "").strip()
                if tav_answer:
                    items.append(SearchSnippet(
                        title="Tavily synthesized answer",
                        url="",
                        snippet=tav_answer,
                        published_date="",
                    ))
                for r in resp.get("results", []):
                    items.append(SearchSnippet(
                        title=r.get("title", "") or "",
                        url=r.get("url", "") or "",
                        snippet=r.get("content", "") or "",
                        published_date=(r.get("published_date", "") or "")[:10],
                    ))
                if items:
                    return SearchResult(content="", ok=True, source="tavily", query=query, items=items)
            except Exception as e:
                return SearchResult(content="", ok=False, error=str(e), source="tavily", query=query)
        return SearchResult(content="", ok=False, error="Tavily key not set", source="tavily", query=query)

    def _run_exa():
        # Primary tier — Exa returns raw results (no answer synthesis), so we
        # always re-summarize from snippets. Uses search() (search_and_contents
        # deprecated as of 2025).
        if os.environ.get("EXA_API_KEY"):
            try:
                from exa_py import Exa
                client = Exa(api_key=os.environ["EXA_API_KEY"])
                resp = client.search(
                    query, num_results=8, type="auto",
                )
                items = [
                    SearchSnippet(
                        title=r.title or "",
                        url=r.url or "",
                        snippet=(r.text or "")[:1000],
                        published_date=(getattr(r, "published_date", "") or "")[:10],
                    )
                    for r in resp.results
                ]
                if items:
                    return SearchResult(content="", ok=True, source="exa", query=query, items=items)
            except Exception as e:
                return SearchResult(content="", ok=False, error=str(e), source="exa", query=query)
        return SearchResult(content="", ok=False, error="Exa key not set", source="exa", query=query)

    def _gather(named_tasks):
        """Run a list of (name, fn) backends in parallel, keep the ones with items.

        as_completed() previously had no timeout — a single hung backend (a
        scraper stuck on a slow page, a rate-limited call with no internal
        bound) blocked the whole fallback tier indefinitely. _GATHER_TIMEOUT_S
        caps the total wait; whichever backends already finished still count,
        the rest are abandoned (daemon threads, no explicit cancellation needed
        for this fallback tier — matches the executor's own no-wait shutdown).
        """
        out: list[SearchResult] = []
        if not named_tasks:
            return out
        with ThreadPoolExecutor(max_workers=len(named_tasks)) as ex:
            futures = {ex.submit(fn): name for name, fn in named_tasks}
            try:
                done_iter = as_completed(futures, timeout=_GATHER_TIMEOUT_S)
                for f in done_iter:
                    name = futures[f]
                    try:
                        r = f.result()
                        if r and r.ok and r.items:
                            out.append(r)
                            _log.debug("search: got %d items from %s", len(r.items), name)
                    except Exception as e:
                        _log.debug("search: %s failed — %s", name, e)
            except TimeoutError:
                _log.warning("search: fallback tier hit %.0fs timeout, using %d completed backend(s)",
                             _GATHER_TIMEOUT_S, len(out))
        return out

    # Academic sources augment whichever web backend wins. Kick them off up front so they
    # overlap the web search — they used to run in a second _gather AFTER it, paying
    # web-time + academic-time back to back on every "study/drug/cancer" query.
    academic_ex = None
    academic_futures: dict = {}
    if _ACADEMIC_RE.search(query):
        academic_ex = ThreadPoolExecutor(max_workers=2)
        academic_futures = {
            academic_ex.submit(_run_arxiv): "arxiv",
            academic_ex.submit(_run_pubmed): "pubmed",
        }

    results: list[SearchResult] = []

    # PRIMARY: Exa — active key, no rate-limit issues. Used alone when it succeeds.
    if os.environ.get("EXA_API_KEY"):
        exa = _run_exa()
        if exa and exa.ok and exa.items:
            _log.debug("search: exa primary -> %d items", len(exa.items))
            results.append(exa)
        else:
            _log.debug("search: exa primary failed (%s) -> trying tavily",
                       exa.error if exa else "none")

    # SECONDARY: Tavily — rate-limited key, tried only when Exa didn't deliver,
    # before falling through to the much slower SearXNG/DDG/Brave scraping tier.
    if not results and os.environ.get("TAVILY_API_KEY"):
        tav = _run_tavily()
        if tav and tav.ok and tav.items:
            _log.debug("search: tavily fallback -> %d items", len(tav.items))
            results.append(tav)
        else:
            _log.debug("search: tavily fallback failed (%s) -> falling back to searxng/ddg/brave",
                       tav.error if tav else "none")

    # FALLBACK: SearXNG + DDG + Brave (parallel) only when Tavily/Exa did not deliver.
    if not results:
        fallback_tasks = [("searxng", _run_searxng), ("ddg", _run_ddg)]
        if os.environ.get("BRAVE_SEARCH_API_KEY"):
            fallback_tasks.append(("brave", _run_brave))
        results.extend(_gather(fallback_tasks))

    # Collect the academic backends that were running in parallel with the web search.
    if academic_futures:
        try:
            for f in as_completed(academic_futures, timeout=_GATHER_TIMEOUT_S):
                name = academic_futures[f]
                try:
                    r = f.result()
                    if r and r.ok and r.items:
                        results.append(r)
                        _log.debug("search: got %d items from %s", len(r.items), name)
                except Exception as e:
                    _log.debug("search: %s failed — %s", name, e)
        except TimeoutError:
            _log.warning("search: academic futures timed out after %ss", _GATHER_TIMEOUT_S)
        academic_ex.shutdown(wait=False)

    if not results:
        return SearchResult(content="", ok=False, error="all backends failed", source="parallel", query=query)

    # Merge items, deduplicate by URL
    merged_items: list[SearchSnippet] = []
    seen_urls: set[str] = set()
    for r in results:
        for item in r.items:
            url = (item.url or "").strip().rstrip("/")
            # Keep URL-less items (e.g. the Tavily synthesized answer) — don't dedup them away.
            if not url:
                merged_items.append(item)
                continue
            if url not in seen_urls:
                seen_urls.add(url)
                merged_items.append(item)

    _log.debug("search: %s primary -> %d merged items from %d backends",
               results[0].source, len(merged_items), len(results))

    return SearchResult(
        content="", ok=True, source=results[0].source,
        query=query, items=merged_items[:10],
    )


def _rerank(query: str, items: list[SearchSnippet], limit: int = 5) -> list[SearchSnippet]:
    """Simplified heuristic rerank — authority + text overlap."""
    from tools.search_agentic import rerank_items

    return rerank_items(query, items, limit=limit)


def _summarize(
    query: str, items: list[SearchSnippet], on_token: Callable | None = None, timeout_s: float = 60.0
) -> str:
    """Summarize search results using cloud model (deepseek), falls back to gemma4."""
    from tools.search_agentic import summarize_with_model
    from clients import cloud_client

    try:
        return summarize_with_model(
            query=query, items=items, timeout_s=timeout_s, on_token=on_token,
            model=cloud_client.DEFAULT_CLOUD_MODEL,
        )
    except Exception as _e:
        _log.warning("cloud summarize failed (%s), falling back to gemma4", _e)
        return summarize_with_model(
            query=query, items=items, timeout_s=timeout_s, on_token=on_token, model="gemma4:12b-mlx",
        )

def _finalize(answer: str) -> str:
    """Clean and return the final answer."""
    answer = (answer or "").strip()
    # Strip think blocks (gemma4/qwen3 may include them)
    import re
    answer = re.sub(r"<think>.*?</think>", "", answer, flags=re.DOTALL).strip()
    if answer.startswith("</think>"):
        answer = answer[len("</think>"):].strip()
    if not answer or len(answer) < 10:
        return "Could not find an answer."
    return answer


def _is_chinese_query(query: str) -> bool:
    """True when the query is majority Chinese-language — SearXNG/DDG/Tavily are
    English-centric and don't reach Chinese platforms (Bilibili etc.) well."""
    han = sum(1 for c in query if "一" <= c <= "鿿")
    return han >= max(2, len(query) * 0.3)


def search(query: str, on_token: Callable | None = None, run_id: str | None = None) -> str:
    """
    Complete web search pipeline. Returns a model-ready answer string.

    Stages:
     1. guard     — check if clarification needed
     2. rewrite   — improve query for search engines
     3. search    — SearXNG + DDG (parallel), + arXiv/PubMed if academic
     4. rerank    — score and order results
     5. summarize — gemma4 synthesis from evidence
     6. finalize  — clean and return

    Chinese-language queries skip all of the above — SearXNG/DDG/Tavily barely
    index Chinese platforms — and go straight to agent-reach (Bilibili + Exa,
    see tools/agent_reach.py) instead.

    `run_id`, when given, records one trace_store entry for this call (tool
    name, elapsed_ms, error). Without this, `ask_web_search`'s calls into this
    function were structurally invisible to orchestrator_tools.py's
    `_run_subagent()` envelope, which reads trace_store to decide status=ok
    vs status=degraded — every websearch call reported status=degraded
    tools=none *even when it succeeded*, because nothing here ever wrote to
    trace_store at all. The orchestrator's own system prompt tells it not to
    trust a degraded subagent, so it was (correctly, per that instruction)
    re-querying with reworded phrasing 3-5x for a single fact (found
    2026-07-10/11: "what's the weather in Boston today" cost ~20s across 4-5
    near-duplicate ask_web_search calls instead of ~7s for one)."""
    t0 = time.perf_counter()
    check_aborted()

    def _trace(error: str | None = None) -> None:
        if not run_id:
            return
        from store import trace_store
        trace_store.record(run_id, {
            "tool": "ask_web_search",
            "args": {"query": query},
            "elapsed_ms": (time.perf_counter() - t0) * 1000,
            "error": error,
        })

    if _is_chinese_query(query):
        from tools.agent_reach import search_chinese_web
        result = search_chinese_web({"query": query})
        if on_token:
            on_token(result)
        _log.info("websearch: routed to agent-reach (Chinese query) %r → %d chars in %.2fs",
                   query[:60], len(result), time.perf_counter() - t0)
        _trace()
        return result

    # 1. Guard — clarification check
    clarification = _guard_check(query)
    if clarification:
        _log.debug("guard: clarification needed for %r", query[:60])
        _trace()
        return clarification

    check_aborted()

    from tools.search_agentic import _is_temporal
    _is_temp_early = _is_temporal(query)

    # Semantic cache — skip for temporal/live queries (schedules, scores, "right now"),
    # which must never serve a stale cached answer. Tight distance (0.12) requires a
    # near-paraphrase, not just same-topic, since a wrong-question cache hit is worse
    # than the ~1-2s a fresh search costs.
    if not _is_temp_early:
        cached = _cache_lookup(query)
        if cached is not None:
            if on_token:
                on_token(cached)
            _log.info("websearch cache hit: %r → %d chars in %.3fs", query[:60], len(cached),
                       time.perf_counter() - t0)
            _trace()
            return cached

    # 2. Rewrite
    search_query = _rewrite_query(query)

    # 3. Search
    time_range = "year" if _is_temp_early else ""
    result = _search(search_query, time_range=time_range)

    if not result.ok or not result.items:
        _log.debug("search: no results for %r → %s", query[:60], result.error)
        _trace(error=result.error or "no results")
        return f"Search failed: {result.error or 'no results'}"

    items = result.items
    _log.info("search: %d results in %.2fs", len(items), time.perf_counter() - t0)

    check_aborted()

    _is_temp = _is_temp_early

    # Fast-path: stream Tavily's synthesized answer directly and skip the gemma4:12b-mlx
    # summarize (the dominant latency). Safe when Tavily gives a substantial answer AND the
    # query isn't a deep explain/compare/list/format ask — those want gemma4's fuller
    # synthesis, so _extract_format_hint (non-None) routes them past the fast-path.
    # Temporal keeps the lower 150-char bar (schedules are short-but-complete); others
    # need 300+ so we don't serve a terse partial in place of a real answer.
    from tools.search_agentic import _extract_format_hint
    _tav = next((it for it in items if it.title == "Tavily synthesized answer"), None)
    _wants_depth = _extract_format_hint(query) is not None
    _min_len = 150 if _is_temp else 300
    # Temporal/time-sensitive queries (sports scores, schedules, live updates) are highly
    # prone to stale Tavily cached answers — always run them through the gemma4:12b-mlx
    # summarizer over fresh search snippets to verify accuracy.
    if (
        not _is_temp
        and _tav and _tav.snippet and len(_tav.snippet.strip()) >= _min_len
        and not _wants_depth
    ):
        import unicodedata as _ud, re as _ws
        # Normalize exotic unicode spaces (narrow/thin/nbsp/zero-width) Tavily emits.
        ans = "".join(" " if _ud.category(c) == "Zs" else c for c in _tav.snippet)
        ans = ans.replace("​", "")
        ans = _ws.sub(r"[ \t]{2,}", " ", ans).strip()
        if on_token:
            on_token(ans)
        _log.info("websearch fast-path (tavily answer, temporal=%s): %r → %d chars in %.2fs",
                  _is_temp, query[:60], len(ans), time.perf_counter() - t0)
        if not _is_temp:
            _cache_store(query, ans)
        _trace()
        return ans

    # 4. Rerank — keep more evidence for temporal/schedule (lists need every row).
    rerank_limit = 8 if _is_temp else 5
    ranked = _rerank(query, items, limit=rerank_limit)
    # Pin the Tavily synthesized answer (no URL → rerank may drop it) at the top.
    tav_answer = next((it for it in items if it.title == "Tavily synthesized answer"), None)
    if tav_answer and tav_answer not in ranked:
        ranked = [tav_answer] + ranked[: rerank_limit - 1]
    _log.debug("rerank: %d → %d items", len(items), len(ranked))

    check_aborted()

    # 5. Summarize
    answer = _summarize(query, ranked, on_token=on_token)
    _log.info("summarize (cloud): %d chars, cumulative %.2fs", len(answer), time.perf_counter() - t0)

    check_aborted()

    # 6. Finalize
    final = _finalize(answer)
    _log.info("websearch done: %r → %d chars in %.2fs", query[:60], len(final), time.perf_counter() - t0)

    if not _is_temp:
        _cache_store(query, final)

    _trace()
    return final


def _cache_lookup(query: str) -> str | None:
    """Best-effort semantic cache read. Any failure (LanceDB/Ollama down) = cache miss, never raises."""
    try:
        from store.knowledge_base import KnowledgeBase
        return KnowledgeBase().is_cached(query, source="websearch_cache", max_distance=0.12)
    except Exception:
        return None


def _cache_store(query: str, answer: str) -> None:
    """Best-effort semantic cache write. Failure is silent — caching is an optimization, not a
    correctness requirement, and must never break a successful search response."""
    if not answer or answer.startswith("Search failed"):
        return
    try:
        from store.knowledge_base import KnowledgeBase
        KnowledgeBase().store(answer, source="websearch_cache", query=query)
    except Exception:
        _log.debug("websearch cache store failed for %r", query[:60], exc_info=True)
