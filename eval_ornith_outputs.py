"""
Manual eval of Ornith output accuracy for <=2 round tasks.
Runs each scenario, prints raw output + analysis.
"""
import json, os, sys, time
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "tools-harness"))
import ollama
from tools.registry import ALL_TOOLS, execute_tool
from tools.injection_guard import wrap_external_output

MODEL = "ornith:9b"
OPTS = dict(num_predict=4096, temperature=0.3, num_ctx=16384)

def chat(msgs, tools=None, label=""):
    start = time.time()
    resp = ollama.chat(model=MODEL, messages=msgs, tools=tools or None, options=OPTS, think=False)
    elapsed = time.time() - start
    content = resp.message.content or ""
    tcs = resp.message.tool_calls or []
    print(f"\n{'='*60}")
    print(f"  {label}  ({elapsed:.1f}s)")
    print(f"{'='*60}")
    print(content.strip() if content else "(empty)")
    for c in tcs:
        print(f"  >> TOOL: {c.function.name}({json.dumps(c.function.arguments)})")
    return content, tcs

def run_tool(name, args):
    try:
        r = execute_tool(name, args)
        return wrap_external_output(name, r)[:600]
    except Exception as e:
        return f"[error] {e}"

# Filter tool schemas once
TOOL_SETS = {
    "remember": [t for t in ALL_TOOLS if t["function"]["name"] in ("remember", "forget")],
    "ocr":      [t for t in ALL_TOOLS if t["function"]["name"] == "ocr_image"],
    "auth":     [t for t in ALL_TOOLS if t["function"]["name"] == "refresh_google_token"],
    "auto":     [t for t in ALL_TOOLS if t["function"]["name"] == "list_automations"],
    "telegram": [t for t in ALL_TOOLS if t["function"]["name"] == "send_telegram"],
    "whatsapp": [t for t in ALL_TOOLS if t["function"]["name"] == "send_whatsapp"],
}

def tool_then_synth(msgs, schemas, sys_prompt, label):
    msgs = [{"role": "system", "content": sys_prompt}] + msgs
    content, tcs = chat(msgs, tools=schemas, label=f"{label} (round 1)")
    if tcs:
        asst = {"role": "assistant", "content": content}
        if hasattr(tcs[0], "model_dump"):
            asst["tool_calls"] = [t.model_dump() for t in tcs]
        msgs.append(asst)
        for i, c in enumerate(tcs):
            r = run_tool(c.function.name, c.function.arguments)
            msgs.append({"role": "tool", "name": c.function.name, "tool_call_id": f"x{i}", "content": r})
        content2, tcs2 = chat(msgs, tools=schemas, label=f"{label} (round 2)")
        return content2, tcs
    return content, tcs


# ── 1. Classifier ────────────────────────────────────────────────
chat([{"role": "user", "content": (
    'Classify this message intent. Output ONLY JSON: {"intent": "...", "specialist_hint": null}\n'
    "Known intents: casual, coding, research, document, automation, messaging, data_analysis, web_browsing, planning, admin, git, followup\n"
    'Message: "search for latest python version 2026"\n'
)}], label="1. Classifier (should be research/coding)")

# ── 2. Search summarizer ─────────────────────────────────────────
chat([{"role": "user", "content": (
    "Synthesize these search results into a clear answer about Python versions and features.\n\n"
    '[{"title": "Python 3.14 Release", "snippet": "Python 3.14 released Oct 2025 with pattern matching, improved typing, faster runtime."}, '
    '{"title": "Python 3.13 Features", "snippet": "Python 3.13 introduced JIT compiler and improved error messages."}]'
)}], label="2. Search summarizer")

# ── 3. Doc summarizer ───────────────────────────────────────────
chat([{"role": "user", "content": (
    "Summarize in 3-5 bullet points. Focus on key facts, decisions, action items.\n\n"
    "Meeting Notes - Q3 Planning\n"
    "Action items:\n"
    "1. John to update API docs by Friday\n"
    "2. Sarah to review PR #342\n"
    "3. Deploy v2.1 to staging\n"
    "Decisions: Use PostgreSQL 17 for new service."
)}], label="3. Doc summarizer (should be 3-5 bullets)")

# ── 4. Chat followup (remember) ─────────────────────────────────
tool_then_synth(
    [{"role": "user", "content": "Remember that my favorite editor is Neovim"}],
    TOOL_SETS["remember"],
    "You have remember/forget tools. When user says 'remember', call remember().",
    "4. Remember tool"
)

# ── 5. OCR ───────────────────────────────────────────────────────
from PIL import Image, ImageDraw
img = Image.new("RGB", (300, 60), "white")
d = ImageDraw.Draw(img)
d.text((10, 10), "Hello OCR World", fill="black")
img.save("/tmp/ornith_eval_ocr.png")
tool_then_synth(
    [{"role": "user", "content": "Extract text from this image: /tmp/ornith_eval_ocr.png"}],
    TOOL_SETS["ocr"],
    "You have ocr_image tool. Call it with image_path, then report the extracted text.",
    "5. OCR"
)
os.remove("/tmp/ornith_eval_ocr.png")

# ── 6. Refresh auth ─────────────────────────────────────────────
tool_then_synth(
    [{"role": "user", "content": "Google APIs returning 401 errors. Refresh Google token."}],
    TOOL_SETS["auth"],
    "You have refresh_google_token(). Call it immediately, then report success or error.",
    "6. Auth refresh"
)

# ── 7. List automations ─────────────────────────────────────────
tool_then_synth(
    [{"role": "user", "content": "Show me what automations are running."}],
    TOOL_SETS["auto"],
    "You have list_automations(). Call it and present results.",
    "7. List automations"
)

# ── 8. Send telegram ────────────────────────────────────────────
tool_then_synth(
    [{"role": "user", "content": "Send Telegram message: 'Hello from Ornith test'"}],
    TOOL_SETS["telegram"],
    "You have send_telegram(message). Call it with user's message, then confirm.",
    "8. Send Telegram"
)

# ── 9. Send whatsapp ────────────────────────────────────────────
tool_then_synth(
    [{"role": "user", "content": "Send WhatsApp to 14155552671 saying 'Test from Ornith'"}],
    TOOL_SETS["whatsapp"],
    "You have send_whatsapp(to, message). Call it with user's number and message, then confirm.",
    "9. Send WhatsApp"
)

print("\n\n=== DONE ===")
