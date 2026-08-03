"""
Handler: user.automation
Generic dispatcher for user-created automations (tools/automation_tools.py's
create_automation). Unlike the builtin handlers, these don't get their own
automation_id/handler pair — they carry action_type + config directly on the
workflow_instance row, so one handler reads that and acts.
"""
from __future__ import annotations

import logging
import os
import random
import re
import threading
import time
from datetime import datetime, timedelta

_log = logging.getLogger(__name__)

# ponytail: was str.format_map with keys like "step.notify" — Python's .format()
# parses a dotted name as attribute access (vd["step"].notify), not a literal key
# lookup, so single-brace refs raised AttributeError (silently swallowed by a bare
# except, template left unsubstituted) and double-brace refs (the smoke test's own
# example!) parsed as escaped literal braces and never substituted at all —
# cross-step data passing in workflow automations has never actually worked.
# Regex substitution on a `$stepname.field` token sidesteps str.format's semantics
# entirely. Module-level (not nested in handle()) so it's directly unit-testable.
_STEP_REF_RE = re.compile(r"\$([a-zA-Z_][a-zA-Z0-9_]*)\.([a-zA-Z_][a-zA-Z0-9_]*)")


def _llm_json_extract(prompt: str, schema: dict, model: str | None = None) -> dict:
    """Extract JSON per `schema` — ollama first (native JSON-mode `format`),
    cloud fallback on failure (local models aren't reliably loaded in this
    env — qwen3.5:4b 404'd in tests 2026-07-30). Cloud path has no native
    format=, so it instructs JSON-only and re-parses."""
    import json as _json
    model = model or os.environ.get("OLLAMA_REWRITE_MODEL", "qwen3.5:4b")
    from clients.cloud_client import is_cloud_model
    if not is_cloud_model(model):
        try:
            import ollama as _ollama
            resp = _ollama.Client().chat(
                model=model,
                messages=[{"role": "user", "content": prompt}],
                format=schema,
                think=False,
                options={"temperature": 0},
            )
            return _json.loads(resp["message"]["content"])
        except Exception as exc:
            _log.warning("llm_extract: local ollama (%s) failed, falling back to cloud: %s", model, exc)
    from clients.cloud_client import DEFAULT_CLOUD_MODEL, raw_completion
    choice = raw_completion(
        model=DEFAULT_CLOUD_MODEL,
        messages=[
            {"role": "system", "content": "Return ONLY valid JSON matching the requested schema. No markdown, no code fences, no commentary."},
            {"role": "user", "content": prompt},
        ],
        tools=[],
        temperature=0,
        bypass_budget=True,
    )
    text = (choice.message.content or "").strip()
    if text.startswith("```"):
        text = re.sub(r"^```(?:json)?\s*|\s*```$", "", text)
    return _json.loads(text)


def _interp(obj, results: dict):
    """Substitute $stepname.field tokens in obj (str/dict/list, recursive) against
    a {step_name: {"success":..., "result"|"error":...}} results dict."""
    if isinstance(obj, str):
        def _sub(m):
            step_name, field = m.group(1), m.group(2)
            val = results.get(step_name, {}).get(field, "")
            return str(val)
        return _STEP_REF_RE.sub(_sub, obj)
    if isinstance(obj, dict):
        return {k: _interp(v, results) for k, v in obj.items()}
    if isinstance(obj, list):
        return [_interp(v, results) for v in obj]
    return obj


def _wrap_html(task_name: str, date_str: str, body: str) -> str:
    """Wrap a plain-text briefing body in the standard clixen email chrome."""
    import html as _html
    styled = (body.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
              .replace("\n", "<br>\n"))
    return (
        '<!DOCTYPE html><html><head><meta charset="utf-8">'
        '<meta name="viewport" content="width=device-width,initial-scale=1"></head>'
        '<body style="margin:0;padding:0;background:#f4f5f7;'
        "font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',Roboto,Helvetica,Arial,sans-serif;"
        'color:#1a1a1a;">'
        '<table width="100%" cellpadding="0" cellspacing="0" style="background:#f4f5f7;padding:24px 0;">'
        '<tr><td align="center"><table width="600" cellpadding="0" cellspacing="0" '
        'style="max-width:600px;width:100%;background:#ffffff;border-radius:12px;overflow:hidden;'
        'box-shadow:0 1px 3px rgba(0,0,0,0.08);">'
        f'<tr><td style="background:linear-gradient(135deg,#0f172a,#1e3a8a);padding:28px 32px;">'
        f'<div style="font-size:20px;font-weight:700;color:#ffffff;letter-spacing:-0.01em;">'
        f'{_html.escape(task_name)}</div>'
        f'<div style="font-size:13px;color:#cbd5e1;margin-top:4px;">{_html.escape(date_str)}</div></td></tr>'
        f'<tr><td style="padding:24px 32px;font-size:14px;color:#1a1a1a;line-height:1.6;">{styled}</td></tr>'
        f'<tr><td style="padding:20px 32px;background:#f8fafc;border-top:1px solid #e2e8f0;'
        f'font-size:12px;color:#94a3b8;">Auto-generated by clixen. '
        f'Manage this briefing in your automations.</td></tr>'
        '</table></td></tr></table></body></html>'
    )


