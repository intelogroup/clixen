"""
Live integration check: chat memory + LLM router intent correctness.

Hits the ACTUALLY RUNNING chat_ui.py server (real Ollama models, non-deterministic
LLM classify stage) -- NOT part of the offline pytest suite (those mock the LLM
stage for determinism; this is the complement that exercises the real thing
end-to-end, the way the live bug-hunting in this session did by hand).

Run manually after any change to clients/router.py, store/conversation.py, or
tools/followup.py, or whenever OLLAMA_MAX_LOADED_MODELS / model warmup changes:

    python tests/live_check_memory_and_router.py [base_url]

Requires: chat_ui.py running (default http://localhost:9234), Ollama up.
Each case gets its own chat_id and is cleaned up via DELETE /history/{id} after.
"""

import sys
import time
import uuid

import requests

BASE_URL = sys.argv[1] if len(sys.argv) > 1 else "http://localhost:9234"
# filesystem-intent requests go through the LangGraph local-agent's multi-round tool
# loop (read -> recover -> retry), which legitimately takes 60-90s+ even for a
# not-found file -- found 2026-07-01 testing "cat <nonexistent path>" (~75s to
# complete correctly). Plain chat/casual turns are far faster; this is a ceiling.
TIMEOUT = 150

_passed = 0
_failed = 0


def _cid(tag: str) -> str:
    return f"livecheck_{tag}_{uuid.uuid4().hex[:8]}"


def send(message: str, chat_id: str, timeout: int = TIMEOUT) -> dict:
    r = requests.post(
        f"{BASE_URL}/chat",
        json={"message": message, "chat_id": chat_id},
        timeout=timeout,
    )
    r.raise_for_status()
    return r.json()


def clear(chat_id: str) -> None:
    try:
        requests.delete(f"{BASE_URL}/history/{chat_id}", timeout=10)
    except Exception:
        pass


def check(desc: str, ok: bool, detail: str) -> None:
    global _passed, _failed
    if ok:
        _passed += 1
        print(f"  PASS  {desc}")
    else:
        _failed += 1
        print(f"  FAIL  {desc}\n        {detail}")


# ---------------------------------------------------------------------------
# Router / intent correctness
# ---------------------------------------------------------------------------

