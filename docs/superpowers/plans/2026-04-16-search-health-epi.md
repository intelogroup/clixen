# Search: Health/Epi Query Fix Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Fix the search graph so health epidemiology queries (COVID prevalence, case counts, outbreak stats) route to authoritative sources (CDC, WHO, OWID, disease.sh) instead of dead-ending with a "no relevant information" error.

**Architecture:** Five independent but chained fixes. Tasks 1–3 are additive config/routing changes. Task 4 adds the new `EpiAdapter` discovery class. Task 5 converts the hard dead-end in `summarize_node` to a retryable failure so the existing `verify_node` retry mechanism kicks in. Together they form a complete path: DDG no longer short-circuits → discovery runs → EpiAdapter fetches live stats → authority boost ranks them up → summarizer gets real evidence.

**Tech Stack:** Python, LangGraph, `tools/search_agentic.py`, `agents/discovery.py`, `agents/search_graph.py`, Playwright (already wired in `tools/fetch_url.py`), `disease.sh` REST API.

## Current Task Checklist

- [ ] Task 1: Health domain authority + allow boost
- [ ] Task 2: DDG race coverage guard
- [ ] Task 3: Query rewrite - lower threshold + health keyword condensing
- [ ] Task 4: EpiAdapter - epidemiology discovery adapter
- [ ] Task 5: `summarize_node` evidence-fail -> retry path
- [ ] Task 6: End-to-end smoke test

---

## Background / Diagnosis

Running `run_search_graph("is covid19 still around? what are the latest prevalence data?", "recent", "health")` fails with:

```
"The search results don't contain relevant information to answer..."
Sources: Wikipedia, AAMC variants article
```

Root causes:

| # | Where | Bug |
|---|-------|-----|
| A | `search_graph.py:616` | `_DDG_RACE_MIN_ITEMS = 2` — DDG returns 2 background articles, satisfies threshold, cancels discovery |
| B | `search_agentic.py:190` | No `"health"` key in `_DOMAIN_ALLOW_BOOST` — CDC/WHO/OWID get no ranking boost |
| C | `search_agentic.py:127` | `ourworldindata.org` and `disease.sh` absent from `_HIGH_AUTHORITY_DOMAINS` |
| D | `discovery.py:1386` | `HealthAdapter` only handles supplement queries (turmeric, vitamin D); generates PubMed + Wikipedia URLs for all health queries including epi |
| E | `search_graph.py:1008` | `_evidence_covers_query` failure returns hard user-facing error instead of triggering `verify_node` retry |
| F | `search_graph.py:_rewrite_queries` | Model-rewrite threshold `> 12 words` skips 10-word queries; no keyword distillation for health |

---

## File Map

| File | Changes |
|------|---------|
| `tools-harness/tools/search_agentic.py` | Add `"health"` to `_DOMAIN_ALLOW_BOOST`; add OWID + disease.sh to `_HIGH_AUTHORITY_DOMAINS` |
| `tools-harness/agents/search_graph.py` | Raise `_DDG_RACE_MIN_ITEMS`; add coverage check to `_ddg_quality_ok`; lower rewrite threshold; fix evidence-fail path |
| `tools-harness/agents/discovery.py` | Add `EpiAdapter` class; wire it into `discovery_router_node` |
| `tools-harness/tests/test_search_evals.py` | New test: EpiAdapter returns disease.sh URL for covid prevalence query |
| `tools-harness/tests/test_search_graph_advanced.py` | New test: DDG 2-item race does NOT short-circuit when coverage check fails |

---

## Task 1: Health domain authority + allow boost

**Files:**
- Modify: `tools-harness/tools/search_agentic.py:127-145` (`_HIGH_AUTHORITY_DOMAINS`)
- Modify: `tools-harness/tools/search_agentic.py:190-195` (`_DOMAIN_ALLOW_BOOST`)
- Test: `tools-harness/tests/test_search_evals.py`

- [ ] **Step 1: Write failing test**

