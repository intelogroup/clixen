"""
Test Ornith on all clixen <=2 round task types.
Uses real tool registry for tool-execution tasks.
Pure-LLM tasks call Ornith directly.
"""

import json, os, sys, time
from concurrent.futures import ThreadPoolExecutor

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "tools-harness"))

import ollama
from tools.registry import execute_tool, ALL_TOOLS
from tools.injection_guard import wrap_external_output
from tools.premise_check import inject as _premise_inject

_MODEL = "ornith:9b"
_SEARCH_TOOLS = {"web_search"}

_TOOL_SETS = {
    "remember": {"remember", "forget"},
    "ocr": {"ocr_image"},
    "auth": {"refresh_google_token"},
    "automation": {"list_automations"},
    "telegram": {"send_telegram"},
    "whatsapp": {"send_whatsapp"},
}
_SCHEMAS_CACHE = {}
for key, names in _TOOL_SETS.items():
    _SCHEMAS_CACHE[key] = [t for t in ALL_TOOLS if t["function"]["name"] in names]

def trunc(s, n=3000):
    s, n = str(s), n
    return s[:n] + f"... [{len(s)} chars]" if len(s) > n else s

def call_ornith(messages, tools=None, opts=None):
    o = dict(opts or {})
    o.setdefault("num_predict", 4096)
    o.setdefault("temperature", 0.3)
    o.setdefault("num_ctx", 16384)
    start = time.time()
    resp = ollama.chat(model=_MODEL, messages=messages, tools=tools or None, options=o, think=False)
    elapsed = time.time() - start
    return resp.message.content or "", resp.message.tool_calls, elapsed

def run_loop(msgs, tool_schemas, max_rounds=2, sys_prompt=None):
    sys_msg = {"role": "system", "content": sys_prompt or "You are a helpful assistant. Use tools when needed, then answer."}
    all_tool_calls = []
    for rnd in range(max_rounds):
        history = [sys_msg] + list(msgs)
        content, tcs, elapsed = call_ornith(history, tools=tool_schemas)
        if tcs:
            all_tool_calls.extend(tcs)
        if not tcs:
            print(f"  [r{rnd}] synth -- {elapsed:.1f}s")
            return content, rnd, all_tool_calls
        print(f"  [r{rnd}] tool(s)={len(tcs)} -- {elapsed:.1f}s  [{', '.join(c.function.name for c in tcs)}]")
        with ThreadPoolExecutor(max_workers=min(len(tcs), 4)) as pool:
            futs = {pool.submit(_exec, c.function.name, c.function.arguments, msgs[0].get("content","")): (i, c) for i, c in enumerate(tcs)}
            results = {}
            for fut in futs:
                i, c = futs[fut]
                results[i] = fut.result()
        asst = {"role": "assistant", "content": content}
        if hasattr(tcs[0], "model_dump"):
            asst["tool_calls"] = [t.model_dump() for t in tcs]
        msgs.append(asst)
        for i, c in enumerate(tcs):
            msgs.append({"role": "tool", "name": c.function.name, "tool_call_id": f"c_{rnd}_{i}", "content": trunc(results[i], 4000)})
    content, tcs, elapsed = call_ornith([sys_msg] + list(msgs), tools=None)
    if tcs:
        all_tool_calls.extend(tcs)
    print(f"  [final] synth -- {elapsed:.1f}s")
    return content, max_rounds, all_tool_calls

def _exec(fn_name, fn_args, user_msg):
    try:
        result = execute_tool(fn_name, fn_args)
        if fn_name in _SEARCH_TOOLS:
            result = _premise_inject(user_msg, result)
        result = wrap_external_output(fn_name, result)
        return result
    except Exception as e:
        return f"[error] {e}"

def check(msg, got):
    print(f"  {msg}: {'PASS' if got else 'FAIL'}")
    return got

def tool_was_called(tcs, name):
    return any(c.function.name == name for c in tcs)

## ── 1-round (no tools) ──────────────────────────────────────────────────

def test_1_classifier():
    print("\n=== 1-round: LLM classifier ===")
    prompt = (
        'Classify this message intent. Output ONLY JSON: {"intent": "...", "specialist_hint": null}\n'
        'Known intents: casual, coding, research, document, automation, messaging, data_analysis, web_browsing, planning, admin, git, followup\n'
        'Message: "search for latest python version 2026"\n'
    )
    content, _, _ = call_ornith([{"role": "user", "content": prompt}], tools=None)
    try:
        d = json.loads(content.strip().lstrip("```json").rstrip("```").strip())
        ok = d.get("intent") in ("research", "coding")
    except:
        ok = False
    if not ok: print(f"  raw: {content[:200]}")
    return check("intent classification JSON", ok)

def test_2_search_summarizer():
    print("\n=== 1-round: search summarizer ===")
    results = json.dumps([
        {"title": "Python 3.14 Release", "snippet": "Python 3.14 released Oct 2025 with pattern matching, improved typing, faster runtime."},
        {"title": "Python 3.13 Features", "snippet": "Python 3.13 introduced JIT compiler and improved error messages."},
    ])
    content, _, _ = call_ornith([{"role": "user", "content": f"Synthesize these search results into a clear answer about Python versions.\n\n{results}"}], tools=None)
    ok = "python" in content.lower() and ("3.14" in content or "3.13" in content)
    return check("summarizer mentions Python versions", ok)

