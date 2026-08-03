#!/usr/bin/env python3
"""
Benchmark models for human-facing conversational quality.

Three angles:
  casual    — small talk, preferences, empathy (does it feel like a person?)
  reasoning — logic, math, analogy, common sense (does it think?)
  memory    — coreference across turns (does it remember what you said?)

Each case is a multi-turn exchange. The model sees the full prior history.
Final-turn reply is extracted and scored against:
  - no AI disclaimer phrases (+3)
  - appropriate reply length (+2)
  - expected content terms present (+4)
  - prior-context terms referenced (+4)
  - forbidden/wrong-answer terms (−2 each, floor 0)
  - correct answer for exact-answer tasks (+2)
Max per case: 15 pts.

Usage:
    python3 tools-harness/benchmark_conversation.py

Environment:
    CONVO_MODELS="granite3.3:2b,qwen3.5:4b,qwen3:4b,mistral-nemo"
    OLLAMA_URL="http://127.0.0.1:11434/api/chat"
"""

from __future__ import annotations

import json
import os
import re
import sys
import time
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))

OLLAMA_URL = os.environ.get("OLLAMA_URL", "http://127.0.0.1:11434/api/chat")
MODELS = [
    m.strip()
    for m in os.environ.get(
        "CONVO_MODELS",
        "granite3.3:2b,qwen3.5:4b,qwen3:4b,mistral-nemo",
    ).split(",")
    if m.strip()
]

SYSTEM = (
    "You are a helpful, direct, friendly assistant. "
    "Reply naturally as a human would — concise, warm, no bullet lists unless genuinely needed. "
    "Return exactly one JSON object: {\"reply\": \"<your reply>\"}. "
    "No markdown outside the JSON. No preamble."
)

AI_DISCLAIMER_RE = re.compile(
    r"as an ai|as a language model|i('m| am) just an? (ai|language model|llm)|"
    r"i (don't|do not) have (feelings|emotions|opinions|personal|consciousness)|"
    r"i('m| am) not (capable|able) of (feeling|having|experiencing)",
    re.IGNORECASE,
)