```python
# In tools-harness/tests/test_search_evals.py — add at end:

def test_health_domain_allow_boost_ourworldindata():
    """ourworldindata.org should get a boost in health domain."""
    from tools.search_agentic import _domain_allow_score
    from tools.search_result import SearchSnippet
    item = SearchSnippet(url="https://ourworldindata.org/covid-cases", title="COVID cases", snippet="x")
    assert _domain_allow_score(item, "health") > 0.0


def test_disease_sh_in_high_authority():
    """disease.sh should score as high authority."""
    from tools.search_agentic import _authority_score
    from tools.search_result import SearchSnippet
    item = SearchSnippet(url="https://disease.sh/v3/covid-19/all", title="COVID stats", snippet="x")
    assert _authority_score(item) >= 2.0
```

- [ ] **Step 2: Run to verify failure**

```bash
cd tools-harness && python -m pytest tests/test_search_evals.py::test_health_domain_allow_boost_ourworldindata tests/test_search_evals.py::test_disease_sh_in_high_authority -v
```

Expected: `FAILED` — health key missing from `_DOMAIN_ALLOW_BOOST`, `disease.sh` not in authority set.

- [ ] **Step 3: Add health allow list + authority entries**

In `tools-harness/tools/search_agentic.py`, find `_HIGH_AUTHORITY_DOMAINS = frozenset({` (line ~127). Add two entries:

```python
    "ourworldindata.org",                          # Epidemiology/global health data
    "disease.sh",                                  # Live disease stats REST API
```

In the same file, find `_DOMAIN_ALLOW_BOOST: dict[str, frozenset] = {` (line ~190). Add health entry after the `"code"` line:

```python
    "health": frozenset({
        "cdc.gov", "who.int", "nih.gov",
        "ourworldindata.org", "disease.sh",
        "nejm.org", "bmj.com", "thelancet.com",
        "pubmed.ncbi.nlm.nih.gov",
    }),
```

- [ ] **Step 4: Run tests to verify pass**

```bash
cd tools-harness && python -m pytest tests/test_search_evals.py::test_health_domain_allow_boost_ourworldindata tests/test_search_evals.py::test_disease_sh_in_high_authority -v
```

Expected: `PASSED`.

- [ ] **Step 5: Commit**

```bash
git add tools-harness/tools/search_agentic.py tools-harness/tests/test_search_evals.py
git commit -m "feat(search): add health domain allow list + OWID/disease.sh authority boost"
```

---

## Task 2: DDG race coverage guard

**Files:**
- Modify: `tools-harness/agents/search_graph.py:616-619` (`_DDG_RACE_MIN_ITEMS` + `_ddg_quality_ok`)
- Test: `tools-harness/tests/test_search_graph_advanced.py`

Context: `_ddg_quality_ok` is a nested function inside `parallel_search_node`. It has closure access to `state`. `_evidence_covers_query` is a module-level function in the same file.

- [ ] **Step 1: Write failing test**