# (message, expect=[...] or forbid=[...], description)
ROUTER_CASES = [
    dict(
        msg="I just adopted a cat named Whiskers, my new pet.",
        forbid=["filesystem"],
        desc="pet 'cat' must not trigger filesystem (regression: cat/shell-cmd collision)",
    ),
    dict(
        msg="Show me Chipotle on DoorDash",
        forbid=["filesystem"],
        desc="'show me X on DoorDash' must not steal filesystem intent (regression: bare 'show me')",
    ),
    dict(
        msg="show me my files in the current folder",
        expect=["filesystem"],
        desc="legit 'show me my files' still hits filesystem",
    ),
    dict(
        msg="list files in my Downloads folder",
        expect=["filesystem"],
        desc="filesystem intent: list files",
    ),
    dict(
        msg="take a screenshot of my screen",
        expect=["ocr"],
        desc="screenshot request -> ocr",
    ),
    dict(
        msg="what's 12 times 15",
        expect=["math", "casual"],
        desc="arithmetic stays local (math/casual get identical no-tool handling in chat mode)",
    ),
    dict(
        msg="what was the last question I asked you",
        expect=["casual"],
        desc="self-referential conversation ref -> casual, never web search",
    ),
    dict(
        msg="you said earlier we agreed on the plan",
        expect=["casual"],
        desc="explicit 'you said earlier' -> casual",
    ),
    dict(
        msg="get me directions from Boston to Cambridge",
        expect=["transit"],
        desc="directions request -> transit",
    ),
    dict(
        msg="cat ~/Downloads/notes.txt and tell me what's inside",
        expect=["filesystem"],
        desc="legit shell 'cat <path>' still hits filesystem (opposite-direction check on the cat fix)",
        timeout=240,  # goes through the LangGraph local-agent's multi-round tool loop
    ),
    dict(
        msg="My rabbit hopped onto the couch, so cute -- anyway, show me a good recipe for banana bread",
        forbid=["filesystem"],
        desc="'show me' buried mid-sentence with no file/folder noun must not steal filesystem",
    ),
    dict(
        msg="Did I get any new emails from Sarah?",
        expect=["email"],
        desc="email intent",
    ),
    dict(
        msg="Remind me to take the trash out tonight",
        expect=["reminder"],
        desc="reminder intent",
    ),
    dict(
        msg="Bonjour, comment ça va aujourd'hui?",
        expect=["multilingual"],
        desc="multilingual intent (non-English INPUT script, not an English translation request)",
    ),
    dict(
        msg="What meetings do I have scheduled for tomorrow?",
        expect=["calendar"],
        desc="calendar intent",
    ),
    dict(
        msg="Write a python function that reverses a string",
        expect=["code_quick", "code_medium", "code_heavy"],
        desc="code request -> one of the code_* intents",
    ),
    dict(
        msg="show me the recent git log for this repo",
        expect=["git"],
        desc="'show me' + git context -> git, not filesystem (another show-me stress test; "
             "intent is single-valued so this also implicitly proves it's not filesystem)",
    ),
    dict(
        msg="fetch and summarize the contents of https://example.com",
        expect=["url_fetch"],
        desc="url_fetch intent",
    ),
    dict(
        msg="add 'buy milk' to my task list",
        expect=["tasks"],
        desc="tasks intent",
    ),
    dict(
        msg="how much battery do I have left on my mac",
        expect=["system_status"],
        desc="system_status intent",
    ),
    dict(
        msg="run my morning routine automation",
        expect=["automation"],
        desc="automation intent",
    ),
    # Round 3 (2026-07-02): intents never exercised by rounds 1-2 -- library_docs,
    # spotlight, macos_native, browser, analysis, hard math, repl, transit(cost phrasing).
    dict(
        msg="what's the syntax for useEffect in react hooks",
        expect=["library_docs"],
        desc="library_docs intent",
    ),
    dict(
        msg="find the pdf I saved last week about taxes",
        expect=["spotlight"],
        desc="spotlight intent (recency + file-type noun)",
    ),
    dict(
        msg="what's in my clipboard right now",
        expect=["macos_native"],
        desc="macos_native intent (clipboard)",
    ),
    dict(
        msg="navigate to https://example.com and take a screenshot",
        expect=["browser"],
        desc="browser automation intent (read-only nav+screenshot, no account login -- "
             "'log into my X account' phrasings are deliberately NOT used here: they "
             "reach session_login(), which opens a real visible browser and attempts a "
             "real third-party login; found 2026-07-02 running this suite unattended)",
    ),
    dict(
        msg="explain the tradeoffs between REST and GraphQL",
        expect=["analysis"],
        desc="analysis intent",
    ),
    dict(
        msg="what's the derivative of x^3 plus 2x",
        expect=["math"],
        desc="hard math intent (calculus keyword)",
    ),
    dict(
        msg="run this in python: print(2+2)",
        expect=["repl"],
        desc="repl intent (execute + show output)",
    ),
    dict(
        msg="how much would an uber cost from Fenway to Logan airport",
        expect=["transit"],
        desc="transit intent (cost phrasing, must not fall to temporal on 'cost')",
    ),
]

# Observed only -- no strong ground truth for the exact bucket (channel-dependent
# defaults / harness-level guards outside classify_message), but useful to watch.
ROUTER_OBSERVE_CASES = [
    "what is the capital of Peru",
    "I'm hungry, what should I eat",
    "what's the weather in Boston right now",
    # Round 3: depend on real local DB contents (Slack/iMessage archives) --
    # correctness of the *result* isn't assertable here, only the routing is
    # interesting, and even that can legitimately vary with archive state.
    "search my slack for the deploy discussion from yesterday",
    "did I text mom about dinner plans",
]


