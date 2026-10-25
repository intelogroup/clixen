"""Lean agent mode for the Clixen orb (chat_id starting "orb") and for voice.

The orb drives the user's own screen, so it gets the screen tools plus a few lookups and a short
prompt written for that job, instead of the chat coordinator's ~84 tools and ~3.5k-token prompt.
Voice surfaces (chat_id "voice_agent") get the same tool set with spoken-reply rules appended,
because they were silently falling through to the coordinator profile.
Telegram, WhatsApp and the web UI are untouched: they keep the full orchestrator.
"""

from __future__ import annotations

import os

ORB_CHAT_PREFIX = "orb"

TOOL_NAMES = frozenset({
    # the user's screen (same set harness.py offers the orchestrator)
    "observe", "act", "wait_for", "tabs", "app_task", "read_page_text", "open_url", "open_app", "finder", "window",
    "menu", "app", "dialog", "system", "get_current_time",
    # Fallbacks for apps that expose no accessibility tree. observe()'s own
    # "no accessibility tree" message tells the model to use get_screen_context
    # or click_text — before 2026-10-24 neither was in this list, so the orb was
    # handed advice it was not allowed to follow (canvas UIs, games, some
    # Electron views: blind, then out of ideas). click_text also covers "click X"
    # by visible label, and type_text is what makes that fallback useful at all.
    "get_screen_context", "click_text", "type_text",
    # Direct data tools. These replaced the subagent versions because
    # ask_calendar_agent / ask_tasks_agent route to the LOCAL Ollama model by
    # design (harness._get_optimized_opts), so with Ollama down — the normal
    # state on this machine, verified 2026-10-24 — every calendar ask failed
    # with "Failed to connect to Ollama" after a whole round. The direct tools
    # hit the Google/Apple APIs instead: one call, no local model, no round.
    "get_latest_email", "list_emails", "read_email",
    "list_calendar_events", "create_calendar_event",
    "list_tasks", "complete_task", "list_reminders",
    # lookups an on-screen task needs
    "ask_web_search", "ask_fetch_url", "ask_research_agent",
    "ask_browser_agent",
    # Screenshot + OCR locally: take_screenshot works from any model, while
    # ask_vision_agent routes to the local Ollama model for anything that is not
    # an image the caller already has (it failed that way 2026-09-23, and Ollama
    # is normally down here), so "take a screenshot" was unanswerable.
    "take_screenshot", "ocr_image",
    # progress and memory
    "update_task_plan", "remember",
    # Every tool's description reaches the model shortened; this is how it reads
    # the full text for the one call it is unsure about (see tools/lazy_schema.py).
    "tool_help",
})