def _synthesize_news_email(*, task_name, date_str, topics, source_pack, recipient, subject):
    """LLM-craft a substance-first briefing (the 'grain', not headlines).

    Returns send_email kwargs {to, subject, body, html_body}, or None on any
    failure so the caller can fall back to the title+snippet digest.
    """
    tool_defs = [{
        "type": "function",
        "function": {
            "name": "send_email",
            "description": "Send an email from the authenticated Gmail account.",
            "parameters": {
                "type": "object",
                "properties": {
                    "to": {"type": "string"},
                    "subject": {"type": "string"},
                    "body": {"type": "string", "description": "Plain-text email body. NO markdown, NO asterisks."},
                    "html_body": {"type": "string", "description": "Optional HTML version of the body."},
                },
                "required": ["to", "subject", "body"],
            },
        },
    }]

    prompt = (
        f"You curate an AI/robotics news briefing for a busy founder/operator who already knows "
        f"the headline buzzwords. They do NOT want a list of headlines \u2014 they want the signal: "
        f"what actually happened and WHY IT MATTERS strategically. Today's watch topics: {topics}.\n\n"
        "Write a plain-text email with:\n"
        "1. A short lead ('WHAT YOU NEED TO KNOW') \u2014 2-4 sentences synthesizing the day's real "
        "strategic signal across these stories (not a recap of titles). Lead with business/market "
        "impact: who gains, who's threatened, what shifts for operators and founders.\n"
        "2. One short section per story: 2-3 sentences in plain English on what happened and the "
        "concrete takeaway ('the grain') \u2014 the business, market, or competitive implication a "
        "top operator should act on. Name the source and put its URL on its own line. Do NOT just "
        "restate the headline.\n\n"
        "NO markdown, NO asterisks, NO hashtags. Use CAPS for the lead header and each story title, "
        "dashes for lists, blank lines between sections.\n\n"
        f"SOURCES:\n{source_pack}"
    )
    messages = [
        {"role": "system", "content": (
            "You write plain-text emails. NO markdown, NO asterisks, NO hashtags. "
            "CAPS headers, dashes for lists, blank lines between sections."
        )},
        {"role": "user", "content": prompt},
    ]
    opts = {"num_ctx": 16384, "num_predict": 2048, "temperature": 0.5, "top_p": 0.85, "repetition_penalty": 1.05}
    # Cloud-first (2026-07-31): local gemma4:12b-mlx is not reliably loaded in
    # this cloud-first env (404 on live runs) — cloud primary (with the
    # built-in OpenAI fallback in raw_completion), local ollama kept as a last
    # resort only.
    try:
        from clients.cloud_client import DEFAULT_CLOUD_MODEL, raw_completion
        choice = raw_completion(
            model=DEFAULT_CLOUD_MODEL,
            messages=messages,
            tools=tool_defs,
            bypass_budget=True,
        )
        msg = {
            "message": {
                "content": (choice.message.content or ""),
                "tool_calls": [
                    {
                        "function": {
                            "name": tc.function.name,
                            "arguments": tc.function.arguments,
                        }
                    }
                    for tc in (choice.message.tool_calls or [])
                ],
            }
        }
    except Exception as exc:
        _log.warning("user.automation: cloud news synthesis failed (%s), trying local ollama", exc)
        try:
            from clients.ollama_client import _get_client, DEFAULT_MODEL as _LOCAL_DEFAULT
            client = _get_client()
            resp = client.chat(model=_LOCAL_DEFAULT, messages=messages, tools=tool_defs,
                               keep_alive=-1, options=opts, think=False)
            msg = resp
        except Exception as exc2:
            _log.warning("user.automation: news synthesis call failed (%s)", exc2)
            return None

    msg = resp.get("message", {})
    tool_calls = msg.get("tool_calls", [])
    if tool_calls:
        fn = tool_calls[0].get("function", {})
        args = fn.get("arguments", "{}")
        if isinstance(args, str):
            try:
                import json
                args = json.loads(args)
            except Exception:
                return None
        body = (args.get("body") or "").replace("**", "").replace("*", "")
        args["body"] = body
        args.setdefault("to", recipient)
        args.setdefault("subject", subject)
        if not args.get("html_body") and body:
            args["html_body"] = _wrap_html(task_name, date_str, body)
        return args
    content = (msg.get("content") or "").replace("**", "").replace("*", "")
    if content:
        return {"to": recipient, "subject": subject, "body": content,
                "html_body": _wrap_html(task_name, date_str, content)}
    return None