def run_router_cases() -> None:
    print("\n=== Router / intent correctness ===")
    for case in ROUTER_CASES:
        cid = _cid("router")
        try:
            resp = send(case["msg"], cid, timeout=case.get("timeout", TIMEOUT))
            intent = resp.get("intent")
            if "expect" in case:
                ok = intent in case["expect"]
                detail = f"got intent={intent!r}, expected one of {case['expect']}"
            else:
                ok = intent not in case["forbid"]
                detail = f"got intent={intent!r}, forbidden: {case['forbid']}"
            check(case["desc"], ok, detail)
        except Exception as e:
            check(case["desc"], False, f"request error: {e}")
        finally:
            clear(cid)

    print("\n=== Router / observe only (no hard assertion) ===")
    for msg in ROUTER_OBSERVE_CASES:
        cid = _cid("observe")
        try:
            resp = send(msg, cid)
            print(f"  INFO  {msg!r} -> intent={resp.get('intent')!r}")
        except Exception as e:
            print(f"  INFO  {msg!r} -> error: {e}")
        finally:
            clear(cid)


# ---------------------------------------------------------------------------
# Conversation memory
# ---------------------------------------------------------------------------

def run_memory_immediate_recall() -> None:
    print("\n=== Memory: immediate recall ===")
    cid = _cid("mem_immediate")
    try:
        send("My favorite drink is oolong tea. Just remember that, no need to comment much.", cid)
        resp = send("What's my favorite drink?", cid)
        reply = (resp.get("reply") or "").lower()
        check(
            "immediate recall of a planted fact",
            "oolong" in reply,
            f"reply did not mention 'oolong': {resp.get('reply')!r}",
        )
        check(
            "personal-recall phrasing classified casual, not searched",
            resp.get("intent") in ("casual", "factual_qa"),
            f"unexpected intent: {resp.get('intent')!r}",
        )
    finally:
        clear(cid)


def run_memory_personal_recall_phrasing_variants() -> None:
    print("\n=== Memory: personal-recall phrasing variants ===")
    cid = _cid("mem_phrasing")
    try:
        send("I have a car and its nickname is Thunderbolt.", cid)
        for q in [
            "What is my car's nickname?",
            "What's my car's nickname?",
            "What did I say my car's nickname was?",
        ]:
            resp = send(q, cid)
            reply = (resp.get("reply") or "").lower()
            check(
                f"recall via phrasing: {q!r}",
                "thunderbolt" in reply,
                f"reply did not mention 'thunderbolt': {resp.get('reply')!r}",
            )
    finally:
        clear(cid)


def run_memory_recall_after_fillers() -> None:
    print("\n=== Memory: recall survives unrelated filler turns ===")
    cid = _cid("mem_fillers")
    try:
        send(
            "I'm planning a trip to Lisbon in September. Also I just adopted a pet named "
            "Whiskers, a cat. Just remember these, no need to comment much.",
            cid,
        )
        fillers = [
            "What's 12 times 15?",
            "Tell me a fun fact about octopuses, one sentence.",
            "Give me a one-sentence motivational quote.",
            "What's 100 divided by 4?",
            "Any quick tips for learning a new language?",
            "What's your favorite type of database index, briefly?",
        ]
        for f in fillers:
            send(f, cid)

        resp1 = send("Where am I traveling to in September?", cid)
        check(
            "trip destination recalled after 6 filler turns",
            "lisbon" in (resp1.get("reply") or "").lower(),
            f"reply: {resp1.get('reply')!r}",
        )

        resp2 = send("What's my pet's name?", cid)
        check(
            "pet name recalled after 6 filler turns",
            "whiskers" in (resp2.get("reply") or "").lower(),
            f"reply: {resp2.get('reply')!r}",
        )
    finally:
        clear(cid)