PROMPT = """You are Clixen, the agent behind the floating orb. A pixel bunny shows the user what you do: it follows every click and keystroke on their real Mac screen. Do the task yourself, to the end.

Tool descriptions are shortened to the first sentence. When a call is not obvious, read the full text with tool_help(name) first.

YOUR SCREEN TOOLS
- The turn starts with a SCREEN block: app, window, url, focus, read live for this turn. It is authoritative for "what app is in front", "what page is this", "what URL is this" — answer those from it, do not call observe() or get_screen_context for them. observe is only for element ids to click, or when the page changed mid-task.
- observe(): app, title, URL, focus, overlays in the way, and numbered elements (ids like e3.14) with role, label and text-field values.
- act(target, action, text, expect): click / type / key / scroll one thing; target is an element id from observe copied exactly, or its label. Returns what changed: {ok, effect:{focus,title,appeared,gone}} or {ok:false, error:{code,hint,candidates}}; a click that changed nothing warns "no_effect". Extension side panels are closed for you.
- wait_for(expect, timeout): wait until the screen shows something. tabs(action="list"|"switch"|"close", match): list, switch or close tabs by title or URL. app_task(name, args): known jobs end to end.
- No accessibility tree? Canvas UIs, games and some Electron views expose no elements and observe() says so. Then get_screen_context to look, click_text to click by label, type_text to type. Do not retry observe expecting a new answer.

HOW YOU WORK
1. Any task with 3+ steps: call update_task_plan(steps=[...]) first, tick steps with mark_done only when the screen shows them done.
2. observe before you act, and after anything that may have changed the page. A stale id is rejected safely; it never hits another element.
3. Read every act result. ok:false codes: ambiguous (pick a candidate id), not_found (candidate or observe), stale (observe again), not_editable (target the field itself), expect_failed (what you waited for did not show). Do not repeat a failed call unchanged.
4. To fill a form or run a known sequence, pass act(steps=[{target, action, text}, ...]): one call, in order, stops at the first failure — prefer it to a call per field. Correcting a field that already has text: type with replace=true. Use expect when a result must appear, e.g. expect={"appears": "Message sent"}.
5. Never say a step is done unless act's effect or a fresh observe shows it. Never say a window closed or a field is empty without a fresh observe.
6. Do not stop after one failure. Try two different approaches before saying you are blocked, and say exactly what blocked you.
7. In Gmail's compose, 'Describe your message' is an AI prompt box, never the body; the body is 'Message Body'. Prefer the compose link below.
8. Reuse what is open: check before opening another tab or window.

ON SCREEN, NOT BEHIND IT
When the user names an app or site, do the task by operating it with observe and act so they can watch the bunny. Lookup tools (ask_web_search, ask_fetch_url, ask_research_agent) only gather facts. ask_browser_agent drives a browser the user cannot see: only for a site that refuses to be driven on screen, never to click what they are watching.

EMAIL, CALENDAR, TASKS
Reading mail, calendar or to-dos is a lookup, not a screen action, and does not depend on what is in front: get_latest_email (newest, full body), list_emails (Gmail search syntax), read_email(id), list_calendar_events, create_calendar_event, list_tasks, complete_task, list_reminders. One call each; do not open Gmail in a tab to read mail. Take any absolute date from the date line in your context.
Sending is the exception: it happens on screen via app_task("gmail.send", {to, subject, body}); you write subject and body (body under ~1500 chars), it clicks Send itself and confirms with a read-only Gmail check. Returns sent:true with confirmed_by or sent:false. If false, the mail may still have gone out: check the inbox, never send the same mail twice, and never hand-click Gmail's compose window.

SENDING OR POSTING
Told to send something? That is your authorization: do it. Before the final click, confirm recipient, subject and body match the ask; after sending, confirm it and say so.

NEWS AND FACTS
Search results rarely match an exact date: use the latest items, say what dates they carry, never invent items, and never refuse a task because nothing is stamped today.

REPLY
When done: one short plain paragraph. What you did, what you verified, what you did not."""


def is_orb_chat(chat_id) -> bool:
    return isinstance(chat_id, str) and chat_id.startswith(ORB_CHAT_PREFIX)


# Voice surfaces (the orb's mic, PTT, the wake listener, brabble_hook) all write
# history under voice.agent.VOICE_CHAT_ID. That value does not start with "orb",
# so the lean-profile gate below used to miss it entirely and every spoken turn
# ran the chat coordinator's ~84-tool, ~17k-token prompt instead of this one —
# measured at 16_466-16_992 prompt tokens per round on the voice path versus
# 5_533 on the orb path, i.e. ~0.3-0.5 s of extra first-token latency on every
# round of every spoken turn, plus far worse tool selection.
VOICE_CHAT_IDS = frozenset({"voice_agent", "brabble_voice"})


def is_voice_chat(chat_id) -> bool:
    return isinstance(chat_id, str) and chat_id in VOICE_CHAT_IDS


def voice_extra_tool_names() -> frozenset[str]:
    """Extra tools voice may use on top of the orb set.

    Empty by default (the orb set is the fastest and the most predictable), but
    settable per machine with CLIXEN_VOICE_EXTRA_TOOLS="set_reminder,get_current_time"
    so the "should voice also answer non-screen asks?" experiment is a config
    change, not a code change.
    """
    raw = os.environ.get("CLIXEN_VOICE_EXTRA_TOOLS", "")
    return frozenset(part.strip() for part in raw.split(",") if part.strip())


# Tools voice does not get, even though the orb does. `get_current_time` is pure
# round-waste for a spoken turn: the current date and time are already injected as
# a context block, but with the tool present the model called it for "What is
# 2+2?", "Say hello" and "What time is it?" alike — each call a whole extra round
# (+0.8-1.0 s) and it ate the entire prompt-size win measured 2026-10-24.
VOICE_EXCLUDED_TOOLS = frozenset({"get_current_time"})


