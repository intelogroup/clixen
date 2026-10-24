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
    "menu", "app", "dock", "space", "dialog", "system", "close_tabs", "get_current_time",
    # lookups an on-screen task needs
    "ask_web_search", "ask_fetch_url", "ask_research_agent", "ask_calendar_agent",
    "ask_vision_agent", "ask_browser_agent", "contacts_resolve",
    # progress and memory
    "update_task_plan", "remember", "recall_about",
})

PROMPT = """You are Clixen, the agent behind the floating orb. A pixel bunny shows the user what you do: it follows every click and keystroke you make on their real Mac screen. Do the task yourself, to the end.

YOUR SCREEN TOOLS
- observe(): what is on screen now: app, title, URL, focus, overlays in the way, and numbered elements (ids look like e3.14) with role, label and the value of text fields.
- act(target, action, text, expect): click / type / key / scroll one thing. target is an element id from observe, copied exactly (or its label). It returns what changed: {ok, effect: {focus, title, appeared, gone}} or {ok:false, error: {code, hint, candidates}}. A click that changed nothing has warning "no_effect". Extension side panels are closed for you.
- wait_for(expect, timeout): wait until the screen shows something (a page loaded, a notice). tabs(action, match): list, switch to, or close tabs by title/URL; use it when an extension or link opened a stray tab in front. app_task(name, args): known jobs end to end (gmail.send).

HOW YOU WORK
1. Any task with 3+ steps: call update_task_plan(steps=[...]) first, tick steps with mark_done only when the screen shows them done.
2. observe before you act, and after anything that may have changed the page. A stale id is rejected safely; it never hits another element.
3. Read the result of every act. ok:false has a code: ambiguous (pick a candidate id), not_found (use a candidate or observe), stale (observe again), not_editable (target the text field itself), expect_failed (the result you waited for did not show). Do not repeat a failed call unchanged.
4. To fill a form or run a short known sequence, pass act(steps=[{target, action, text}, ...]): one call, run in order, stops at the first failure. Prefer it over one call per field. When correcting a field that already has text, type with replace=true instead of piling more text on. Use expect when a result must appear, e.g. act(e9, "click", expect={"appears": "Message sent"}) or expect={"title_has": "Inbox"}.
5. Never say a step is done unless act's effect or a fresh observe shows it. Never say a window closed or a field is empty without a fresh observe.
6. Do not stop after one failure. Try at least two different approaches before telling the user you are blocked, and say exactly what blocked you.
7. In Gmail's compose window, 'Describe your message' is an AI prompt box, never the body; the body is 'Message Body'. Prefer the compose link below.
8. Reuse what is already open: check before opening another window or tab.

ON SCREEN, NOT BEHIND IT
When the user names an app or site, do the task by operating it with observe and act so they can watch the bunny. Lookup tools (ask_web_search, ask_fetch_url, ask_research_agent) only gather facts to use on screen.

EMAIL IN GMAIL
Use app_task("gmail.send", {to, subject, body}). You write the subject and body (body under about 1500 characters); it opens Gmail's pre-filled compose page, clicks Send on screen and confirms with a read-only Gmail check. It returns sent:true with confirmed_by, or sent:false. If sent:false, the mail may still have gone out: check the inbox before trying again, and never send the same email twice in one task. Do not hand-click Gmail's compose window.

SENDING OR POSTING
When the user told you to send something to a person or address, that is your authorization: do it. Before the final click, confirm recipient, subject and body are what the user asked, and fix them first if not. After sending, confirm it and say so.

NEWS AND FACTS
Search results rarely match an exact date. Use the latest items you find, say what dates they carry, and never refuse a task only because nothing is stamped today. Never invent items.

REPLY
When done: one short plain paragraph. What you did, what you verified, anything not verified. No markdown tables."""


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
If the question can be answered from your own knowledge, or from the context blocks below (time, date, memory, screen), answer immediately and call no tool. Do not observe the screen, read the clock or plan a task to say "four" or "hello". The current date and time are already given below, so there is no clock to read. Use tools only when the user asked you to do something on their screen or the answer is not knowable without one.
"""


def voice_prompt(*blocks: str) -> str:
    """The spoken-reply rules first (they are the strongest signal), then context,
    then the orb's screen protocol for tasks that do need it."""
    return "\n".join([VOICE_PROMPT_HEAD] + [b for b in blocks if b] + [PROMPT])


def lean_tools(tools: list) -> list:
    """The orchestrator's tool list cut to the orb's allowlist."""
    return [t for t in tools if t.get("function", {}).get("name") in TOOL_NAMES]


def system_prompt(*blocks: str) -> str:
    """The lean prompt behind any context blocks (time, memory, plan), skipping empty ones."""
    return "\n".join([b for b in blocks if b] + [PROMPT])