```python
# In tools-harness/tests/test_search_graph_advanced.py — add at end:

def test_ddg_race_does_not_short_circuit_when_2_off_topic_items(monkeypatch):
    """
    DDG returning exactly 2 items about background history should NOT win the race
    and cancel discovery when those items don't cover the query tokens.
    """
    from tools.search_result import SearchResult, SearchSnippet

    off_topic_items = [
        SearchSnippet(url="https://en.wikipedia.org/wiki/COVID-19_pandemic",
                      title="COVID-19 pandemic - Wikipedia",
                      snippet="and 19 for when the outbreak was first identified (31 December 2019)."),
        SearchSnippet(url="https://www.aamc.org/news/covid-19-variants",
                      title="COVID-19 variants are spreading",
                      snippet="SARS-CoV-2, the virus at the root of the COVID-19 pandemic, has mutated"),
    ]
    ddg_result = SearchResult(content="", ok=True, source="ddg",
                               query="covid19 prevalence", items=off_topic_items)

    # Simulate _ddg_quality_ok with state that has a coverage-requiring query
    # We test the inner function by calling parallel_search_node with mocked backends
    # and verifying discovery was NOT cancelled (i.e. discovery result is not "cancelled_race")
    discovery_called = []

    def mock_ddg(query):
        return ddg_result

    def mock_discovery_search(state):
        discovery_called.append(True)
        return SearchResult(content="COVID-19 prevalence data 2026", ok=True,
                            source="discovery", query=state.get("current_query", ""),
                            items=[SearchSnippet(url="https://disease.sh/v3/covid-19/all",
                                                 title="COVID-19 Stats",
                                                 snippet="cases: 700M deaths: 7M prevalence data 2026")])

    import agents.search_graph as sg
    monkeypatch.setattr(sg, "_ddg", mock_ddg)
    monkeypatch.setattr("agents.discovery.discovery_router_node", lambda state, runtime=None: {
        "candidate_urls": ["https://disease.sh/v3/covid-19/all"],
        "discovery_quality": "accepted",
        "raw_result": {"content": "prevalence data", "ok": True, "source": "discovery",
                       "query": "covid prevalence", "items": []},
    })

    from agents.search_state import SearchState
    state: SearchState = {
        "query": "is covid19 still around? what are the latest prevalence data?",
        "current_query": "covid19 prevalence data 2026",
        "intent": "recent",
        "domain": "health",
        "rewritten_queries": ["covid19 prevalence data 2026"],
        "retry_count": 0,
    }
    result_state = sg.parallel_search_node(state)

    disc_result = result_state.get("discovery_search_result", {})
    # Discovery must NOT be "cancelled_race" — it should have run
    assert disc_result.get("error") != "cancelled_race", (
        "Discovery was cancelled despite DDG items not covering the query"
    )
```

- [ ] **Step 2: Run to verify failure**

```bash
cd tools-harness && python -m pytest tests/test_search_graph_advanced.py::test_ddg_race_does_not_short_circuit_when_2_off_topic_items -v
```

Expected: `FAILED` — discovery is cancelled because `_ddg_quality_ok` only checks `len >= 2`.

- [ ] **Step 3: Apply the two-part fix**

In `tools-harness/agents/search_graph.py`, change line 616 from:

```python
    _DDG_RACE_MIN_ITEMS = 2
```

to:

```python
    _DDG_RACE_MIN_ITEMS = 4
```

Change lines 618-619 from:

```python
    def _ddg_quality_ok(result) -> bool:
        return result.ok and len(result.items or []) >= _DDG_RACE_MIN_ITEMS
```

to:

```python
    def _ddg_quality_ok(result) -> bool:
        if not result.ok or len(result.items or []) < _DDG_RACE_MIN_ITEMS:
            return False
        # Also require the items actually cover the query — prevents stale background
        # articles (Wikipedia, AAMC history pieces) from cancelling the discovery path.
        return _evidence_covers_query(state["current_query"], result.items or [])
```

- [ ] **Step 4: Run test to verify pass**

```bash
cd tools-harness && python -m pytest tests/test_search_graph_advanced.py::test_ddg_race_does_not_short_circuit_when_2_off_topic_items -v
```

Expected: `PASSED`.

- [ ] **Step 5: Run existing parallel search tests to check no regressions**

```bash
cd tools-harness && python -m pytest tests/test_search_graph_advanced.py -v -x 2>&1 | tail -20
```

Expected: all existing tests pass.

- [ ] **Step 6: Commit**

```bash
git add tools-harness/agents/search_graph.py tools-harness/tests/test_search_graph_advanced.py
git commit -m "fix(search): DDG race requires coverage check + min 4 items before cancelling discovery"
```

---

## Task 3: Query rewrite — lower threshold + health keyword condensing

**Files:**
- Modify: `tools-harness/agents/search_graph.py` (`_rewrite_queries` function, ~line 172–230)
- Test: `tools-harness/tests/test_search_graph_advanced.py`

Context: `_rewrite_queries` currently only triggers model-rewrite for queries `> 12 words`. The COVID query has 10 words. Additionally there is no keyword-condensed variant for health queries.

- [ ] **Step 1: Write failing test**