def voice_tools(tools: list) -> list:
    """The orb's screen tools, minus voice's known round-wasters, plus whatever
    extras the machine opted into."""
    wanted = (TOOL_NAMES | voice_extra_tool_names()) - VOICE_EXCLUDED_TOOLS
    return [t for t in tools if t.get("function", {}).get("name") in wanted]


# Spoken replies are heard, not read: they need their own length and formatting
# rules, and an explicit "answer first" rule. Without it the orb's screen
# protocol ("observe before you act", "do the task yourself") made the lean
# prompt *slower* for plain questions: measured 2026-10-24, "What is 2+2?" and
# "Say hello in one short sentence." each spent a whole extra round on
# get_current_time/observe, which ate the entire prompt-size win.
VOICE_PROMPT_HEAD = """SPEAKING ALOUD
Your reply is spoken by a TTS voice: one or two short sentences, plain words, no markdown, no lists, no tables, no emoji, no URLs read out loud. Answer the question first, then at most one clarifying detail.

ANSWER DIRECTLY WHEN YOU CAN
If the question can be answered from your own knowledge, or from the context blocks below (time, date, memory, screen), answer immediately and call no tool. Do not observe the screen, read the clock or plan a task to say "four" or "hello". The current date and time are already given below, so there is no clock to read — take any absolute date a calendar, reminder or task tool needs from that same line. When a SCREEN block is present it already says which app and window are in front, which page is open and what has focus — answer from it, and call observe only when you need element ids to click. Use tools only when the user asked you to do something on their screen or the answer is not knowable without one.
"""


def screen_block(ctx: dict) -> str:
    """One compact line describing what is in front, or '' when unknown.

    Injected into the orb/voice prompt so "what app is in front?" and "what page
    is this?" are answered from context instead of costing an observe() round.
    Kept terse on purpose: it is prepended to every spoken turn, so it is a
    latency cost as well as a context win.
    """
    if not ctx or not ctx.get("app"):
        return ""
    bits = [f"app={ctx['app']}"]
    if ctx.get("window"):
        bits.append(f'window="{ctx["window"][:80]}"')
    if ctx.get("url"):
        bits.append(f"url={ctx['url'][:120]}")
    focus = ctx.get("focus_label") or ctx.get("focus_value")
    if focus:
        role = ctx.get("focus_role") or "element"
        bits.append(f'focus={role} "{focus[:60]}"')
    if ctx.get("popovers"):
        bits.append("popovers=" + "; ".join(p[:40] for p in ctx["popovers"][:3]))
    return (
        "SCREEN (snapshot taken for this turn, no tool call needed): "
        + " | ".join(bits)
    )


def voice_prompt(*blocks: str) -> str:
    """The spoken-reply rules first (they are the strongest signal), then context,
    then the orb's screen protocol for tasks that do need it."""
    return "\n".join([VOICE_PROMPT_HEAD] + [b for b in blocks if b] + [PROMPT])


def system_prompt_with_screen(ctx: dict, *blocks: str) -> str:
    """The orb's prompt with the screen snapshot prepended ('' when unavailable)."""
    block = screen_block(ctx)
    return system_prompt(*(  ([block] if block else []) + list(blocks)))


def lean_tools(tools: list) -> list:
    """The orb's tool list, selected by name from the registry.

    Callers must pass the FULL registry list (harness.ALL_TOOLS), not the
    orchestrator's list. It used to filter the orchestrator list, which meant a
    name in TOOL_NAMES that the orchestrator list happened not to carry was
    silently unavailable — "read me the last email" got get_screen_context
    twice instead of get_latest_email, because the data tools only ever
    appeared in per-intent lists, never the orchestrator's (found 2026-10-24 by
    running the orb against a real inbox).
    """
    return [t for t in tools if t.get("function", {}).get("name") in TOOL_NAMES]


def system_prompt(*blocks: str) -> str:
    """The lean prompt behind any context blocks (time, memory, plan), skipping empty ones."""
    return "\n".join([b for b in blocks if b] + [PROMPT])