def handle(instance: dict) -> dict:
    action_type = instance.get("action_type", "")
    config = dict(instance.get("config") or {})
    task_name = instance.get("task_name", "automation")

    try:
        if action_type == "telegram":
            from tools.telegram_send import send_telegram
            query = config.get("query", "")

            if query:
                # ponytail: same seen-IDs dedupe pattern as email.watch_sender.py
                # and the pubmed email branch below, applied generically to any
                # telegram automation that carries a Gmail `query` — so a
                # workflow like this doesn't just re-blast a static message
                # every tick, and any future create_automation(action_type=
                # "telegram", query=...) call gets real detection for free.
                import re as _re
                from tools.gmail import list_emails

                senders = config.get("senders") or []
                gmail_query = query
                if senders:
                    from_clause = " OR ".join(f'from:"{s}"' for s in senders)
                    gmail_query = f"({query}) ({from_clause})"

                listing = list_emails(query=gmail_query, max_results=config.get("max_results", 10))
                if listing.startswith("[gmail error]"):
                    return {"success": False, "error": listing}
                if listing == "No emails found.":
                    return {"success": True, "detail": "no matching emails", "checked": True, "new_items": 0}

                items = []
                for block in listing.split("\n\n"):
                    m_id = _re.search(r"^ID:\s*(\S+)", block, _re.M)
                    m_from = _re.search(r"^\s*From:\s*(.+)$", block, _re.M)
                    m_subj = _re.search(r"^\s*Subject:\s*(.+)$", block, _re.M)
                    if m_id:
                        items.append({
                            "id": m_id.group(1).strip(),
                            "sender": m_from.group(1).strip() if m_from else "?",
                            "subject": m_subj.group(1).strip() if m_subj else "(no subject)",
                        })

                seen = set(config.get("seen_ids", []))
                new_items = [it for it in items if it["id"] not in seen]
                if not new_items:
                    return {"success": True, "detail": "no new emails", "checked": True, "new_items": 0}

                template = config.get("message") or (
                    f"Automation '{task_name}' matched a new email.\n\nFrom: {{sender}}\nSubject: {{subject}}"
                )
                sent_ok = True
                for it in new_items:
                    msg = template.format(sender=it["sender"], subject=it["subject"])
                    result = send_telegram(msg)
                    sent_ok = sent_ok and ("error" not in result.lower())

                seen.update(it["id"] for it in new_items)
                from store import workflow_store
                workflow_store.update_workflow_instance(
                    instance["id"],
                    config={**config, "seen_ids": list(seen)[-500:]},
                )
                return {"success": sent_ok, "detail": f"{len(new_items)} new match(es) sent", "new_items": len(new_items)}

            message = config.get("message") or f"Automation '{task_name}' triggered."
            result = send_telegram(message)
            return {"success": "error" not in result.lower(), "detail": result}

        if action_type == "notification":
            from store import workflow_store
            message = config.get("message") or f"Automation '{task_name}' triggered."
            workflow_store.add_notification(message=message, source="automation", level="info")
            return {"success": True}

        if action_type == "imessage":
            import os
            from tools.imessage_search import send as send_imessage

            watch_contact = config.get("watch_contact", "")
            if watch_contact:
                # ponytail: watch+auto-reply mode, same seen-ids-by-cursor shape
                # as the telegram action's email watch branch above, but keyed
                # by iMessage ROWID (search() has no stable id to dedupe on,
                # see list_new_from()'s docstring) instead of a seen_ids set.
                from tools.imessage_search import list_new_from
                from tools.registry import is_error_result

                since_rowid = int(config.get("seen_rowid", 0) or 0)
                _log.info("imessage poll contact=%s since_rowid=%d", watch_contact, since_rowid)
                new_msgs = list_new_from(watch_contact, since_rowid=since_rowid, limit=20)
                if isinstance(new_msgs, str):
                    # list_new_from() returns a list on success, an
                    # "[imessage error] ..."/FDA-hint string on failure —
                    # is_error_result() assumes a string, so only call it
                    # once we know we actually have one.
                    if is_error_result(new_msgs):
                        _log.warning("imessage poll error: %s", new_msgs)
                        return {"success": False, "error": new_msgs}
                    new_msgs = []
                if not new_msgs:
                    return {"success": True, "detail": "no new messages", "checked": True, "new_items": 0}
                _log.info("imessage poll: %d new from %s — rowids %d..%d",
                          len(new_msgs), watch_contact,
                          new_msgs[0]["rowid"], new_msgs[-1]["rowid"])
                for m in new_msgs:
                    _log.info("imessage poll msg rowid=%d text=%r", m["rowid"], m["text"][:200])

                from store import workflow_store

                decline_template = config.get("decline_message") or "Sorry, not able."
                blackout_days = {d.strip().lower() for d in config.get("blackout_days") or []}
                skip_keywords = [k.lower() for k in (config.get("skip_keywords") or [])]
                blacklist_locations = [loc.lower() for loc in (config.get("blacklist_locations") or [])]
                earliest_hour = int(config.get("earliest_start_hour", 0) or 0)
                check_calendar = bool(config.get("check_calendar", False))
                calendar_buffer = int(config.get("calendar_buffer_minutes", 60) or 60)
                template = config.get("reply_message") or f"Automation '{task_name}' auto-reply."

                def _fmt_time(hhmm: str) -> str:
                    try:
                        h, mnt = (int(p) for p in hhmm.split(":", 1))
                    except (ValueError, AttributeError):
                        return hhmm or ""
                    suffix = "AM" if h < 12 else "PM"
                    h12 = h % 12 or 12
                    return f"{h12}:{mnt:02d}{suffix}"

                def _queue_send(to: str, message: str, ctx: str) -> bool:
                    # ponytail: random 60-180s delay before the actual send,
                    # off the shared worker thread (a Timer-style daemon
                    # thread) — an instant reply on every single message is
                    # what makes an auto-responder read as a bot; a human
                    # takes a beat. Must NOT block handle() itself: this
                    # workflow's cron fires every minute in the same
                    # task_worker thread every other automation shares, so a
                    # synchronous 1-3min sleep here would stall all of them.
                    # Real send success/failure is only known after the
                    # delay, so it's logged async, not fed back into this
                    # poll's result — seen_rowid already advances
                    # unconditionally (no-retry fix), so there's nothing to
                    # gate on here. Known gap: if core.py restarts while a
                    # send is queued, the daemon thread dies with it and
                    # that reply is silently dropped (never sent) — a
                    # restart within the 1-3min window is the tradeoff for
                    # not blocking the worker; acceptable at current volume.
                    delay = random.uniform(60, 180)

                    def _delayed():
                        time.sleep(delay)
                        result = send_imessage(to=to, message=message)
                        if is_error_result(result):
                            _log.warning("imessage poll: delayed send failed (%s) after %.0fs: %s",
                                         ctx, delay, result)
                        else:
                            _log.info("imessage poll: delayed send ok (%s) after %.0fs", ctx, delay)

                    try:
                        threading.Thread(target=_delayed, daemon=True).start()
                    except Exception as exc:
                        _log.warning("imessage poll: failed to queue send (%s): %s", ctx, exc)
                        return False
                    _log.info("imessage poll: queued send in %.0fs (%s)", delay, ctx)
                    return True

                def _decline(reason: str, detail: str = "") -> bool:
                    log.append(f"declined ({reason})")
                    return True

                def _extract_assignments(text: str) -> list[dict]:
                    # Structured-output extraction replaces regex segmentation —
                    # the input space (free-form assignment text: bullets,
                    # numbered lists, run-on sentences) is NOT bounded, so a
                    # fixed separator regex silently drops assignments it
                    # wasn't written for (see: bulleted 2-assignment message
                    # that got zero replies because the segmenter never split
                    # it and the whole-message MM/DD scan only found the first
                    # date). Ollama's `format` JSON-schema param uses
                    # constrained decoding — the model cannot emit a token
                    # sequence that violates the schema, so segmentation and
                    # classification (offer vs confirmation vs cancellation)
                    # are as reliable as a regex match without being bounded
                    # to one sender's format.
                    #
                    # The model is asked to COPY the date/time substrings
                    # verbatim, not convert them — date math (MM/DD -> ISO,
                    # year rollover, 12h -> 24h) is done deterministically in
                    # Python below. Confirmed live: asking the model to do the
                    # conversion itself silently miscomputes ~1 in 3 dates
                    # (e.g. "07/24" -> "2026-08-03") even at temperature=0,
                    # despite the schema guaranteeing valid JSON *shape* — a
                    # schema can't guarantee the *values* are arithmetically
                    # correct, and small models are unreliable at date math.
                    import json
                    import ollama as _ollama
                    schema = {
                        "type": "object",
                        "properties": {
                            "assignments": {
                                "type": "array",
                                "items": {
                                    "type": "object",
                                    "properties": {
                                        "location": {"type": "string"},
                                        "raw_date": {"type": "string"},
                                        "raw_time": {"type": "string"},
                                    },
                                    "required": ["location", "raw_date"],
                                },
                            }
                        },
                        "required": ["assignments"],
                    }
                    from tools._time_context import now_line
                    prompt = (
                        f"{now_line()} Extract every shift/assignment from "
                        f"this message that NEEDS A FRESH YES/NO DECISION: a new offer, or a message "
                        f"saying an already-offered assignment's date/time/location CHANGED (extract "
                        f"the NEW date/time/location — a change needs to be re-checked and re-answered "
                        f"even if the message doesn't literally ask a question). Copy raw_date and "
                        f"raw_time EXACTLY as written in the message (e.g. \"7/24\", \"07-20\", "
                        f"\"9am-11am\", \"12:50pm-4:38pm\") — do not convert or compute anything, just "
                        f"copy the substring. Omit raw_time if no time is stated. Return an empty list "
                        f"if the message needs NO decision — e.g. it's a thank-you, a cancellation "
                        f"notice, or a CONFIRMED/confirmation receipt that restates the SAME "
                        f"already-accepted details with nothing changed (those mention a date/location "
                        f"too but aren't asking anything).\n\nMessage:\n{text}"
                    )
                    try:
                        data = _llm_json_extract(prompt, schema, model="qwen3.5:4b")
                    except Exception as exc:
                        _log.warning("imessage poll: assignment extraction failed (%s)", exc)
                        return []
                    # _llm_json_extract returns the full parsed object, not the
                    # array — unwrap or the loop below iterates the dict's keys
                    # and `.get()` blows up on the "assignments" string
                    # (confirmed live 2026-08-01: 'str' object has no attribute
                    # 'get'). Cloud fallback can also emit a bare list of
                    # strings off-schema despite the JSON-only instruction —
                    # drop non-dict entries defensively.
                    if isinstance(data, dict):
                        data = data.get("assignments", [])
                    return [a for a in data if isinstance(a, dict)] if isinstance(data, list) else []

                def _resolve_raw_date(raw: str) -> "datetime | None":
                    # ponytail: MM/DD (or MM-DD) only, this sender's format —
                    # assume current year, roll to next year if that lands
                    # more than a month in the past.
                    date_match = re.search(r"\b(\d{1,2})[/-](\d{1,2})\b", raw or "")
                    if not date_match:
                        return None
                    month, day = int(date_match.group(1)), int(date_match.group(2))
                    now = datetime.now()
                    try:
                        candidate = now.replace(year=now.year, month=month, day=day,
                                                 hour=0, minute=0, second=0, microsecond=0)
                    except ValueError:
                        return None
                    if candidate < now - timedelta(days=30):
                        candidate = candidate.replace(year=now.year + 1)
                    return candidate

                def _resolve_raw_time_range(raw: str) -> tuple[str | None, str | None]:
                    times = re.findall(r"\b(\d{1,2})(?::(\d{2}))?\s*([AaPp][Mm])\b", raw or "")
                    if not times:
                        return None, None

                    def _to_24h(h: str, mnt: str, ap: str) -> str:
                        hour = int(h) % 12
                        if ap.lower() == "pm":
                            hour += 12
                        return f"{hour:02d}:{int(mnt or 0):02d}"

                    start = _to_24h(*times[0])
                    end = _to_24h(*times[1]) if len(times) > 1 else None
                    return start, end

                accepted = 0
                log: list[str] = []
                # If a reply/decline send hard-fails (both iMessage and SMS
                # ponytail: retry-on-failure was removed 2026-07-17 — the
                # verify-then-hold-back behavior was itself re-sending real
                # duplicate SMS to the contact every poll (confirmed live:
                # chat.db showed alternating failed-iMessage/succeeded-SMS
                # rows every ~15-30s for over an hour). Never retry: always
                # advance past every message, log-only on send failure.
                send_failed_rowid = None

                # ponytail: per-automation config, NOT a hardcoded phrase — this
                # started as a BCH-only fix (hardcoded "can you help" etc.) but
                # that lived in this SHARED handler, so it silently applied to
                # every other imessage/watch_contact automation too, whatever
                # their sender's actual phrasing (found while trigger-testing
                # the new automation-creation checklist, 2026-07-17). Each
                # automation supplies its own `trigger_phrases` list matching
                # its own sender's real wording; create_automation() requires
                # this field for watch_contact mode (see automation_tools.py)
                # so it's asked for at creation time, not discovered live.
                # No phrases configured = no filter (every message is a
                # candidate) — the safe default for an automation that never
                # said it needs one, rather than silently borrowing BCH's.
                trigger_phrases = [p for p in (config.get("trigger_phrases") or []) if p]
                _TRIGGER_PHRASE_RE = (
                    re.compile("|".join(re.escape(p) for p in trigger_phrases), re.I)
                    if trigger_phrases else None
                )

                for m in new_msgs:
                    msg_had_failure = False
                    if _TRIGGER_PHRASE_RE and not _TRIGGER_PHRASE_RE.search(m["text"] or ""):
                        _log.info("imessage poll skip rowid=%d: no configured trigger phrase", m["rowid"])
                        log.append("skip: no configured trigger phrase match — not a request")
                        continue
                    assignments = _extract_assignments(m["text"])
                    _log.info("imessage poll rowid=%d extracted=%d assignment(s)",
                              m["rowid"], len(assignments))
                    msg_lower = m["text"].lower()

                    for a in assignments:
                        _log.info("imessage poll eval rowid=%d assignment=%r", m["rowid"], a)

                        assignment_date = _resolve_raw_date(a.get("raw_date", ""))
                        if assignment_date is None and len(assignments) == 1:
                            # model copied a non-date span (e.g. "TODAY" instead
                            # of the adjacent "07/16") — fall back to scanning
                            # the raw message for a date pattern. Only safe when
                            # there's a single assignment: with multiple, a bad
                            # span on one could silently borrow another's date.
                            assignment_date = _resolve_raw_date(m["text"])
                        if assignment_date is None:
                            # no usable date — don't guess: no accept, no
                            # decline, no reply.
                            _log.info("imessage poll skip: no usable date in extracted assignment")
                            log.append(f"skip: no usable date — ambiguous, no reply sent ({a})")
                            continue
                        start_hhmm, end_hhmm = _resolve_raw_time_range(a.get("raw_time", ""))
                        if not start_hhmm:
                            start_hhmm, end_hhmm = _resolve_raw_time_range(m["text"])

                        detail_parts = [assignment_date.strftime("%-m/%d")]
                        if start_hhmm:
                            trange = _fmt_time(start_hhmm)
                            if end_hhmm:
                                trange += f"-{_fmt_time(end_hhmm)}"
                            detail_parts.append(trange)
                        if a.get("location"):
                            detail_parts.append(a["location"])
                        assignment_detail = " ".join(detail_parts)

                        weekday_name = assignment_date.strftime("%A").lower()
                        # ponytail: uses the same eval_rule('weekday_in') primitive
                        # the generic branching workflow engine's condition DSL
                        # uses (jobs/gate_rules.py) — a clean 1:1 dedup since this
                        # is a pure boolean predicate over a scalar with no extra
                        # diagnostic data to preserve, unlike gates 2/3/5 below
                        # (kept hand-rolled: gates 2/3 need to report WHICH keyword
                        # /location matched, and gate 5's decline text needs the
                        # actual conflicting event description from
                        # check_calendar_conflict — forcing either through
                        # eval_rule's bool-only return would lose that or double
                        # the external calendar call for no benefit).
                        from jobs.gate_rules import eval_rule
                        if eval_rule({"field": "assignment.when", "op": "weekday_in",
                                      "value": list(blackout_days)},
                                     {"assignment": {"when": assignment_date.isoformat()}}):
                            _log.info("imessage poll skip blackout weekday=%s date=%s",
                                      weekday_name, assignment_date.date())
                            log.append(f"skip: assignment falls on {weekday_name} ({assignment_date:%m/%d})")
                            if not _decline(f"blackout day {weekday_name} ({assignment_date:%m/%d})", assignment_detail):
                                msg_had_failure = True
                            continue

                        # skip_keywords matched against the raw message, not
                        # just the extracted fields — "today"/"tomorrow" etc.
                        # are urgency/ambiguity signals from the sender's own
                        # wording, not something the structured fields carry.
                        seg_lower = f"{a.get('location', '')} {msg_lower}".lower()
                        hit = next((k for k in skip_keywords if k in seg_lower), None)
                        if hit:
                            _log.info("imessage poll skip keyword=%r", hit)
                            log.append(f"skip: contains {hit!r}")
                            if not _decline(f"keyword {hit!r}", assignment_detail):
                                msg_had_failure = True
                            continue

                        hit = next((loc for loc in blacklist_locations if loc in seg_lower), None)
                        if hit:
                            _log.info("imessage poll skip location=%r", hit)
                            log.append(f"skip: blacklisted location {hit!r}")
                            if not _decline(f"location {hit!r}", assignment_detail):
                                msg_had_failure = True
                            continue

                        parsed_dt = None
                        if start_hhmm:
                            hour, minute = (int(p) for p in start_hhmm.split(":", 1))
                            if eval_rule({"field": "assignment.hour", "op": "lt", "value": earliest_hour},
                                         {"assignment": {"hour": hour}}):
                                _log.info("imessage poll skip early hour=%d", hour)
                                log.append(f"skip: starts {start_hhmm}, before {earliest_hour}:00")
                                if not _decline(f"starts {start_hhmm}", assignment_detail):
                                    msg_had_failure = True
                                continue
                            parsed_dt = assignment_date.replace(hour=hour, minute=minute, second=0, microsecond=0)

                        parsed_end_dt = None
                        if parsed_dt is not None and end_hhmm:
                            eh, em = (int(p) for p in end_hhmm.split(":", 1))
                            parsed_end_dt = parsed_dt.replace(hour=eh, minute=em, second=0, microsecond=0)
                            if parsed_end_dt <= parsed_dt:
                                parsed_end_dt += timedelta(days=1)

                        if check_calendar and parsed_dt is not None:
                            from tools.gcalendar import check_calendar_conflict
                            conflict = check_calendar_conflict(
                                parsed_dt, buffer_minutes=calendar_buffer, end=parsed_end_dt,
                            )
                            if conflict:
                                # fail-safe: an unverifiable check ("[calendar
                                # error] ...") is treated the same as a real
                                # conflict — don't auto-accept when we can't
                                # actually confirm the calendar is clear.
                                _log.info("imessage poll skip calendar=%s", conflict)
                                log.append(f"skip: calendar — {conflict}")
                                if not _decline("calendar conflict", assignment_detail):
                                    msg_had_failure = True
                                continue

                        reply_text = f"{template} ({assignment_detail})"

                        ok = _queue_send(watch_contact, reply_text, "accept")
                        if not ok:
                            msg_had_failure = True
                        else:
                            accepted += 1
                            log.append(f"accepted, queued reply {reply_text!r}")

                            # Calendar event creation is NOT delayed — only the
                            # human-facing reply gets the anti-robotic delay;
                            # the event itself should exist immediately so a
                            # conflict check against it (gate 5, above) sees it
                            # right away for any assignment offered right after.
                            from tools.gcalendar import create_calendar_event
                            event_start = (parsed_dt or assignment_date).astimezone()
                            event_end_iso = ""
                            if end_hhmm:
                                eh, em = (int(p) for p in end_hhmm.split(":", 1))
                                event_end = event_start.replace(hour=eh, minute=em, second=0, microsecond=0)
                                if event_end <= event_start:
                                    # overnight shift (e.g. 9pm-1am) — end time
                                    # lands on the next calendar day.
                                    event_end += timedelta(days=1)
                                event_end_iso = event_end.isoformat()
                            # extraction schema requires a location value, so the
                            # model fills unstated ones with a literal "N/A" —
                            # don't let that leak into the event title/location.
                            loc = (a.get("location") or "").strip()
                            if loc.lower() in ("", "n/a", "none", "unknown"):
                                loc = ""
                            event_title = "BCH Assignment" + (f" - {loc}" if loc else "")
                            cal_result = create_calendar_event(
                                title=event_title, start_iso=event_start.isoformat(),
                                end_iso=event_end_iso, location=loc,
                            )
                            if is_error_result(cal_result):
                                _log.warning("imessage poll: calendar event creation failed: %s", cal_result)
                                log.append(f"accepted but calendar event failed: {cal_result}")
                            else:
                                log.append(cal_result)

                    if msg_had_failure:
                        send_failed_rowid = m["rowid"]
                        _log.warning("imessage poll: send failure on rowid=%d — not retrying", m["rowid"])

                new_seen_rowid = new_msgs[-1]["rowid"]
                _log.info("imessage poll done accepted=%d total=%d seen_rowid=%d -> %d",
                          accepted, len(new_msgs), since_rowid, new_seen_rowid)
                workflow_store.update_workflow_instance(
                    instance["id"], config={**config, "seen_rowid": new_seen_rowid},
                )
                _detail = f"{accepted} accepted, {len(log) - accepted} skipped"
                if send_failed_rowid:
                    _detail += f" — send failure on rowid={send_failed_rowid}, not retried"
                return {
                    "success": True,
                    "detail": _detail,
                    "new_items": len(new_msgs),
                    "log": log[:20],
                }

            recipient = config.get("recipient") or os.environ.get("IMESSAGE_DEFAULT_TARGET", "")
            message = config.get("message") or f"Automation '{task_name}' triggered."
            if not recipient:
                return {"success": False, "error": "no recipient configured for imessage action"}
            result = send_imessage(to=recipient, message=message)
            return {"success": "error" not in result.lower(), "detail": result}

        if action_type == "http_webhook":
            url = config.get("url", "")
            if not url:
                return {"success": False, "error": "no url configured for http_webhook action"}
            import requests
            payload = config.get("payload") or {"automation": task_name}
            resp = requests.post(url, json=payload, timeout=10)
            return {"success": resp.ok, "status_code": resp.status_code}

        if action_type == "email":
            from tools.registry import EXECUTORS
            recipient = config.get("recipient", "")
            if not recipient:
                return {"success": False, "error": "no recipient configured for email action"}
            subject = config.get("subject") or task_name

            query = config.get("query", "")
            topics = config.get("topics", "")

            if topics and not query:
                import os as _os

                days = int(config.get("days", 2))
                max_results = int(config.get("max_results", 8))
                raw_items: list[dict] = []
                search_source = None

                if _os.environ.get("EXA_API_KEY"):
                    try:
                        from exa_py import Exa
                        client = Exa(api_key=_os.environ["EXA_API_KEY"])
                        resp = client.search_and_contents(
                            topics, num_results=max_results, text=True,
                            type="auto", category="news",
                        )
                        raw_items = [
                            {
                                "title": r.title or "",
                                "url": r.url or "",
                                "content": (r.text or "")[:1000],
                                "published_date": (getattr(r, "published_date", "") or "")[:10],
                            }
                            for r in resp.results
                        ]
                        if raw_items:
                            search_source = "exa"
                    except Exception as exc:
                        _log.warning("user.automation: exa news search failed (%s), trying tavily", exc)

                if not raw_items and _os.environ.get("TAVILY_API_KEY"):
                    try:
                        from tavily import TavilyClient
                        client = TavilyClient(api_key=_os.environ["TAVILY_API_KEY"])
                        resp = client.search(
                            topics, topic="news", days=days,
                            max_results=max_results, include_raw_content=False,
                        )
                        raw_items = resp.get("results", []) if isinstance(resp, dict) else []
                        if raw_items:
                            search_source = "tavily"
                    except Exception as exc:
                        _log.warning("user.automation: tavily news search failed (%s)", exc)

                if not raw_items:
                    return {"success": False, "error": "no news search backend available (checked tavily, exa)"}

                # Dedupe only against the previous send (daily digest may re-show still-relevant stories).
                from jobs.dedup import dedup_by_url, persist_seen
                new_items, seen_urls = dedup_by_url(
                    raw_items, seen=config.get("last_sent_urls", []),
                    url_of=lambda it: it.get("url", ""),
                )
                if not new_items:
                    persist_seen(instance, last_sent_urls=seen_urls)
                    return {"success": True, "detail": "no new stories", "checked": True, "new_items": 0}

                import html as _html
                from urllib.parse import urlparse as _urlparse

                date_str = datetime.now().strftime("%A, %B %d, %Y")
                # Synthesize a curated, substance-first briefing (not a headline dump).
                source_pack = "\n\n---\n\n".join(
                    f"TITLE: {(it.get('title') or '(untitled)').strip()}\n"
                    f"SOURCE: {_urlparse(it.get('url') or '').netloc.replace('www.', '') or 'source'} "
                    f"({ (it.get('published_date') or '').strip() })\n"
                    f"URL: {it.get('url') or ''}\n"
                    f"CONTENT: {' '.join((it.get('content') or '').split())[:900]}"
                    for it in new_items[:max_results]
                )

                synthesized = _synthesize_news_email(
                    task_name=task_name, date_str=date_str, topics=topics,
                    source_pack=source_pack, recipient=recipient, subject=subject,
                )

                if synthesized is None:
                    # Fallback: original title + snippet digest so the run still emails something.
                    lines = [task_name, date_str, "", f"Top AI & robotics stories from the last {days} days:", ""]
                    cards = []
                    for it in new_items[:max_results]:
                        title = (it.get("title") or "(untitled)").strip()
                        url = (it.get("url") or "").strip()
                        pub = (it.get("published_date") or "").strip()
                        outlet = _urlparse(url).netloc.replace("www.", "") or "source"
                        snippet = " ".join((it.get("content") or "").split())[:160]
                        lines.append(f"• {title}")
                        lines.append(f"  {pub}  {url}")
                        if snippet:
                            lines.append(f"  {snippet}")
                        lines.append("")
                        cards.append(
                            f'<tr><td style="padding:18px 32px;border-bottom:1px solid #eef2f7;">'
                            f'<a href="{_html.escape(url)}" style="font-size:15px;font-weight:600;'
                            f'color:#1d4ed8;text-decoration:none;line-height:1.35;">{_html.escape(title)}</a>'
                            f'<div style="font-size:12px;color:#94a3b8;margin-top:5px;">{_html.escape(outlet)}'
                            f' &middot; {_html.escape(pub)}</div>'
                            f'<div style="font-size:13px;color:#475569;margin-top:7px;line-height:1.5;">'
                            f'{_html.escape(snippet)}</div></td></tr>'
                        )
                    lines += ["---", "Auto-generated by clixen. Manage this briefing in your automations."]
                    body = "\n".join(lines)
                    html_body = (
                        '<!DOCTYPE html><html><head><meta charset="utf-8">'
                        '<meta name="viewport" content="width=device-width,initial-scale=1"></head>'
                        '<body style="margin:0;padding:0;background:#f4f5f7;'
                        "font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',Roboto,Helvetica,Arial,sans-serif;"
                        'color:#1a1a1a;">'
                        '<table width="100%" cellpadding="0" cellspacing="0" style="background:#f4f5f7;padding:24px 0;">'
                        '<tr><td align="center"><table width="600" cellpadding="0" cellspacing="0" '
                        'style="max-width:600px;width:100%;background:#ffffff;border-radius:12px;overflow:hidden;'
                        'box-shadow:0 1px 3px rgba(0,0,0,0.08);">'
                        f'<tr><td style="background:linear-gradient(135deg,#0f172a,#1e3a8a);padding:28px 32px;">'
                        f'<div style="font-size:20px;font-weight:700;color:#ffffff;letter-spacing:-0.01em;">'
                        f'{_html.escape(task_name)}</div>'
                        f'<div style="font-size:13px;color:#cbd5e1;margin-top:4px;">{_html.escape(date_str)}'
                        f' &middot; Top stories from the last {days} days</div></td></tr>'
                        f'<tr><td style="padding:20px 32px 4px;font-size:14px;color:#475569;">'
                        f'Your daily AI &amp; robotics briefing &mdash; pulled from today\'s news outlets.</td></tr>'
                        + "".join(cards)
                        + f'<tr><td style="padding:20px 32px;background:#f8fafc;border-top:1px solid #e2e8f0;'
                        f'font-size:12px;color:#94a3b8;">Auto-generated by clixen. '
                        f'Manage this briefing in your automations.</td></tr>'
                        '</table></td></tr></table></body></html>'
                    )
                    res = EXECUTORS["send_email"]({"to": recipient, "subject": subject, "body": body, "html_body": html_body})
                else:
                    res = EXECUTORS["send_email"](synthesized)
                if "error" in str(res).lower():
                    return {"success": False, "detail": str(res)}
                persist_seen(instance, last_sent_urls=seen_urls)
                return {"success": True, "detail": f"{len(new_items)} new stories sent", "new_items": len(new_items)}

            if query:
                from hashlib import sha256 as _sha256
                import re as _re
                from store import workflow_store
                from tools.pubmed import execute as pubmed_search
                days = config.get("days")
                result = pubmed_search(
                    query, max_results=config.get("max_results", 10),
                    reldate=int(days) if days else None,
                    sort="pub_date" if days else "relevance",
                )
                if not result.ok:
                    msg = f"[pubmed] {result.error or 'no results'}"
                    return {"success": True, "detail": msg, "checked": True, "new_items": 0}

                def _pmid_of(it):
                    m = _re.search(r"/(\d+)/?$", it.url.strip("/"))
                    return m.group(1) if m else ""

                # dedup: content hash (cross-workflow) + PMID (legacy)
                from jobs.dedup import dedup_by_hash, persist_seen
                kept, seen_hashes, seen_pmids = dedup_by_hash(
                    result.items,
                    seen_hashes=config.get("seen_hashes", []),
                    stable_ids=config.get("seen_pmids", []),
                    hash_of=lambda it: _sha256(f"{it.title}|{it.published_date}".encode()).hexdigest(),
                    id_of=_pmid_of,
                )
                # pmid derived per-item (not sliced off the mixed old+new seen
                # list, which had no positional correspondence to `kept`)
                deduped = [(it, _pmid_of(it)) for it in kept]

                # keyword relevance gate — no LLM dependency.
                # ponytail: title must mention an aging term; catches papers
                # that match a broad PubMed keyword (quercetin, osteoblast)
                # but aren't substantively about aging.
                _AGING_TERMS = frozenset({
                    "aging", "ageing", "senescence", "senolytic", "longevity",
                    "geroprotector", "epigenetic clock", "age-related", "healthspan",
                    "lifespan", "rejuvenation", "anti-aging", "biological age",
                })
                deduped = [(it, p) for it, p in deduped
                           if any(t in it.title.lower() for t in _AGING_TERMS)]

                if not deduped:
                    # persist seen sets even on zero send — prevents
                    # re-evaluating the same non-relevant papers next cycle.
                    persist_seen(instance, seen_hashes=seen_hashes, seen_pmids=seen_pmids)
                    return {"success": True, "detail": "no relevant papers", "checked": True, "new_items": 0}

                parts = []
                for item, pmid in deduped:
                    date = f" ({item.published_date})" if item.published_date else ""
                    url = item.url or (f"https://pubmed.ncbi.nlm.nih.gov/{pmid}/" if pmid else "")
                    snippet = (item.snippet or "(no abstract)")
                    parts.append(f"- {item.title}{date} ({url})\n  {snippet}")
                body = "\n\n".join(parts)
                intro = config.get("message") or ""
                if intro:
                    body = intro + "\n\n" + body

                EXECUTORS["send_email"]({"to": recipient, "subject": subject, "body": body})

                persist_seen(instance, seen_hashes=seen_hashes, seen_pmids=seen_pmids)
                return {"success": True, "detail": f"{len(deduped)} new paper(s) sent", "new_items": len(deduped)}
            else:
                body = config.get("message") or config.get("body") or f"Automation '{task_name}' triggered."
                result = EXECUTORS["send_email"]({"to": recipient, "subject": subject, "body": body})
                return {"success": "error" not in str(result).lower(), "detail": str(result)}

        if action_type == "tool_call":
            from tools.registry import TOOL_TAGS, execute_tool, is_error_result

            tool_name = config.get("tool_name", "")
            if "browser" not in TOOL_TAGS.get(tool_name, frozenset()):
                return {
                    "success": False,
                    "error": f"tool_name {tool_name!r} is not an allowed browser-tagged tool for tool_call automations",
                }

            # ponytail: bounds how long this call can block the worker. Not
            # signal.alarm() — core.py runs task_worker as a background
            # thread (alongside chat_ui/telegram_bot/email_watch), and
            # signal.alarm only works in the main thread; jobs/worker.py's
            # own _execute_job uses signal.alarm too, so it likely has this
            # same latent bug (unconfirmed — not exercised live here).
            # Underlying BrowserOS subprocess calls already have their own
            # timeout=60 (tools/connector_doordash.py:_run, tools/_browseros.py),
            # so this just stops the worker from waiting past 90s if that
            # somehow doesn't bound it. _poll_workflow_store is still
            # single-threaded, so one long call still blocks every other due
            # automation until it returns or this timeout fires — real fix is
            # threading workflow dispatch; out of scope here.
            from concurrent.futures import ThreadPoolExecutor, TimeoutError as _FutureTimeoutError

            # ponytail: NOT `with ThreadPoolExecutor() as ex:` — __exit__'s
            # shutdown(wait=True) blocks until the submitted call finishes
            # regardless of the future.result(timeout=...) below, defeating
            # the timeout entirely (documented gotcha in this codebase).
            _ex = ThreadPoolExecutor(max_workers=1)
            future = _ex.submit(execute_tool, tool_name, config.get("tool_args") or {})
            try:
                result = future.result(timeout=90)
            except _FutureTimeoutError:
                return {"success": False, "error": f"tool_call to {tool_name!r} timed out after 90s"}
            finally:
                _ex.shutdown(wait=False)

            success = not is_error_result(result)
            notify_via = config.get("notify_via", "telegram")

            if notify_via == "email":
                recipient = config.get("recipient", "")
                if not recipient:
                    return {"success": False, "error": "no recipient configured for tool_call notify_via=email"}
                from tools.registry import EXECUTORS as _EXECUTORS
                send_res = _EXECUTORS["send_email"]({"to": recipient, "subject": task_name, "body": result})
                return {"success": success and "error" not in str(send_res).lower(), "detail": result}

            if notify_via == "notification":
                from store import workflow_store
                workflow_store.add_notification(message=result, source="automation", level="info" if success else "error")
                return {"success": success, "detail": result}

            from tools.telegram_send import send_telegram
            send_res = send_telegram(result)
            return {"success": success and "error" not in send_res.lower(), "detail": result}

        if action_type == "workflow":
            from concurrent.futures import ThreadPoolExecutor, TimeoutError as _WfTimeout
            from store import workflow_store as _wf_store
            from tools.registry import EXECUTORS as _wf_execs, execute_tool as _wf_exec_tool, is_error_result as _wf_is_err
            from tools.telegram_send import send_telegram as _wf_send_tg
            import requests as _wf_req

            # ponytail: capability ceiling for workflow tool_call steps. LLM-authored
            # automations (create_automation) had no cap here — any step could call
            # bash_exec/delete_file/git_commit/send_email unattended on a schedule.
            # Ceiling defaults to read_only; a workflow must opt into "execute" via
            # config.capability_mode to reach a tool in the existing PLAN_BLOCKED_TOOLS
            # set (already the curated list of destructive/side-effecting tools —
            # reused here instead of hand-rolling a second list).
            from tools.registry import PLAN_BLOCKED_TOOLS as _wf_destructive_tools
            _wf_capability_mode = config.get("capability_mode", "read_only")

            def _exec_step(action, params):
                if action == "tool_call":
                    tn = params.get("tool", "")
                    ta = {k: v for k, v in params.items() if k != "tool"}
                    if tn in _wf_destructive_tools and _wf_capability_mode != "execute":
                        raise RuntimeError(
                            f"[blocked] tool_call to {tn!r} requires capability_mode='execute' "
                            f"on this automation (current ceiling: {_wf_capability_mode!r})"
                        )
                    _ex = ThreadPoolExecutor(max_workers=1)
                    f = _ex.submit(_wf_exec_tool, tn, ta)
                    try:
                        sr = f.result(timeout=90)
                    except _WfTimeout:
                        raise RuntimeError("[timeout] tool_call timed out after 90s")
                    finally:
                        _ex.shutdown(wait=False)
                    # execute_tool() never raises — every failure (unknown
                    # tool, bad args, the tool's own exception) comes back as
                    # an error-prefixed STRING, not a Python exception. Left
                    # alone, that means failure_mode/goto-on-exception and a
                    # downstream condition's 'is_error' op never engage for a
                    # failing tool_call — the step just looks like a normal
                    # success with an error string as its "result" (found
                    # live 2026-07-16). Re-raising here restores normal
                    # failure_mode semantics; a condition step can also gate
                    # on this step's result directly via the 'is_error' op
                    # without needing the exception path at all.
                    if _wf_is_err(sr):
                        raise RuntimeError(sr)
                    return sr
                if action == "telegram":
                    return _wf_send_tg(params.get("message", ""))
                if action == "email":
                    return str(_wf_execs["send_email"](params))
                if action == "notification":
                    _wf_store.add_notification(
                        message=params.get("message", ""),
                        source="automation", level=params.get("level", "info"),
                    )
                    return "ok"
                if action == "http_webhook":
                    r = _wf_req.post(params["url"], json=params.get("payload", {}), timeout=10)
                    return f"http {r.status_code}"
                if action == "imessage":
                    import os as _os
                    from tools.imessage_search import send as _im_send
                    rcpt = params.get("recipient") or _os.environ.get("IMESSAGE_DEFAULT_TARGET", "")
                    if not rcpt:
                        return "[error] no recipient for imessage step"
                    return _im_send(to=rcpt, message=params.get("message", ""))
                # 'approval' is handled specially in the step loop below (it must
                # pause the whole run, not just return a string like every other
                # action) — see the `if action == "approval":` branch there.
                if action == "llm_extract":
                    return _llm_json_extract(
                        params.get("prompt", ""),
                        params.get("schema", {"type": "object"}),
                        model=params.get("model"),
                    )
                if action is None:
                    # A step used purely as a gate (a 'condition' with
                    # on_pass/on_fail and nothing to actually DO) naturally
                    # has no 'action' — found live 2026-07-16 authored
                    # unprompted by the orchestrator itself. Treat as an
                    # explicit no-op instead of falling into the generic
                    # unknown-action error string below, which was cosmetic
                    # noise (the step still branched correctly, but its own
                    # result looked like a failure).
                    return "ok (no-op gate step)"
                return f"[error] unknown step action: {action!r}"

            steps = config.get("steps", [])
            if not steps:
                return {"success": False, "error": "workflow has no steps"}

            named_steps = [(step.get("name", f"step_{i}"), step) for i, step in enumerate(steps)]
            index_by_name = {name: i for i, (name, _) in enumerate(named_steps)}

            # A prior run of this same workflow_instance paused on an 'approval'
            # step (see below) — approve_workflow() clears _workflow_pause and
            # flips status back to 'active' with next_run_at=now to get here.
            # Resume with the saved results instead of starting the step
            # sequence over, so steps before the approval don't re-execute
            # (re-sending an already-sent notification, re-running a tool_call
            # with side effects, etc).
            _pause = config.get("_workflow_pause")
            if _pause:
                # Consume it now, not in approve_workflow/reject_workflow — those
                # tools only flip status/next_run_at (see automation_tools.py).
                # This is the one and only read of _workflow_pause, so a resume
                # can't accidentally fire twice (crash/retry mid-run just
                # re-reads the already-cleared config and starts over, same as
                # any other unconfigured workflow run).
                config = {k: v for k, v in config.items() if k != "_workflow_pause"}
                _wf_store.update_workflow_instance(instance["id"], config=config)
            if _pause and _pause.get("resume_step") == "__end__":
                return {
                    "success": True,
                    "detail": f"Executed {len(_pause.get('results') or {})}/{len(steps)} steps "
                              f"(approved, no steps after the approval)",
                    "steps": _pause.get("results") or {},
                }
            if _pause and _pause.get("resume_step") in index_by_name:
                results = dict(_pause.get("results") or {})
                cur = index_by_name[_pause["resume_step"]]
            else:
                results = {}
                cur = 0
            executed = 0
            # ponytail: guards a config-authored infinite goto loop; nothing
            # loops back in v1 so 4x is generous slack, not a tuned bound.
            max_steps = len(steps) * 4
            while cur is not None and 0 <= cur < len(named_steps) and executed < max_steps:
                name, step = named_steps[cur]
                executed += 1
                action = step.get("action")
                params = step.get("params", {})
                failure_mode = step.get("failure_mode", "stop")
                condition = step.get("condition")
                rp = _interp(params, results)

                if action == "approval":
                    # Draft-and-send: pause the run here instead of executing
                    # further steps. Nothing after this step (the actual send/
                    # tool_call/webhook it's gating) runs until approve_workflow
                    # resumes this same workflow_instance at resume_step with
                    # the results gathered so far — see the _pause read above
                    # and _approve_workflow/_reject_workflow in automation_tools.py.
                    import datetime as _dt
                    msg = rp.get("message", "Approve workflow to continue")
                    expires = rp.get("expires_in_minutes", 60)
                    exp_at = (_dt.datetime.now(_dt.timezone.utc) + _dt.timedelta(minutes=expires)).isoformat()
                    app = _wf_store.create_approval_request(
                        kind="step_approval",
                        payload={"message": msg, "params": rp},
                        workflow_instance_id=instance["id"],
                        job_id=instance["id"],
                        expires_at=exp_at,
                    )
                    _wf_store.add_notification(
                        message=f"Approval needed: {msg}",
                        source="automation", level="action",
                    )
                    resume_step = step.get("on_pass") or (
                        named_steps[cur + 1][0] if cur + 1 < len(named_steps) else "__end__"
                    )
                    _wf_store.update_workflow_instance(
                        instance["id"],
                        status="awaiting_approval",
                        config={**config, "_workflow_pause": {
                            "approval_id": app["id"], "resume_step": resume_step, "results": results,
                        }},
                    )
                    return {
                        "success": True,
                        "detail": f"paused for approval {app['id'][:12]}: {msg}",
                        "approval_id": app["id"], "paused": True, "steps": results,
                    }

                if condition is not None:
                    from jobs.gate_rules import eval_condition
                    passed = eval_condition(condition, results)
                else:
                    passed = True

                if condition is not None and not passed:
                    # gate failure is a routing outcome, not an execution
                    # error — failure_mode/rollback stay reserved for actual
                    # action exceptions below.
                    results[name] = {"success": True, "result": None, "gated": True, "passed": False}
                    nxt = step.get("on_fail")
                else:
                    try:
                        sr = _exec_step(action, rp)
                        results[name] = {"success": True, "result": sr}
                        if condition is not None:
                            results[name]["passed"] = True
                        nxt = step.get("on_pass") if condition is not None else step.get("goto")
                    except Exception as exc:
                        results[name] = {"success": False, "error": str(exc)}
                        if failure_mode == "rollback":
                            raise
                        if failure_mode == "stop":
                            break
                        nxt = step.get("goto")

                if nxt is None:
                    cur += 1  # backward compat: no jump field set -> next step, unchanged
                elif nxt == "__end__":
                    break
                else:
                    cur = index_by_name.get(nxt)
                    if cur is None:
                        results[name]["error"] = (results[name].get("error", "") +
                                                   f" [bad goto target: {nxt!r}]")
                        break

            all_ok = all(r["success"] for r in results.values())
            return {
                "success": all_ok,
                "detail": f"Executed {len(results)}/{len(steps)} steps: " + ", ".join(
                    f"{k}: {'ok' if v['success'] else 'fail'}" for k, v in results.items()
                ),
                "steps": results,
            }

        return {"success": False, "error": f"unknown action_type: {action_type!r}"}
    except Exception as exc:
        _log.exception("user.automation: failed: %s", exc)
        return {"success": False, "error": str(exc)}