```python
# In tools-harness/tests/test_search_graph_advanced.py — add at end:

def test_rewrite_queries_health_short_query_gets_keyword_variant():
    """
    A 10-word health query should produce a keyword-condensed variant
    without the full question sentence.
    """
    from agents.search_graph import _rewrite_queries
    rewrites = _rewrite_queries(
        "is covid19 still around? what are the latest prevalence data?",
        "recent",
        "health",
    )
    # At least one rewrite should be short keyword form (no "is...still...around")
    keyword_variants = [r for r in rewrites if "is covid19 still" not in r and len(r.split()) <= 8]
    assert keyword_variants, (
        f"Expected a short keyword rewrite, got: {rewrites}"
    )


def test_rewrite_queries_health_includes_authority_site_hint():
    """Health + recent queries should include a CDC/WHO site-anchored variant."""
    from agents.search_graph import _rewrite_queries
    rewrites = _rewrite_queries(
        "is covid19 still around? what are the latest prevalence data?",
        "recent",
        "health",
    )
    site_variants = [r for r in rewrites if "cdc.gov" in r or "who.int" in r or "ourworldindata" in r]
    assert site_variants, f"Expected a site-constrained rewrite, got: {rewrites}"
```

- [ ] **Step 2: Run to verify failure**

```bash
cd tools-harness && python -m pytest tests/test_search_graph_advanced.py::test_rewrite_queries_health_short_query_gets_keyword_variant tests/test_search_graph_advanced.py::test_rewrite_queries_health_includes_authority_site_hint -v
```

Expected: `FAILED`.

- [ ] **Step 3: Apply fix in `_rewrite_queries`**

In `tools-harness/agents/search_graph.py`, find the block starting with `keywords = [w for w in q.split() if w.lower() not in _CONDENSING_STOPWORDS]` and the following `if len(q.split()) > 12` check. Change:

```python
    if len(q.split()) > 12 and len(keywords) >= 4 and domain != "pricing":
```

to:

```python
    if len(q.split()) > 7 and len(keywords) >= 4 and domain != "pricing":
```

Then, just before the `if site_constraint:` block at the bottom of `_rewrite_queries`, add the health-specific variant:

```python
    # Health + temporal: add keyword-condensed variant + authority site anchor.
    # CDC/WHO/OWID return current statistics; the site hint surfaces them in DDG.
    if domain == "health" and needs_freshness:
        kws = [w for w in q.split() if w.lower() not in _CONDENSING_STOPWORDS]
        kw_form = " ".join(kws[:6])
        if kw_form:
            rewrites.append(kw_form)
            rewrites.append(f"{kw_form} site:cdc.gov OR site:who.int OR site:ourworldindata.org")
```

- [ ] **Step 4: Run tests to verify pass**

```bash
cd tools-harness && python -m pytest tests/test_search_graph_advanced.py::test_rewrite_queries_health_short_query_gets_keyword_variant tests/test_search_graph_advanced.py::test_rewrite_queries_health_includes_authority_site_hint -v
```

Expected: `PASSED`.

- [ ] **Step 5: Run full rewrite test suite**

```bash
cd tools-harness && python -m pytest test_search_graph.py tests/test_search_graph_advanced.py -v -x 2>&1 | tail -20
```

Expected: all pass.

- [ ] **Step 6: Commit**

```bash
git add tools-harness/agents/search_graph.py tools-harness/tests/test_search_graph_advanced.py
git commit -m "fix(search): lower rewrite threshold to >7 words + add health/temporal keyword+site variants"
```

---

## Task 4: EpiAdapter — epidemiology discovery adapter

**Files:**
- Modify: `tools-harness/agents/discovery.py` (add class after `HealthAdapter`, wire into `_ADAPTERS` + `discovery_router_node`)
- Test: `tools-harness/tests/test_search_evals.py`

