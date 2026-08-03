"""
Conversational follow-up resolution for the websearch/temporal path.

The search branch in harness.py is context-blind — it hands the raw user message to
the web search pipeline and never reads conversation history. So a follow-up like
"why did you give me false info?" gets searched literally (and comes back with a legal
answer about lying to police). This module decides, given the current message + history:

  ("chat", query)        -> meta/self-referential ("why did you say that", "you're wrong").
                            Answer from history via the chat model, NOT the web.
  ("search", standalone) -> context-dependent info follow-up ("what about the second half?").
                            Rewritten into a self-contained search query using history.
  ("standalone", query)  -> already self-contained. Search as-is.

Only the "search" branch touches the LLM (a cheap qwen3.5:4b rewrite); the meta and
standalone decisions are pure regex, so they're deterministic and unit-testable offline.
"""

from __future__ import annotations

import logging
import os
import re

_log = logging.getLogger("websearch")

# Meta / self-referential: the message is ABOUT the assistant's prior turn, not a new
# web query. These must never hit the search pipeline.
_META_RE = re.compile(
    r"\b(?:"
    r"why\s+(?:did|would|do)\s+you|"
    r"you\s+(?:said|told|gave|claimed|answered|mentioned|were\s+wrong)|"
    r"you'?re\s+wrong|you\s+are\s+wrong|"
    r"that'?s\s+(?:not\s+)?(?:wrong|right|correct|incorrect|false|true)|"
    r"are\s+you\s+sure|is\s+that\s+(?:right|correct|true)|"
    r"in\s+the\s+first\s+place|"
    r"(?:your|the)\s+(?:last\s+)?(?:answer|response|reply)|"
    r"what\s+did\s+you\s+(?:say|mean)|"
    r"i\s+(?:didn'?t|did\s+not|never)\s+(?:say|ask|mean)|"
    r"false\s+info|wrong\s+(?:info|answer)|"
    r"earlier\s+you|repeat\s+(?:that|your)"
    r")\b",
    re.I,
)

# Personal-recall: "what is my dog's name", "who is my emergency contact" — the open web
# can never answer these (it has no idea what "my X" refers to), so they must always be
# resolved from conversation history, never searched. Found 2026-07-01: "what is my dog's
# name?" fell through to standalone search and got a generic essay on popular dog names,
# ignoring that the user had just stated the name.
_PERSONAL_RE = re.compile(
    r"\b(?:what|who|where|which|how|when)(?:'s|\s+(?:is|are|was|were))\s+my\b",
    re.I,
)

# Context-dependent openers: the message continues the prior topic but asks for NEW
# information (so it still needs a search, just rewritten to stand alone).
_CONTEXT_OPENER_RE = re.compile(
    r"^\s*(?:and|but|so|what\s+about|how\s+about|what\s+of|then)\b",
    re.I,
)
# Bare pronoun/deictic reference with no proper noun of its own → depends on prior turn.
_PRONOUN_RE = re.compile(
    r"\b(?:it|its|they|them|their|that|those|these|this|he|she|his|her|the\s+other|"
    r"the\s+same|the\s+second|the\s+first|the\s+latter|the\s+former)\b",
    re.I,
)
# A capitalized multi-letter token mid-sentence usually means the query names its own
# subject (proper noun) and doesn't need the prior turn to be understood.
_PROPER_NOUN_RE = re.compile(r"(?<!^)(?<![.!?]\s)\b[A-Z][a-zA-Z]{2,}\b")
# Elliptical follow-ups: the question has an implicit missing subject rather than an
# explicit pronoun ("what was the score?", "who scored the goal?") — very common in
# sports/event chat. Same proper-noun guard as _PRONOUN_RE: if the query names its own
# subject ("what was the score of the World Cup final?") it's self-contained, not a
# follow-up. Found 2026-07: these fell through to "standalone" and got searched/answered
# with no idea which game was being discussed.
_ELLIPTICAL_QUESTION_RE = re.compile(
    r"\bwhat(?:'s|\s+(?:was|is|were))\s+the\s+(?:score|result|outcome|final\s+score)\b|"
    r"\bwho\s+(?:scored|won|got\s+the\s+goal|scored\s+the\s+goal)\b",
    re.I,
)


def _is_meta_followup(query: str) -> bool:
    """True when the message is about the assistant's prior answer, or asks the assistant
    to recall something personal it can only know from this conversation — either way, the
    open web can't answer it."""
    q = query or ""
    return bool(_META_RE.search(q) or _PERSONAL_RE.search(q))