def test_3_doc_summarizer():
    print("\n=== 1-round: doc summarizer (3-5 bullets) ===")
    doc = "Meeting Notes - Q3 Planning\nAction items:\n1. John to update API docs by Friday\n2. Sarah to review PR #342\n3. Deploy v2.1 to staging\nDecisions: Use PostgreSQL 17 for new service."
    content, _, _ = call_ornith([{"role": "user", "content": f"Summarize in 3-5 bullet points. Focus on key facts, decisions, action items.\n\n{doc}"}], tools=None)
    bullets = sum(1 for l in content.split("\n") if l.strip().startswith(("- ", "* ", "•", "1.", "2.", "3.")))
    return check(f"bullet-point summary ({bullets} bullets)", bullets >= 2)

## ── 2-round (1 tool call + synthesis) ──────────────────────────────────

def test_4_chat_followup():
    print("\n=== 2-round: chat follow-up (remember) ===")
    result, rounds, tcs = run_loop(
        [{"role": "user", "content": "Remember that my favorite editor is Neovim"}],
        _SCHEMAS_CACHE["remember"], max_rounds=2,
        sys_prompt="Answer the user's follow-up using conversation history. You have remember/forget tools to save/delete facts.")
    ok = tool_was_called(tcs, "remember") and ("neovim" in result.lower() or "saved" in result.lower() or "remembered" in result.lower())
    return check("remember tool called + confirmed", ok)

def test_5_ocr_image():
    print("\n=== 2-round: OCR image ===")
    img = "/tmp/test_ornith_ocr.png"
    from PIL import Image, ImageDraw
    d = ImageDraw.Draw(Image.new("RGB", (200, 50), color="white"))
    d.text((10, 10), "Hello OCR World", fill="black")
    Image.new("RGB", (200, 50), color="white").save(img)
    from PIL import Image as Im2
    d2 = ImageDraw.Draw(Im2.open(img))
    d2.text((10, 10), "Hello OCR World", fill="black")
    Im2.fromarray(Im2.open(img).tobytes()).save(img)
    result, rounds, tcs = run_loop(
        [{"role": "user", "content": f"Extract text from this image: {img}"}],
        _SCHEMAS_CACHE["ocr"], max_rounds=2,
        sys_prompt="You have ocr_image tool. Call it with image_path, then report the text.")
    os.remove(img)
    ok = tool_was_called(tcs, "ocr_image") and any(w in result.lower() for w in ("hello", "ocr", "world"))
    return check("OCR extracted text from image", ok)

def test_6_refresh_auth():
    print("\n=== 2-round: refresh Google auth ===")
    result, rounds, tcs = run_loop(
        [{"role": "user", "content": "Google APIs returning 401 errors. Refresh Google token."}],
        _SCHEMAS_CACHE["auth"], max_rounds=2,
        sys_prompt="You have refresh_google_token(). Call it immediately. Report success or error.")
    ok = tool_was_called(tcs, "refresh_google_token") and any(w in result.lower() for w in ("token", "refresh", "success", "error", "expir", "auth"))
    return check("auth token refresh handled", ok)

def test_7_list_automations():
    print("\n=== 2-round: list automations ===")
    result, rounds, tcs = run_loop(
        [{"role": "user", "content": "Show me what automations are running."}],
        _SCHEMAS_CACHE["automation"], max_rounds=2,
        sys_prompt="You have list_automations(). Call it and show results.")
    ok = len(result) > 10 and not result.startswith("[error") and not result.startswith("[blocked")
    return check("automations listed", ok)

def test_8_send_telegram():
    print("\n=== 2-round: send Telegram ===")
    result, rounds, tcs = run_loop(
        [{"role": "user", "content": "Send Telegram message: 'Hello from Ornith test'"}],
        _SCHEMAS_CACHE["telegram"], max_rounds=2,
        sys_prompt="You have send_telegram(message). Call it with user's message, then confirm.")
    ok = tool_was_called(tcs, "send_telegram")
    return check("send_telegram called", ok)

def test_9_send_whatsapp():
    print("\n=== 2-round: send WhatsApp ===")
    result, rounds, tcs = run_loop(
        [{"role": "user", "content": "Send WhatsApp to 14155552671: 'Test from Ornith'"}],
        _SCHEMAS_CACHE["whatsapp"], max_rounds=2,
        sys_prompt="You have send_whatsapp(to, message). Call it with user's number and message, then confirm.")
    ok = tool_was_called(tcs, "send_whatsapp")
    return check("send_whatsapp called", ok)


if __name__ == "__main__":
    print(f"=== Ornith {_MODEL} -- <=2 round task benchmark ===\n")

    tests = [
        ("classifier", test_1_classifier),
        ("search summarizer", test_2_search_summarizer),
        ("doc summarizer", test_3_doc_summarizer),
        ("chat follow-up (remember)", test_4_chat_followup),
        ("ocr image", test_5_ocr_image),
        ("refresh auth", test_6_refresh_auth),
        ("list automations", test_7_list_automations),
        ("send telegram", test_8_send_telegram),
        ("send whatsapp", test_9_send_whatsapp),
    ]

    results = {}
    for name, fn in tests:
        try:
            results[name] = fn()
        except Exception as e:
            print(f"  FAIL: {e}")
            results[name] = False

    passed = sum(1 for v in results.values() if v)
    print(f"\n=== {passed}/{len(results)} passed ===")
    for name, ok in results.items():
        print(f"  {'PASS' if ok else 'FAIL'}  {name}")