Context: `HealthAdapter` only handles supplement queries (PubMed + Wikipedia). `EpiAdapter` targets live epidemiology data. The key source is `disease.sh` — a pure JSON REST API that returns `{"cases": 704M, "deaths": 7M, "active": 6.2M, ...}` with no JS rendering needed. CDC and OWID pages are JS-rendered and use the existing Playwright fallback in `tools/fetch_url.py`. The adapter is triggered when `domain == "health"` AND the query contains epidemiology keywords.

- [ ] **Step 1: Write failing test**

```python
# In tools-harness/tests/test_search_evals.py — add at end:

def test_epi_adapter_returns_disease_sh_for_covid_prevalence():
    """EpiAdapter should include disease.sh and WHO for COVID prevalence queries."""
    from agents.discovery import EpiAdapter
    candidates = EpiAdapter.get_candidates(
        "is covid19 still around? what are the latest prevalence data?"
    )
    urls = " ".join(candidates)
    assert "disease.sh" in urls, f"Expected disease.sh in candidates, got: {candidates}"
    assert "who.int" in urls or "cdc.gov" in urls, (
        f"Expected WHO or CDC in candidates, got: {candidates}"
    )


def test_epi_adapter_returns_disease_sh_for_flu_prevalence():
    """EpiAdapter should adapt to flu queries."""
    from agents.discovery import EpiAdapter
    candidates = EpiAdapter.get_candidates("flu prevalence statistics this year")
    urls = " ".join(candidates)
    assert "disease.sh" in urls or "cdc.gov" in urls, (
        f"Expected epi source in flu candidates, got: {candidates}"
    )


def test_discovery_router_routes_epi_query_to_epi_adapter():
    """discovery_router_node should select EpiAdapter for covid prevalence queries."""
    from agents.discovery import discovery_router_node, EpiAdapter
    state = {
        "query": "is covid19 still around? what are the latest prevalence data?",
        "current_query": "covid19 prevalence data 2026",
        "query_domain": "health",
        "intent": "recent",
    }
    # Just check candidate_urls include disease.sh or CDC
    result = discovery_router_node(state)
    candidates = result.get("candidate_urls", [])
    urls = " ".join(candidates)
    assert "disease.sh" in urls or "cdc.gov" in urls, (
        f"Expected epi URL in discovery candidates, got: {candidates}"
    )
```

- [ ] **Step 2: Run to verify failure**

```bash
cd tools-harness && python -m pytest tests/test_search_evals.py::test_epi_adapter_returns_disease_sh_for_covid_prevalence tests/test_search_evals.py::test_epi_adapter_returns_disease_sh_for_flu_prevalence tests/test_search_evals.py::test_discovery_router_routes_epi_query_to_epi_adapter -v
```

Expected: `FAILED` — `EpiAdapter` does not exist.

- [ ] **Step 3: Add `EpiAdapter` class to `discovery.py`**

In `tools-harness/agents/discovery.py`, add this class directly after the `HealthAdapter` class (after its closing brace, before `class WeatherAdapter:`):

