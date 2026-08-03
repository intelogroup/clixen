# Agent Email / Autopilot Mail (2026-07)

Per-agent email infrastructure via `@autopilot-mail/server` (open-source AgentMail-compatible daemon). Future infrastructure — currently zero integration with clixen automations.

## Architecture

```
[agentmail_client.py] → HTTP :3100 → [autopilot daemon] → SQLite (autopilot.db)
                                  → [Resend SMTP] → Internet
```

| Layer | Tech | Detail |
|-------|------|--------|
| REST API | `@autopilot-mail/server` | Node.js, port 3100, AgentMail-compatible endpoints |
| Storage | SQLite | `tools-harness/data/autopilot/autopilot.db` |
| Outbound | Resend SMTP | `bch-agent@resend.dev`, port 465 (SSL), key in plist |
| Client | `tools-harness/tools/agentmail_client.py` | stdlib `urllib`, no deps, error-prefix `[agentmail error]` |
| Auth | Bearer token | `API_KEYS` env var, default `clixen-test-key` |

## Launchd

Daemon: `com.clixen.autopilot-mail` — KeepAlive=true, auto-restart on crash.

```
launchctl load   launchd/com.clixen.autopilot-mail.plist
launchctl unload launchd/com.clixen.autopilot-mail.plist
```

Logs: `tools-harness/data/autopilot/autopilot_{stdout,stderr}.log`

## Python client

- `create_inbox(username)` → new inbox with `{inbox_id, email, created_at}`
- `send_message(inbox_id, to, subject, text, html?)` → send outbound
- `reply_to_message(inbox_id, message_id, text, html?)` → thread-aware reply
- `list_threads(inbox_id)` / `get_thread(inbox_id, thread_id)`
- `get_message(inbox_id, message_id)` / `list_inboxes()`
- `health()` → `"healthy"` or `"unhealthy: ..."`

All functions return error strings prefixed `[agentmail error]` on failure (caller checks via `isinstance(result, str)`).

## Review (2026-07-13)

### Integration score: zero

No clixen code imports `agentmail_client.py`. No automation handler, task worker, orchestrator tool, or catalog entry consumes it. The daemon runs idle.

### What it solves

| Problem | Solved? |
|---------|---------|
| BCH iMessage cursor fix | No (SQL fix in `imessage_search.py`) |
| Gmail polling latency | No (existing automations still use Gmail API) |
| Per-agent email identity | Yes — `bch-agent@resend.dev` instead of `jimkalinov@gmail.com` |
| Native threading API | Yes — In-Reply-To / References managed server-side |
| No-OAuth outbound | Yes — Resend key replaces Google OAuth token rotation |

### What it costs

- +1 Node daemon (idle, near-zero CPU)
- Port :3100 taken
- `@autopilot-mail/server` + `better-sqlite3` (global npm)
- Resend API key in plist (external credential to rotate)
- 128 lines client + 42 lines plist to maintain

### Verdict

Infrastructure without a consumer. Keep running (cost near-zero). Wire in when clixen needs agents that send as themselves, not as the user. Current automations read Gmail as you and send as you — no inbox abstraction needed.

### To wire in (future)

1. Create agent inbox per automation that needs email identity
2. Forward relevant email from Gmail to agent inbox (or have correspondents email agent directly)
3. Write handlers consuming agentmail webhooks (not polling Gmail)

## Key files

- `tools-harness/tools/agentmail_client.py` — Python API wrapper
- `launchd/com.clixen.autopilot-mail.plist` — launchd config
- `tools-harness/data/autopilot/autopilot.db` — message store
- `tools-harness/data/autopilot/autopilot_stdout.log` — daemon logs