def run_memory_multi_fact_recall() -> None:
    print("\n=== Memory: multiple facts planted in one turn ===")
    cid = _cid("mem_multi")
    try:
        send(
            "Quick facts: my sister's name is Elena, I work as a nurse, and my "
            "lucky number is 27. Just remember these, no comment needed.",
            cid,
        )
        checks = [
            ("What's my sister's name?", "elena"),
            ("What's my job?", "nurse"),
            ("What's my lucky number?", "27"),
        ]
        for q, expected_substr in checks:
            resp = send(q, cid)
            reply = (resp.get("reply") or "").lower()
            check(
                f"recall: {q!r} -> contains {expected_substr!r}",
                expected_substr in reply,
                f"reply: {resp.get('reply')!r}",
            )
    finally:
        clear(cid)


def run_memory_correction_uses_latest_value() -> None:
    print("\n=== Memory: correction overrides the original fact ===")
    cid = _cid("mem_correction")
    try:
        send("My favorite season is winter.", cid)
        send("Actually, I changed my mind -- my favorite season is summer now.", cid)
        resp = send("What's my favorite season?", cid)
        reply = (resp.get("reply") or "").lower()
        check(
            "recalls the corrected value (summer), not the original (winter)",
            "summer" in reply and "winter" not in reply,
            f"reply: {resp.get('reply')!r}",
        )
    finally:
        clear(cid)


def run_memory_no_hallucination_on_unstated_fact() -> None:
    print("\n=== Memory: no confident hallucination for a never-stated fact ===")
    cid = _cid("mem_no_halluc")
    try:
        resp = send("What's my favorite movie?", cid)
        reply = (resp.get("reply") or "").lower()
        hedge_words = ["don't know", "haven't", "not sure", "didn't tell", "did not tell",
                       "haven't told", "no idea", "not mentioned", "don't have"]
        check(
            "does not confidently invent a specific movie title for unstated info",
            any(w in reply for w in hedge_words),
            f"reply (expected an honest 'I don't know'-style hedge): {resp.get('reply')!r}",
        )
    finally:
        clear(cid)


def run_memory_numeric_date_recall() -> None:
    print("\n=== Memory: numeric/date fact recalled across turns ===")
    cid = _cid("mem_date")
    try:
        send("My anniversary is on June 14th. Just remember that.", cid)
        send("What's 7 plus 5?", cid)
        send("Tell me a one-line fun fact about the ocean.", cid)
        resp = send("When is my anniversary?", cid)
        reply = (resp.get("reply") or "").lower()
        check(
            "date fact (June 14th) recalled after 2 filler turns",
            "june 14" in reply or "14th" in reply or "june" in reply and "14" in reply,
            f"reply: {resp.get('reply')!r}",
        )
    finally:
        clear(cid)


def run_combined_router_and_memory_case() -> None:
    print("\n=== Combined: router-tricky message that also plants a recallable fact ===")
    cid = _cid("mem_combined")
    try:
        resp1 = send("I'm meeting my cat's vet at 3pm today, just a quick checkup.", cid)
        check(
            "'cat's vet' scheduling message must not be classified as filesystem",
            resp1.get("intent") != "filesystem",
            f"got intent={resp1.get('intent')!r}",
        )
        resp2 = send("What time is my vet appointment?", cid)
        reply = (resp2.get("reply") or "").lower()
        check(
            "recalls the appointment time (3pm) from the earlier message",
            "3" in reply,
            f"reply: {resp2.get('reply')!r}",
        )
    finally:
        clear(cid)


def run_memory_negation_recall() -> None:
    print("\n=== Memory: recalls a stated negation/preference, not its opposite ===")
    cid = _cid("mem_negation")
    try:
        send("I don't like coffee, I much prefer tea.", cid)
        resp = send("Do I like coffee?", cid)
        reply = (resp.get("reply") or "").lower()
        no_words = ["don't", "do not", "no,", "not a fan", "dislike", "prefer tea", "not fond"]
        check(
            "correctly recalls dislike of coffee, doesn't flip to 'yes'",
            any(w in reply for w in no_words),
            f"reply: {resp.get('reply')!r}",
        )
    finally:
        clear(cid)