```python
_EPI_RE = re.compile(
    r"\b(prevalence|incidence|cases?|deaths?|mortality|morbidity|"
    r"statistics|outbreak|epidemic|pandemic|spread|infection\s*rate|"
    r"case\s*count|hospitalization|vaccination\s*rate|active\s*cases?)\b",
    re.I,
)

# Disease name → disease.sh API slug
_DISEASE_SLUG = {
    "covid": "covid-19",
    "covid-19": "covid-19",
    "covid19": "covid-19",
    "coronavirus": "covid-19",
    "sars-cov-2": "covid-19",
    "flu": "influenza",
    "influenza": "influenza",
    "mpox": "monkeypox",
    "monkeypox": "monkeypox",
    "ebola": "ebola",
    "dengue": "dengue",
}


def _detect_disease(query: str) -> str | None:
    """Return the disease.sh slug if a known disease is mentioned, else None."""
    q_lower = query.lower()
    for term, slug in _DISEASE_SLUG.items():
        if term in q_lower:
            return slug
    return None


class EpiAdapter:
    """
    Discovery adapter for epidemiology / disease prevalence queries.

    Targets live data sources:
      1. disease.sh REST API — plain JSON, no JS required, returns global stats
      2. WHO situation report / emergencies page — HTML, directly fetchable
      3. CDC data research index — HTML, directly fetchable (JS pages use Playwright fallback)
      4. Our World in Data — JS-rendered, uses Playwright fallback in fetch_url

    Triggered when domain == "health" AND query matches _EPI_RE.
    """

    @staticmethod
    def get_candidates(query: str) -> list[str]:
        from urllib.parse import quote as _quote

        slug = _detect_disease(query)
        enc_query = _quote(query)
        candidates = []

        if slug:
            # 1. disease.sh live stats for this disease (JSON, no JS)
            candidates.append(f"https://disease.sh/v3/{slug}/all")
            # 2. disease.sh historical 30-day data
            candidates.append(f"https://disease.sh/v3/{slug}/historical/all?lastdays=30")

        if not slug or slug == "covid-19":
            # WHO COVID situation
            candidates.append(
                "https://www.who.int/emergencies/diseases/novel-coronavirus-2019"
            )
            # CDC COVID data research index (HTML, fetchable)
            candidates.append("https://www.cdc.gov/covid/data-research/index.html")
            # Our World in Data (JS-rendered — Playwright fallback handles this)
            candidates.append("https://ourworldindata.org/covid-cases")
        else:
            # Generic WHO search for other diseases
            candidates.append(
                f"https://www.who.int/health-topics/{slug.replace(' ', '-')}"
            )
            candidates.append(
                f"https://www.cdc.gov/search/?query={enc_query}"
            )

        return candidates
```

- [ ] **Step 4: Wire `EpiAdapter` into `_ADAPTERS` and `discovery_router_node`**

In `discovery.py`, find `_ADAPTERS = {` (line ~1652). Add an entry:

```python
    "epi": EpiAdapter,
```

Find `discovery_router_node` (line ~1676). At the start of the function body, before the existing `domain = state.get("query_domain", "general")` block, add epidemiology pre-check:

```python
def discovery_router_node(state: SearchState, runtime=None) -> SearchState:
    """Route query to appropriate domain adapter based on classified domain or semantic intent."""
    domain = state.get("query_domain", "general")
    query = state.get("query", "")

    # Epidemiology override: health domain + prevalence/cases/outbreak keywords → EpiAdapter
    # Must run before the generic health → HealthAdapter fallthrough.
    if domain == "health" and _EPI_RE.search(query):
        adapter_class = EpiAdapter
    else:
        # ... existing logic below (no change to rest of function)
```

Important: the `else:` wraps the rest of the function body so the existing adapter selection logic still runs for non-epi health queries. The existing function already has a `# Check for preferred adapter from semantic intent first` block — that block becomes the `else` branch.

In practice, the edit is: insert the `if domain == "health" and _EPI_RE.search(query): adapter_class = EpiAdapter` block and an `else:` wrapping the rest. The full function shape:

```python
def discovery_router_node(state: SearchState, runtime=None) -> SearchState:
    """Route query to appropriate domain adapter based on classified domain or semantic intent."""
    domain = state.get("query_domain", "general")
    query = state.get("query", "")

    # Epidemiology override
    if domain == "health" and _EPI_RE.search(query):
        adapter_class = EpiAdapter
        candidates = adapter_class.get_candidates(query)
        if not candidates:
            return {"candidate_urls": [], "discovery_quality": "empty"}
        return {
            "candidate_urls": candidates,
            "discovery_quality": "accepted",
            "query_domain": domain,
        }

    # ... rest of existing function body unchanged ...
```

This approach is cleaner than wrapping in `else` — early return for epi, fall through to existing logic otherwise.

- [ ] **Step 5: Run tests to verify pass**

```bash
cd tools-harness && python -m pytest tests/test_search_evals.py::test_epi_adapter_returns_disease_sh_for_covid_prevalence tests/test_search_evals.py::test_epi_adapter_returns_disease_sh_for_flu_prevalence tests/test_search_evals.py::test_discovery_router_routes_epi_query_to_epi_adapter -v
```