if __name__ == "__main__":
    # smoke test — verify workflow dispatch + cross-step interpolation actually substitutes
    _test_config = {
        "steps": [
            {"name": "notify", "action": "notification", "params": {"message": "workflow started"}},
            {"name": "report", "action": "notification", "params": {"message": "prev step said: $notify.result"}},
        ],
    }
    _test_instance = {"action_type": "workflow", "config": _test_config, "task_name": "test_wf"}
    _result = handle(_test_instance)
    assert _result["success"] is not None, "expected result dict"
    assert len(_result.get("steps", {})) == 2, f"expected 2 steps, got {_result.get('steps', {})}"

    # Prove _interp actually substitutes (this exact case was silently broken:
    # {{ step.notify.result }} never substituted, {step.notify.result} raised
    # AttributeError that got masked by a bare except).
    _rendered = _interp("prev step said: $notify.result", {"notify": {"success": True, "result": "ok"}})
    assert _rendered == "prev step said: ok", f"templating did not substitute: {_rendered!r}"

    # Prove condition/goto branching actually jumps and skips the other branch.
    _branch_config = {
        "steps": [
            {"name": "gate", "action": "notification", "params": {"message": "gating"},
             "condition": {"all": [{"field": "gate.result", "op": "eq", "value": "unreachable"}]},
             "on_pass": "accept", "on_fail": "decline"},
            {"name": "accept", "action": "notification", "params": {"message": "accepted"}, "goto": "__end__"},
            {"name": "decline", "action": "notification", "params": {"message": "declined"}},
        ],
    }
    _branch_result = handle({"action_type": "workflow", "config": _branch_config, "task_name": "test_branch"})
    assert "decline" in _branch_result["steps"], f"expected decline branch to run, got {_branch_result['steps']}"
    assert "accept" not in _branch_result["steps"], f"expected accept branch skipped, got {_branch_result['steps']}"

    print("workflow smoke test assertions ok (including cross-step templating and branching)")

    # Prove the capability ceiling actually blocks a destructive tool_call by
    # default, and that setting capability_mode="execute" lifts it.
    _blocked_config = {
        "steps": [{"name": "wipe", "action": "tool_call", "params": {"tool": "delete_file", "path": "/tmp/x"}}],
    }
    _blocked_result = handle({"action_type": "workflow", "config": _blocked_config, "task_name": "test_ceiling"})
    assert _blocked_result["success"] is False, f"expected default read_only ceiling to block, got {_blocked_result}"
    assert "capability_mode" in _blocked_result["steps"]["wipe"]["error"], _blocked_result

    _allowed_config = dict(_blocked_config, capability_mode="execute")
    _allowed_result = handle({"action_type": "workflow", "config": _allowed_config, "task_name": "test_ceiling_ok"})
    # Ceiling lifted → step runs (delete_file itself just reports "not found", no ceiling error).
    assert _allowed_result["steps"]["wipe"]["success"] is True, _allowed_result
    assert "capability_mode" not in str(_allowed_result["steps"]["wipe"]), _allowed_result

    print("capability ceiling assertions ok")
