"""
Agentic post-search pipeline.

Stages:
1. Normalize structured snippets from the chosen backend.
2. Rerank snippets against the user's query.
3. Ask a small local model for a short exact answer grounded only in those snippets.
4. Fall back to a deterministic formatter if the model is unavailable.

This keeps search behavior deterministic at the tool layer instead of hoping the
final chat model will clean up noisy aggregator output.
"""

from __future__ import annotations

from ._scoring import (
    _authority_score,
    _clean,
    _coerce_item,
    _domain_allow_score,
    _freshness_decay,
    _is_nav_noise,
    _is_state_health_dept,
    _is_temporal,
    _matches_domain_or_subdomain,
    _normalize_items,
    _parse_date,
    _parse_items_from_content,
    _query_overlap_score,
    _safe_shutdown,
    _should_deny,
    _source_quality_score,
    _strip_js,
    _tokenize,
    _tokens,
)
from ._summarize import (
    _compute_timezone_context,
    _content_recency_bonus,
    _entity_lock_penalty,
    _evidence_block,
    _extract_answer,
    _extract_format_hint,
    _extract_large_numeric_claims,
    _extract_query_entity_tokens,
    _freshness_bonus,
    _heuristic_rerank,
    _noise_penalty,
    _parse_hhmm,
    _path_intent_score,
    _relative_temporal_penalty,
    _venue_open_status,
    deterministic_summary,
    rerank_items,
    summarize_with_model,
)

__all__ = [
    "rerank_items",
    "summarize_with_model",
    "deterministic_summary",
]