Expected: `PASSED`.

- [ ] **Step 6: Run discovery tests**

```bash
cd tools-harness && python -m pytest tests/ -k "discovery or epi or health" -v 2>&1 | tail -30
```

Expected: all pass.

- [ ] **Step 7: Commit**

```bash
git add tools-harness/agents/discovery.py tools-harness/tests/test_search_evals.py
git commit -m "feat(search): add EpiAdapter for live epidemiology queries (disease.sh, CDC, WHO, OWID)"
```

---

## Task 5: `summarize_node` evidence-fail → retry path

**Files:**
- Modify: `tools-harness/agents/search_graph.py:1008-1015` (`summarize_node`)
- Test: `tools-harness/tests/test_search_graph_advanced.py`

Context: When `_evidence_covers_query` returns `False`, `summarize_node` currently returns a hard user-facing error string. The `verify_node` at lines 1054–1064 already has a retry mechanism: if `state["search_ok"] == False` and `retry_count < 1`, it picks `rewritten_queries[1]` and re-routes to `parallel_search`. We just need `summarize_node` to set `search_ok=False` instead of hard-failing, so `verify_node`'s existing path kicks in. On the second pass, Tasks 1–4 ensure better results arrive.

- [ ] **Step 1: Write failing test**

```python
# In tools-harness/tests/test_search_graph_advanced.py — add at end:

def test_summarize_node_evidence_fail_sets_search_ok_false(monkeypatch):
    """
    When _evidence_covers_query returns False, summarize_node must set
    search_ok=False so verify_node can trigger a retry — not return a
    hard user-facing error string.
    """
    from agents.search_graph import summarize_node
    from tools.search_result import SearchSnippet

    # Items that do NOT cover the query tokens (no 'prevalence', 'data', 'latest')
    off_topic = [
        SearchSnippet(url="https://en.wikipedia.org/wiki/COVID-19_pandemic",
                      title="COVID-19 pandemic - Wikipedia",
                      snippet="identified 31 December 2019 WHO outbreak"),
        SearchSnippet(url="https://www.aamc.org/news/covid-19-variants",
                      title="COVID-19 variants",
                      snippet="SARS-CoV-2 mutated since pandemic began"),
    ]
    state = {
        "query": "is covid19 still around? what are the latest prevalence data?",
        "current_query": "covid19 prevalence data 2026",
        "intent": "recent",
        "domain": "health",
        "search_ok": True,
        "ranked_items": off_topic,
        "rewritten_queries": [
            "covid19 prevalence data 2026",
            "covid19 prevalence data 2026 latest",
        ],
        "retry_count": 0,
    }
    # Patch quality threshold to pass (so we isolate coverage check)
    with monkeypatch.context() as m:
        m.setattr("tools.search_agentic._QUALITY_THRESHOLD", 0.0)
        result = summarize_node(state)

    # Must signal retry-able failure, NOT a user-facing string
    assert result.get("search_ok") is False, (
        f"Expected search_ok=False from evidence-fail path, got: {result}"
    )
    assert "search_error" in result, "Expected search_error key in result"
```

- [ ] **Step 2: Run to verify failure**

```bash
cd tools-harness && python -m pytest tests/test_search_graph_advanced.py::test_summarize_node_evidence_fail_sets_search_ok_false -v
```

Expected: `FAILED` — current code returns a dict with an answer string but `search_ok` remains unchanged.

- [ ] **Step 3: Fix `summarize_node` evidence-fail path**

In `tools-harness/agents/search_graph.py`, find lines 1008–1015:

```python
    if not _evidence_covers_query(state["query"], items):
        _emit(runtime, "stage", stage="summarize_no_coverage", query=state["query"])
        return {
            "answer": (
                f"The search results don't contain relevant information to answer: {state['query']}. "
                "Try rephrasing or using a more specific query."
            )
        }
```

Replace with:

```python
    if not _evidence_covers_query(state["query"], items):
        _emit(runtime, "stage", stage="summarize_no_coverage", query=state["query"])
        # Signal retryable failure so verify_node can trigger a second search pass.
        # The hard user-facing error only surfaces if retry_count >= 1 (i.e. second pass also fails).
        if state.get("retry_count", 0) < 1:
            return {
                "search_ok": False,
                "search_error": "no_coverage",
                "answer": (
                    f"The search results don't contain relevant information to answer: {state['query']}. "
                    "Try rephrasing or using a more specific query."
                ),
            }
        # Second pass also had no coverage — surface the error.
        return {
            "answer": (
                f"The search results don't contain relevant information to answer: {state['query']}. "
                "Try rephrasing or using a more specific query."
            )
        }
```

- [ ] **Step 4: Run test to verify pass**

```bash
cd tools-harness && python -m pytest tests/test_search_graph_advanced.py::test_summarize_node_evidence_fail_sets_search_ok_false -v
```

Expected: `PASSED`.

- [ ] **Step 5: Run full test suite**

```bash
cd tools-harness && python -m pytest test_search_graph.py tests/test_search_graph_advanced.py tests/test_search_evals.py -v 2>&1 | tail -30
```

Expected: all pass (no regressions on existing tests).

- [ ] **Step 6: Commit**

```bash
git add tools-harness/agents/search_graph.py tools-harness/tests/test_search_graph_advanced.py
git commit -m "fix(search): summarize evidence-fail triggers verify retry instead of hard error"
```

---

## Task 6: End-to-end smoke test

**Files:**
- No new files — manual verification

- [ ] **Step 1: Run the live graph with the original failing query**

```bash
cd tools-harness && python -c "
import sys; sys.path.insert(0, '.')
from agents.search_graph import run_search_graph
result = run_search_graph(
    'is covid19 still around? what are the latest prevalence data?',
    'recent',
    'health',
)
print(result)
" 2>&1 | grep -v 'DEBUG\|rustls\|hyper\|reqwest\|primp\|asyncio'
```

Expected output should:
- Contain actual numbers (cases, deaths, or "active" count)
- Cite `disease.sh`, `cdc.gov`, or `who.int` as sources — NOT just Wikipedia/YouTube
- NOT contain "don't contain relevant information"

- [ ] **Step 2: Verify supplement query still works (regression check)**

```bash
cd tools-harness && python -c "
import sys; sys.path.insert(0, '.')
from agents.search_graph import run_search_graph
result = run_search_graph('what are the health benefits of turmeric?', 'general', 'health')
print(result[:400])
" 2>&1 | grep -v 'DEBUG\|rustls\|hyper\|reqwest\|primp\|asyncio'
```

Expected: answer about curcumin/anti-inflammatory properties (same as before — `HealthAdapter` still handles this path).

- [ ] **Step 3: Commit if smoke passes**

```bash
git add .
git commit -m "test(search): verify epi pipeline end-to-end (smoke pass)"
```

---

## Self-Review

**Spec coverage check:**

| Fix | Task | Status |
|-----|------|--------|
| `_DDG_RACE_MIN_ITEMS = 2 → 4` + coverage check | Task 2 | ✓ |
| No `health` allow list | Task 1 | ✓ |
| OWID/disease.sh not in authority set | Task 1 | ✓ |
| `HealthAdapter` wrong scope | Task 4 | ✓ |
| Weak query rewrite | Task 3 | ✓ |
| Evidence-fail hard dead-end | Task 5 | ✓ |
| JS-rendered pages (OWID, CDC tracker) | Task 4 | ✓ (Playwright fallback already in fetch_url.py) |

**Type consistency:** All references to `EpiAdapter` in Task 4 are consistent. `_EPI_RE` defined once, used in `discovery_router_node`. `search_ok`, `search_error` keys are already in `SearchState` (used by existing paths).

**No placeholder checks:** All code blocks are complete and runnable.

**Interaction between tasks:** Tasks 1–5 are independent but compound. Even with only Tasks 2+4 applied, the epi path works. Task 5 adds a retry safety net for cases where a non-epi DDG still sneaks through.