def _needs_context(query: str, history: list) -> bool:
    """True when the message can't be understood/searched without the prior turns."""
    if not history:
        return False
    q = (query or "").strip()
    if _CONTEXT_OPENER_RE.search(q):
        return True
    # Short, pronoun-driven, and without its own proper-noun subject → deictic.
    if len(q.split()) <= 8 and _PRONOUN_RE.search(q) and not _PROPER_NOUN_RE.search(q):
        return True
    # Short, elliptical (implicit-subject), and without its own proper-noun subject.
    if len(q.split()) <= 10 and _ELLIPTICAL_QUESTION_RE.search(q) and not _PROPER_NOUN_RE.search(q):
        return True
    return False


def _recent_context(history: list, max_turns: int = 4, max_chars: int = 1500) -> str:
    turns = history[-max_turns:] if history else []
    text = "\n".join(
        f"{t.get('role', '?')}: {t.get('content', '')}" for t in turns
    )
    return text[-max_chars:]


def _rewrite_standalone(query: str, history: list) -> str:
    """Fold history into a self-contained search query. Cloud-primary (DeepSeek),
    matching websearch.py's _rewrite_query — local qwen3.5:4b is a fallback only,
    not something to depend on in a test/CI environment without Ollama running."""
    prompt = (
        "Rewrite the user's follow-up into a single self-contained web search query by "
        "resolving pronouns and references using the conversation. Output ONLY the query.\n\n"
        f"Conversation:\n{_recent_context(history)}\n\n"
        f"Follow-up: {query}\n"
        "Standalone query:"
    )
    try:
        from clients import cloud_client
        choice = cloud_client.raw_completion(
            model=cloud_client.DEFAULT_CLOUD_MODEL,
            messages=[{"role": "user", "content": prompt}],
            tools=[],
            bypass_budget=True,
        )
        out = (choice.message.content or "").strip().strip('"')
        if out:
            return out
    except Exception as e:
        _log.warning("cloud followup rewrite failed (%s), falling back to local", e)

    try:
        import ollama
        resp = ollama.generate(
            model=os.environ.get("OLLAMA_REWRITE_MODEL", "qwen3.5:4b"),
            prompt=prompt,
            options={"temperature": 0.0, "num_predict": 40},
            keep_alive=-1,
            think=False,
        )
        out = (resp.get("response") or "").strip().strip('"')
        if out:
            return out
    except Exception as e:  # network/model down → fall back to the raw query
        _log.warning("followup rewrite failed: %s", e)
    return query


def classify_followup(query: str, history: list) -> tuple[str, str]:
    """
    Returns (kind, resolved_query) where kind is "chat" | "search" | "standalone".
    Pure regex except the "search" case, which calls a cheap LLM rewrite.
    """
    if history and _is_meta_followup(query):
        return "chat", query
    if _needs_context(query, history):
        return "search", _rewrite_standalone(query, history)
    return "standalone", query


def _demo() -> None:
    """Offline self-check for the deterministic decisions (no network)."""
    hist = [
        {"role": "user", "content": "France vs Sweden final score"},
        {"role": "assistant", "content": "France won 3-0."},
    ]
    # meta → chat
    for q in [
        "Why did you give me false info in the first place?",
        "you're wrong",
        "are you sure about that?",
        "that's not correct",
        "what did you say earlier?",
    ]:
        k, _ = classify_followup(q, hist)
        assert k == "chat", f"{q!r} → {k}, want chat"
    # personal-recall → chat (the open web can never know "my X")
    pet_hist = [
        {"role": "user", "content": "my dog's name is Biscuit"},
        {"role": "assistant", "content": "Got it, Biscuit."},
    ]
    for q in ["What is my dog's name?", "who is my emergency contact", "where's my package",
              "when is my anniversary"]:
        k, rq = classify_followup(q, pet_hist)
        assert k == "chat" and rq == q, f"{q!r} → {k}, want chat"
    # context-dependent (deterministic path — _needs_context True; rewrite is stubbed off here)
    for q in ["and the second half?", "what about their goalkeeper?", "how about the other team?"]:
        assert _needs_context(q, hist), f"{q!r} should need context"
        assert not _is_meta_followup(q), f"{q!r} wrongly flagged meta"
    # self-contained → standalone, and meta patterns don't fire without history
    for q in ["What is the capital of France?", "Real Madrid latest transfer news"]:
        k, rq = classify_followup(q, hist)
        assert k == "standalone" and rq == q, f"{q!r} → {k}"
    assert not _is_meta_followup("why is the sky blue")  # 'why' alone isn't meta
    assert classify_followup("you're wrong", [])[0] == "standalone"  # no history → not meta
    print("followup: all deterministic cases correct")


if __name__ == "__main__":
    _demo()