def post_chat(model: str, messages: list[dict]) -> tuple[str, float]:
    payload = {
        "model": model,
        "messages": messages,
        "think": False,
        "format": {
            "type": "object",
            "properties": {"reply": {"type": "string"}},
            "required": ["reply"],
        },
        "stream": False,
        "options": {"temperature": 0.7, "num_predict": 350},
    }
    req = urllib.request.Request(
        OLLAMA_URL,
        data=json.dumps(payload).encode(),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    t0 = time.time()
    with urllib.request.urlopen(req, timeout=180) as r:
        raw = json.loads(r.read().decode())
    elapsed = time.time() - t0
    content = raw.get("message", {}).get("content", "")
    try:
        return json.loads(content).get("reply", ""), elapsed
    except json.JSONDecodeError:
        return content, elapsed


def build_messages(turns: list[dict]) -> list[dict]:
    """
    turns: list of {"role": "user"|"assistant", "content": "..."}
    Returns full message list with system prepended.
    """
    return [{"role": "system", "content": SYSTEM}] + turns


def run_convo(model: str, history: list[dict], final_user: str) -> tuple[str, float]:
    """Append final_user turn and get model reply."""
    messages = build_messages(history + [{"role": "user", "content": final_user}])
    return post_chat(model, messages)


def score_reply(reply: str, case: dict) -> tuple[int, list[str]]:
    notes = []
    score = 0
    lowered = reply.lower()

    if not reply or len(reply) < 5:
        return 0, ["empty_reply"]

    # No AI disclaimer (+3)
    if AI_DISCLAIMER_RE.search(reply):
        notes.append("ai_disclaimer")
    else:
        score += 3

    # Length check (+2)
    min_c = case.get("min_chars", 20)
    max_c = case.get("max_chars", 350)
    if min_c <= len(reply) <= max_c:
        score += 2
    else:
        notes.append(f"len={len(reply)} (want {min_c}-{max_c})")

    # Expected content terms (+1 each, max 4)
    hit = sum(1 for t in case.get("answer_terms", []) if t.lower() in lowered)
    score += min(4, hit)
    if hit < min(2, len(case.get("answer_terms", []))):
        notes.append(f"missing_terms={case.get('answer_terms')}")

    # Prior context referenced (+1 each, max 4)
    ctx_hit = sum(1 for t in case.get("context_terms", []) if t.lower() in lowered)
    score += min(4, ctx_hit)
    if ctx_hit == 0 and case.get("context_terms"):
        notes.append(f"no_context_ref={case.get('context_terms')}")

    # Correct-answer check (+2, only for exact-answer tasks)
    if "correct_answers" in case:
        if any(a.lower() in lowered for a in case["correct_answers"]):
            score += 2
        else:
            notes.append(f"wrong_answer (want one of {case['correct_answers']!r})")

    # Forbidden / hallucination penalty (-2 each)
    for bad in case.get("forbidden_terms", []):
        if bad.lower() in lowered:
            score -= 2
            notes.append(f"forbidden={bad!r}")

    return max(0, score), notes


# ---------------------------------------------------------------------------
# Test cases
# Each case:
#   name, category, history (prior turns), final_user (the turn we score),
#   answer_terms, context_terms, min_chars, max_chars,
#   correct_answers (optional), forbidden_terms (optional)
# ---------------------------------------------------------------------------
CASES = [
    # ── CASUAL ──────────────────────────────────────────────────────────────
    {
        "name": "monday_vs_friday",
        "category": "casual",
        "history": [
            {"role": "user", "content": "honestly what do you think about monday mornings"},
            {"role": "assistant", "content": "__SKIP__"},  # model fills this in a warmup; we inject a neutral stub
        ],
        "final_user": "and how about fridays compared to mondays?",
        "answer_terms": ["friday", "monday"],
        "context_terms": ["monday"],
        "min_chars": 30,
        "max_chars": 300,
    },
    {
        "name": "pizza_or_tacos",
        "category": "casual",
        "history": [
            {"role": "user", "content": "pizza or tacos — pick one and defend it"},
            {"role": "assistant", "content": "Pizza, no contest. One slice covers every flavor — cheese, sauce, toppings — all in one bite."},
        ],
        "final_user": "ok but what if the tacos had really good melted cheese on them?",
        "answer_terms": ["cheese", "taco"],
        "context_terms": ["pizza"],
        "min_chars": 30,
        "max_chars": 280,
    },
    {
        "name": "rain_mood",
        "category": "casual",
        "history": [
            {"role": "user", "content": "it has been raining here for three days straight, pretty gloomy"},
            {"role": "assistant", "content": "Three days of rain is a lot — hope you have good coffee and something to watch."},
        ],
        "final_user": "does that kind of weather affect your mood at all?",
        "answer_terms": [],
        "context_terms": ["rain"],
        "min_chars": 20,
        "max_chars": 280,
        # Should NOT flat-deny any form of reaction — that's robotic
        "forbidden_terms": ["i don't have a mood", "i don't experience", "i have no mood"],
    },

    # ── REASONING ───────────────────────────────────────────────────────────
    {
        "name": "syllogism_invalid",
        "category": "reasoning",
        "history": [
            {"role": "user", "content": "all cats are mammals. some mammals can swim. can we conclude that some cats can swim?"},
            {"role": "assistant", "content": "No — that doesn't follow. Knowing that some mammals swim doesn't mean cats are in that group. The cats might be entirely outside the swimming mammals."},
        ],
        "final_user": "what if I told you tigers are cats and tigers can swim — does that change the conclusion?",
        "answer_terms": ["yes", "tiger"],
        "context_terms": ["swim", "cat"],
        "correct_answers": ["yes", "now we can", "that does", "changes it", "that confirms"],
        "min_chars": 20,
        "max_chars": 300,
    },
    {
        "name": "math_followup",
        "category": "reasoning",
        "history": [
            {"role": "user", "content": "I have 12 apples, give half to a friend, eat 3, then buy 5 more. how many do I have?"},
            {"role": "assistant", "content": "8. You start with 12, give away 6 (half), eat 3 leaving 3, then add 5 — total 8."},
        ],
        "final_user": "what if I had started with 20 instead of 12?",
        "answer_terms": ["12"],
        "context_terms": ["apples", "started"],
        "correct_answers": ["12"],
        "forbidden_terms": ["8"],
        "min_chars": 10,
        "max_chars": 250,
    },
    {
        "name": "analogy_flip",
        "category": "reasoning",
        "history": [
            {"role": "user", "content": "why is learning to code similar to learning a spoken language?"},
            {"role": "assistant", "content": "Both have syntax rules, vocabulary you memorize, and a need for daily practice. You learn by making mistakes, not by reading about it."},
        ],
        "final_user": "now give me one way they are completely different",
        "answer_terms": [],
        "context_terms": ["language", "code"],
        "min_chars": 20,
        "max_chars": 280,
        # Must pivot — not just repeat the similarity angle
        "forbidden_terms": ["similar", "same as"],
    },
    {
        "name": "phone_in_rain",
        "category": "reasoning",
        "history": [
            {"role": "user", "content": "I just dropped my phone in the rain and it got soaked, what do I do right now?"},
            {"role": "assistant", "content": "Power it off immediately — don't try to charge it. Dry the outside, shake out ports gently, and let it sit in a warm dry spot."},
        ],
        "final_user": "I put it in a bag of rice 30 minutes ago — is that actually useful or is it a myth?",
        "answer_terms": ["rice", "myth"],
        "context_terms": ["phone", "rice"],
        "correct_answers": ["myth", "not ideal", "not great", "silica", "doesn't actually", "limited"],
        "min_chars": 30,
        "max_chars": 300,
    },

    # ── MEMORY / COREFERENCE ─────────────────────────────────────────────────
    {
        "name": "cat_name_memory",
        "category": "memory",
        "history": [
            {"role": "user", "content": "my cat is called Mochi and she's really lazy, sleeps all day"},
            {"role": "assistant", "content": "Mochi is a great name! Lazy cats are secretly the most chill companions."},
            {"role": "user", "content": "what's a good toy for a cat like mine?"},
            {"role": "assistant", "content": "A slow feeder puzzle toy or a crinkle ball — lazy cats will still bat at things that move slowly in front of them."},
        ],
        "final_user": "nice, I want to put her name on the toy — what was it again?",
        "answer_terms": ["mochi"],
        "context_terms": ["mochi"],
        "correct_answers": ["mochi"],
        "min_chars": 5,
        "max_chars": 200,
    },
    {
        "name": "japan_trip_season",
        "category": "memory",
        "history": [
            {"role": "user", "content": "I am planning a trip to Japan in the fall"},
            {"role": "assistant", "content": "Fall is a perfect time — the foliage in Kyoto and Nikko is incredible in October and November."},
            {"role": "user", "content": "what should I pack?"},
            {"role": "assistant", "content": "Layers — mornings can be cool, afternoons warmer. A light jacket, comfortable walking shoes, and a small umbrella."},
        ],
        "final_user": "and what clothing is specifically right for the season I mentioned?",
        "answer_terms": ["fall", "autumn", "layer", "jacket", "cool"],
        "context_terms": ["fall", "autumn"],
        "min_chars": 30,
        "max_chars": 300,
    },
    {
        "name": "cilantro_preference",
        "category": "memory",
        "history": [
            {"role": "user", "content": "I hate cilantro, it tastes like soap to me"},
            {"role": "assistant", "content": "That's a real genetic thing — some people have a receptor that makes cilantro taste soapy. You're not imagining it."},
            {"role": "user", "content": "suggest a Mexican dish I might like"},
            {"role": "assistant", "content": "Carne asada tacos or enchiladas with red sauce — both are easy to get without cilantro and still very satisfying."},
        ],
        "final_user": "why did you pick those dishes specifically for me?",
        "answer_terms": ["cilantro"],
        "context_terms": ["cilantro"],
        "min_chars": 20,
        "max_chars": 300,
    },
]

MAX_PTS_PER_CASE = 15  # 3 + 2 + 4 + 4 + 2(optional correct) - penalties


def run_case(model: str, case: dict) -> dict:
    """Run the final turn and score it."""
    # Build history — replace __SKIP__ stubs so the model sees a real prior exchange.
    # For the monday case we inject a neutral assistant reply so the conversation flows.
    history = []
    for turn in case["history"]:
        if turn.get("content") == "__SKIP__":
            # Inject a plausible neutral stub
            stub = "Mondays feel slow — the week hasn't built momentum yet."
            history.append({"role": turn["role"], "content": stub})
        else:
            history.append(turn)

    reply, elapsed = run_convo(model, history, case["final_user"])
    score, notes = score_reply(reply, case)
    return {
        "task": case["name"],
        "category": case["category"],
        "score": score,
        "max": MAX_PTS_PER_CASE,
        "seconds": round(elapsed, 2),
        "notes": notes,
        "reply_preview": reply[:220],
    }


def main() -> int:
    categories = sorted({c["category"] for c in CASES})
    overall = []

    for model in MODELS:
        print(f"\nMODEL {model}", flush=True)
        total = 0
        latencies = []
        cat_scores: dict[str, list[int]] = {cat: [] for cat in categories}

        for case in CASES:
            result = run_case(model, case)
            total += result["score"]
            latencies.append(result["seconds"])
            cat_scores[case["category"]].append(result["score"])
            print(json.dumps(result, sort_keys=True), flush=True)

        cat_summary = {
            cat: {
                "score": sum(scores),
                "max": len(scores) * MAX_PTS_PER_CASE,
                "pct": round(100 * sum(scores) / (len(scores) * MAX_PTS_PER_CASE), 1),
            }
            for cat, scores in cat_scores.items()
            if scores
        }
        row = {
            "model": model,
            "score": total,
            "max": len(CASES) * MAX_PTS_PER_CASE,
            "pct": round(100 * total / (len(CASES) * MAX_PTS_PER_CASE), 1),
            "avg_seconds": round(sum(latencies) / len(latencies), 2),
            "by_category": cat_summary,
        }
        overall.append(row)
        print("SUMMARY_ROW " + json.dumps(row, sort_keys=True), flush=True)

    print("\nFINAL_SUMMARY", flush=True)
    for row in sorted(overall, key=lambda r: (r["score"], -r["avg_seconds"]), reverse=True):
        print(json.dumps(row, sort_keys=True), flush=True)

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
