"""
Code-level premise contradiction detector.

COUPLED CONTRACT — DO NOT CHANGE IN ISOLATION:
  This module injects [SEARCH NOTE: ...] sentinels into tool results.
  The _MODEL_ADDENDA["gemma4"] entry in clients/ollama_client.py
  instructs gemma4 to treat [SEARCH NOTE:] as an authoritative directive.
  If the sentinel format changes here, update the addendum there too,
  and re-run test_verify_fixes.py to confirm gemma4 still surfaces contradictions.
  The system prompt instruction and the sentinel format are a coupled contract.

Checks whether a search result contradicts the implied assertion in the
user's question, and returns a warning prefix to inject into the tool
result before it reaches the model.

Operates on keyword/pattern signals — no model call, no latency.
Falls back to None (no warning) if no contradiction is detected.
"""

import re

# ── Market direction ──────────────────────────────────────────────────────────

_Q_MARKET_DOWN = re.compile(
    r"\b(drop|dropped|fall|fell|crash|crashed|decline|declined|plunge|plunged|"
    r"tumble|tumbled|slump|slumped|sell.?off|collapse|loss)\b", re.I
)
_Q_MARKET_UP = re.compile(
    r"\b(rise|rose|risen|gain|gained|rally|rallied|surge|surged|climb|climbed|"
    r"recover|recovered|rebound|rebounded|jump|jumped|soar|soared)\b", re.I
)

_R_MARKET_UP = re.compile(
    r"\b(rose|gained|rallied|surged?|surging|climbed|recovered|rebounded|jumped|soared|"
    r"rebound|reversed.{0,20}declines?|turned higher|ends? higher|"
    r"higher after|gains?|up \d|rose \d|gained \d|stocks? (rose|up|gained|higher))\b", re.I
)
_R_MARKET_DOWN = re.compile(
    r"\b(fell|dropped|declined|plunged|tumbled|slumped|crashed|sell.?off|"
    r"down \d|fell \d|dropped \d)\b", re.I
)

# ── Entity existence ──────────────────────────────────────────────────────────

_Q_EXISTENCE = re.compile(
    r"\b(when (will|does|did|is)|what (is|are)|who (is|are)|where is|release date|"
    r"coming out|scheduled for)\b", re.I
)
_R_NONEXISTENCE = re.compile(
    r"\b(does not exist|no such (film|movie|show|game|event)|not (an? )?(official|real|"
    r"confirmed)|never (announced|confirmed)|no official|hypothetical|placeholder)\b", re.I
)

# ── Warning templates ─────────────────────────────────────────────────────────

_WARN_MARKET_UP = (
    "[SEARCH NOTE: Results indicate the market rose or recovered on this date, "
    "not dropped. State this finding explicitly before explaining any volatility.]"
)
_WARN_MARKET_DOWN = (
    "[SEARCH NOTE: Results indicate the market fell on this date, not rose. "
    "State this finding explicitly before answering.]"
)
_WARN_NONEXISTENCE = (
    "[SEARCH NOTE: Results suggest this entity, film, or event may not officially exist "
    "or has not been confirmed. Do not invent details — surface this uncertainty to the user.]"
)


def check(question: str, search_result: str) -> str | None:
    """
    Returns a warning string to prepend to the tool result if a contradiction
    is detected, or None if the result appears consistent with the question.

    The warning is injected by code — the model is not asked to detect it.
    """
    q = question
    r = search_result

    # Market: question implies drop, result signals rise
    if _Q_MARKET_DOWN.search(q):
        up_signals = len(_R_MARKET_UP.findall(r))
        down_signals = len(_R_MARKET_DOWN.findall(r))
        if up_signals >= 1 and up_signals > down_signals:
            return _WARN_MARKET_UP

    # Market: question implies rise, result signals drop
    if _Q_MARKET_UP.search(q):
        down_signals = len(_R_MARKET_DOWN.findall(r))
        up_signals = len(_R_MARKET_UP.findall(r))
        if down_signals >= 1 and down_signals > up_signals:
            return _WARN_MARKET_DOWN

    # Existence: question assumes entity exists, result denies it
    if _Q_EXISTENCE.search(q) and _R_NONEXISTENCE.search(r):
        return _WARN_NONEXISTENCE

    return None


_YEAR_RE = re.compile(r"\b(20\d{2})\b")


def check_temporal_coverage(query: str, evidence_text: str) -> str | None:
    """
    Returns a warning if the evidence corpus appears to be from a different year
    than the one explicitly requested in the query.

    Only fires when the query has at least one explicit year AND the evidence
    contains years that don't overlap with the query years.
    Returns None (no warning) when the query has no year constraint.
    """
    query_years = set(_YEAR_RE.findall(query))
    if not query_years:
        return None  # no year constraint — nothing to check

    evidence_years = set(_YEAR_RE.findall(evidence_text))
    if not evidence_years:
        return None  # no years in evidence — can't determine mismatch

    if query_years & evidence_years:
        return None  # at least one matching year — coverage ok

    q_sorted = ", ".join(sorted(query_years))
    e_sorted = ", ".join(sorted(evidence_years))
    return (
        f"[SEARCH NOTE: query asks about {q_sorted} but retrieved sources mention {e_sorted}. "
        f"Answer may not reflect the correct time period — state this caveat explicitly.]"
    )


def inject(question: str, search_result: str) -> str:
    """
    Return search_result, prepended with a contradiction warning if detected.
    Drop-in replacement at the tool result injection site.
    """
    warning = check(question, search_result)
    if warning:
        return f"{warning}\n\n{search_result}"
    return search_result
