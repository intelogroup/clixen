"""
Focused search layer test — no model contention.

Two isolated concerns:
  A. Query formation: does the model form a good search query?
     (tool call only — no search API, no inner model calls)
  B. Synthesis quality: given pre-canned search results, how well does each model synthesize?
     (no API calls, no search graph — feeds results directly)

Models tested: qwen3:4b vs mistral-nemo
"""
import sys, os, time
if __name__ != "__main__":
    import pytest
    pytest.skip("script-style Ollama smoke test; run directly when local models are available", allow_module_level=True)

sys.path.insert(0, os.path.dirname(__file__))
os.chdir(os.path.dirname(__file__))
from dotenv import load_dotenv; load_dotenv()

import ollama

G = "\033[92m"; Y = "\033[93m"; R = "\033[91m"; B = "\033[94m"; X = "\033[0m"

QWEN_OPTS = {"temperature": 0.7, "top_p": 0.8, "top_k": 20, "repetition_penalty": 1.05}
NEMO_OPTS = {"temperature": 0.7, "top_p": 0.9}

WEB_SEARCH_TOOL = {
    "type": "function",
    "function": {
        "name": "web_search",
        "description": "Search the web for current, real-time information. Use when the question needs up-to-date facts.",
        "parameters": {
            "type": "object",
            "properties": {"query": {"type": "string"}},
            "required": ["query"],
        },
    },
}

def chat(model, msgs, tools=None):
    opts  = QWEN_OPTS if model.startswith("qwen3") else NEMO_OPTS
    think = False     if model.startswith("qwen3") else None
    kw    = dict(model=model, messages=msgs, options=opts, stream=False)
    if think is not None: kw["think"] = think
    if tools:             kw["tools"] = tools
    return ollama.chat(**kw)

# ── A. Query formation ────────────────────────────────────────────────────────

print(f"\n{B}=== A. Query formation (tool call only) ==={X}\n")

FORMATION_CASES = [
    ("what is the Bitcoin price today?",         "finance/live",   ["bitcoin", "price"]),
    ("who won the Champions League 2024 final?", "sports",         ["champions league", "2024"]),
    ("latest stable Python version",             "tech",           ["python", "release"]),
    ("weather in Paris this week",               "weather",        ["paris", "weather"]),
    ("realme gt7 pro specs",                     "product",        ["realme", "gt7"]),
]

for user_msg, label, kw_expect in FORMATION_CASES:
    print(f"  [{label}] \"{user_msg}\"")
    for model in ["qwen3:4b", "mistral-nemo"]:
        t0 = time.time()
        resp = chat(model, [{"role": "user", "content": user_msg}], tools=[WEB_SEARCH_TOOL])
        ms   = (time.time() - t0) * 1000
        calls = resp.message.tool_calls or []
        if not calls:
            print(f"    {model:18s} {R}NO TOOL CALL{X} ({int(ms)}ms) — answered from training: \"{(resp.message.content or '')[:80]}\"")
            continue
        query = calls[0].function.arguments.get("query", "")
        hits  = [k for k in kw_expect if k.lower() in query.lower()]
        col   = G if len(hits) == len(kw_expect) else (Y if hits else R)
        print(f"    {model:18s} {int(ms)}ms  {col}query: \"{query}\"{X}  ({len(hits)}/{len(kw_expect)} expected terms)")
    print()

# ── B. Synthesis quality (pre-canned results, no API, no inner model) ─────────

print(f"\n{B}=== B. Synthesis quality (pre-canned search results) ==={X}\n")

CANNED = [
    {
        "question": "what is the Bitcoin price today?",
        "search_result": (
            "Summary: Bitcoin is trading at $94,350 as of April 10 2026, up 2.1% in the last 24 hours.\n\n"
            "- Bitcoin Price Today (coindesk.com)\n  BTC/USD: $94,350. Market cap: $1.87T. 24h volume: $42B.\n\n"
            "- Crypto Markets (bloomberg.com)\n  Bitcoin surged past $94k driven by ETF inflows and macro optimism."
        ),
        "expected_kw": ["94", "bitcoin", "btc"],
    },
    {
        "question": "who won the Champions League 2024 final?",
        "search_result": (
            "Summary: Real Madrid won the 2024 UEFA Champions League final, defeating Borussia Dortmund 2-0 at Wembley on June 1 2024.\n\n"
            "- UCL Final 2024 (uefa.com)\n  Goals from Carvajal and Vinicius Jr sealed Real Madrid's 15th European title.\n\n"
            "- Champions League Final result (bbc.co.uk/sport)\n  Real Madrid 2-0 Dortmund. Vinicius Jr named man of the match."
        ),
        "expected_kw": ["real madrid", "dortmund", "2-0", "wembley"],
    },
    {
        "question": "latest stable Python version",
        "search_result": (
            "Summary: Python 3.13.2 is the latest stable release as of March 2025.\n\n"
            "- Python.org downloads (python.org)\n  Python 3.13.2 — Released March 12 2025. Security and bug fixes.\n\n"
            "- Python 3.13 release notes (docs.python.org)\n  New features: improved error messages, free-threaded mode, JIT compiler."
        ),
        "expected_kw": ["python", "3.13", "stable"],
    },
    {
        "question": "realme gt7 pro specs",
        "search_result": (
            "Summary: The Realme GT7 Pro features a Snapdragon 8 Elite chip, 6.78-inch 2K AMOLED display, 6000mAh battery, and 120W charging.\n\n"
            "- Realme GT7 Pro specs (gsmarena.com)\n  Snapdragon 8 Elite, 12/16GB RAM, 256/512GB storage, 50MP triple camera.\n\n"
            "- Realme GT7 Pro review (theverge.com)\n  Excellent performance flagship with best-in-class battery life."
        ),
        "expected_kw": ["snapdragon", "6000mah", "gt7"],
    },
]

for case in CANNED:
    q      = case["question"]
    result = case["search_result"]
    kw     = case["expected_kw"]
    print(f"  Q: \"{q}\"")
    for model in ["qwen3:4b", "mistral-nemo"]:
        msgs = [
            {"role": "user",      "content": q},
            {"role": "assistant", "content": "", "tool_calls": [
                {"function": {"name": "web_search", "arguments": {"query": q}}}
            ]},
            {"role": "tool", "name": "web_search", "content": result},
        ]
        t0   = time.time()
        resp = chat(model, msgs)
        ms   = (time.time() - t0) * 1000
        ans  = (resp.message.content or "").strip()
        hits = [k for k in kw if k.lower() in ans.lower()]
        col  = G if len(hits) == len(kw) else (Y if hits else R)
        # Check for hallucination markers (model ignoring search result)
        ignores_result = not any(k.lower() in ans.lower() for k in kw)
        extra = f" {R}[ignores search result]{X}" if ignores_result else ""
        print(f"    {model:18s} {int(ms)}ms  {col}{len(hits)}/{len(kw)} kw{X}{extra}")
        print(f"                       \"{ans[:120]}\"")
    print()

print(f"\n{B}=== Done ==={X}\n")