def run_memory_list_recall() -> None:
    print("\n=== Memory: multi-item list recalled together ===")
    cid = _cid("mem_list")
    try:
        send("My top 3 hobbies are hiking, painting, and chess. Just remember these.", cid)
        resp = send("What are my hobbies?", cid)
        reply = (resp.get("reply") or "").lower()
        found = [h for h in ("hiking", "painting", "chess") if h in reply]
        check(
            "all 3 listed hobbies recalled together",
            len(found) == 3,
            f"found only {found} in reply: {resp.get('reply')!r}",
        )
    finally:
        clear(cid)


def run_memory_long_filler_gap() -> None:
    print("\n=== Memory: recall survives a longer (10-turn) filler gap ===")
    cid = _cid("mem_long_gap")
    try:
        send("My childhood nickname was Sparky. Just remember that.", cid)
        fillers = [
            "What's 3 plus 4?", "Name a primary color.", "What's 9 times 9?",
            "Say a random word.", "What's the boiling point of water in Celsius?",
            "Give me a synonym for 'happy'.", "What's 50 minus 8?",
            "Name a planet in our solar system.", "What's 6 times 7?",
            "Give me a one-word compliment.",
        ]
        for f in fillers:
            send(f, cid)
        resp = send("What was my childhood nickname?", cid)
        check(
            "nickname recalled after 10 filler turns",
            "sparky" in (resp.get("reply") or "").lower(),
            f"reply: {resp.get('reply')!r}",
        )
    finally:
        clear(cid)


def run_combined_git_and_memory_case() -> None:
    print("\n=== Combined: 'show me' + git context plants a fact, then recall ===")
    cid = _cid("mem_git_combined")
    try:
        resp1 = send(
            "show me the git log -- by the way, my project deadline is October 3rd",
            cid,
        )
        check(
            "'show me' + git message must not be classified as filesystem",
            resp1.get("intent") != "filesystem",
            f"got intent={resp1.get('intent')!r}",
        )
        resp2 = send("When is my project deadline?", cid)
        reply = (resp2.get("reply") or "").lower()
        check(
            "deadline (October 3rd) recalled from the earlier combined message",
            "october 3" in reply or ("october" in reply and "3" in reply),
            f"reply: {resp2.get('reply')!r}",
        )
    finally:
        clear(cid)


def run_session_isolation_check() -> None:
    print("\n=== Memory: fresh session has no leaked history ===")
    cid = _cid("mem_isolation")
    try:
        pre = requests.get(f"{BASE_URL}/history/{cid}", timeout=10).json()
        check("fresh chat_id starts with empty history", pre == [], f"got: {pre!r}")

        send("My favorite number is 42.", cid)
        post = requests.get(f"{BASE_URL}/history/{cid}", timeout=10).json()
        check(
            "history after one exchange has exactly 2 turns (no leaked turns)",
            len(post) == 2,
            f"got {len(post)} turns: {post!r}",
        )
    finally:
        clear(cid)


def main() -> None:
    t0 = time.time()
    print(f"Live check against {BASE_URL}")

    run_router_cases()
    run_memory_immediate_recall()
    run_memory_personal_recall_phrasing_variants()
    run_memory_recall_after_fillers()
    run_memory_multi_fact_recall()
    run_memory_correction_uses_latest_value()
    run_memory_no_hallucination_on_unstated_fact()
    run_memory_numeric_date_recall()
    run_combined_router_and_memory_case()
    run_memory_negation_recall()
    run_memory_list_recall()
    run_memory_long_filler_gap()
    run_combined_git_and_memory_case()
    run_session_isolation_check()

    dt = time.time() - t0
    print(f"\n{'='*60}\n{_passed} passed, {_failed} failed  ({dt:.1f}s)\n{'='*60}")
    sys.exit(1 if _failed else 0)


if __name__ == "__main__":
    main()
