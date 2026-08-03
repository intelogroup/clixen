# Local Auth + SQLite Chat Persistence

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add single-user password auth (cookie-based) and SQLite persistence for chat sessions and messages, replacing localStorage and in-memory conversation store.

**Architecture:** New `store/chat_db.py` owns the SQLite schema. `chat_ui.py` gains an auth middleware + login page, new session/message API endpoints, and JS that calls the API instead of localStorage. The existing in-memory `conversation.py` stays for the LLM sliding window; `chat_db.py` is a write-through layer for persistence.

**Tech Stack:** Python `sqlite3` (stdlib), `hmac`/`secrets` (stdlib — no new deps), FastAPI middleware, vanilla JS fetch calls.

---

## File Map

| File | Change |
|------|--------|
| `tools-harness/store/chat_db.py` | **Create** — SQLite sessions + messages CRUD |
| `tools-harness/chat_ui.py` | **Modify** — auth middleware, login page, session/message API endpoints, JS rewrite |
| `tools-harness/.env` | **Modify** — add `UI_PASSWORD` |

---

### Task 1: `store/chat_db.py` — SQLite schema + CRUD

**Files:**
- Create: `tools-harness/store/chat_db.py`

Schema:
```sql
CREATE TABLE IF NOT EXISTS sessions (
    id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS messages (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id TEXT NOT NULL REFERENCES sessions(id) ON DELETE CASCADE,
    role TEXT NOT NULL,
    content TEXT NOT NULL,
    model TEXT,
    ts TEXT NOT NULL
);
```

Public API:
```python
DB_PATH = Path(__file__).parent / "chat.db"

def init() -> None: ...             # create tables, enable WAL + foreign keys
def list_sessions() -> list[dict]: ...
def create_session(id: str, name: str) -> dict: ...
def rename_session(id: str, name: str) -> None: ...
def delete_session(id: str) -> None: ...
def append_message(session_id: str, role: str, content: str, model: str | None = None) -> None: ...
def get_messages(session_id: str) -> list[dict]: ...
```

- [ ] Write `chat_db.py` with full implementation (use `sqlite3.Row` + `row_factory`)
- [ ] Call `init()` in a quick manual test: `python -c "from store.chat_db import init; init(); print('ok')"`
- [ ] Commit: `git add tools-harness/store/chat_db.py && git commit -m "feat: add chat_db SQLite store"`

---

### Task 2: Auth middleware + login page in `chat_ui.py`

**Files:**
- Modify: `tools-harness/chat_ui.py`
- Modify: `tools-harness/.env`

Auth design:
- At startup, generate `_SESSION_TOKEN = secrets.token_hex(32)` (in-memory, resets on restart — fine for local)
- `UI_PASSWORD` read from `.env` via `os.getenv("UI_PASSWORD", "changeme")`
- FastAPI middleware: every request except `/login` checks cookie `g4l_auth`; if missing/wrong → redirect to `/login`
- `GET /login` → serve login HTML (minimal, same dark theme)
- `POST /login` (form) → check password with `hmac.compare_digest` → set `g4l_auth` cookie → redirect `/`
- `GET /logout` → delete cookie → redirect `/login`

- [ ] Add `UI_PASSWORD=localpass` to `tools-harness/.env`
- [ ] Add `_SESSION_TOKEN` generation near top of `chat_ui.py`
- [ ] Add `BaseHTTPMiddleware` subclass that checks the cookie
- [ ] Add `/login` GET + POST routes and minimal login HTML inline string
- [ ] Add `/logout` GET route
- [ ] Restart server, verify redirect to `/login`, verify login works
- [ ] Commit: `git commit -m "feat: single-user cookie auth"`

---

### Task 3: Session + message API endpoints

**Files:**
- Modify: `tools-harness/chat_ui.py`

New endpoints (all protected by middleware):
```
GET  /sessions              → list_sessions()
POST /sessions              body: {id, name} → create_session()
PUT  /sessions/{id}/name    body: {name}     → rename_session()
DELETE /sessions/{id}                        → delete_session()
GET  /messages/{session_id}                  → get_messages() (replaces /history/{chat_id})
```

Message write-through hooks:
- In `chat_stream` generator: after user message is known, call `chat_db.append_message(chat_id, "user", message)`
- After the `done` SSE event is assembled (when `result["reply"]` is set), call `chat_db.append_message(chat_id, "assistant", result["reply"], result.get("model"))`
- Same two calls in `voice_process` after transcript and after streaming completes

- [ ] Add the 5 session endpoints
- [ ] Add write-through calls in `chat_stream` and `voice_process`
- [ ] Keep `/history/{chat_id}` working (proxy to `get_messages`) for backwards compat
- [ ] Verify with `curl http://localhost:9234/sessions` (after login cookie set)
- [ ] Commit: `git commit -m "feat: session + message persistence endpoints"`

---

### Task 4: JS — replace localStorage with API calls

**Files:**
- Modify: `tools-harness/chat_ui.py` (the embedded `HTML` string, JS section only)

Replace these localStorage functions with async API wrappers:

```js
// Remove: loadSessions, saveSessions, getActiveId, setActiveId, createSession,
//         renameSession, deleteSession (localStorage versions)
// Replace with:

async function fetchSessions() {
  const r = await fetch('/sessions'); return r.json();
}
async function apiCreateSession(id, name) {
  await fetch('/sessions', {method:'POST', headers:{'Content-Type':'application/json'},
    body: JSON.stringify({id, name})});
}
async function apiRenameSession(id, name) {
  await fetch(`/sessions/${id}/name`, {method:'PUT', headers:{'Content-Type':'application/json'},
    body: JSON.stringify({name})});
}
async function apiDeleteSession(id) {
  await fetch(`/sessions/${id}`, {method:'DELETE'});
}
// activeId stays in localStorage (ephemeral UI state, not persistence)
function getActiveId()   { return localStorage.getItem('g4l_active'); }
function setActiveId(id) { localStorage.setItem('g4l_active', id); }
```

- Update `renderSidebar()` to be async, call `fetchSessions()` at the top
- Update `createSession()` → async, calls `apiCreateSession()`
- Update `renameSession()` → async, calls `apiRenameSession()`
- Update `confirmDelete()` → async, calls `apiDeleteSession()`
- Update init block at line ~2322: make `async` IIFE, `await fetchSessions()` before render
- Remove `SESSIONS_KEY` constant; keep `ACTIVE_KEY`

- [ ] Rewrite the JS session functions as above
- [ ] Update all callers to await async session functions
- [ ] Update init IIFE
- [ ] Reload browser, verify sessions list from DB, create/rename/delete work
- [ ] Reload page, verify sessions and messages survive restart (no localStorage)
- [ ] Commit: `git commit -m "feat: JS sessions via API, drop localStorage"`

---

### Task 5: Sync to lowercase repo

**Files:**
- Modify: `~/developer/gemma4llama/tools-harness/chat_ui.py` (copy)
- Create: `~/developer/gemma4llama/tools-harness/store/chat_db.py` (copy)

- [ ] `cp tools-harness/store/chat_db.py ~/developer/gemma4llama/tools-harness/store/chat_db.py`
- [ ] `cp tools-harness/chat_ui.py ~/developer/gemma4llama/tools-harness/chat_ui.py`
- [ ] Restart Telegram bot launchd service to pick up changes: `launchctl unload ~/Library/LaunchAgents/com.gemma4llama.telegrambot.plist && launchctl load ~/Library/LaunchAgents/com.gemma4llama.telegrambot.plist`
- [ ] Verify bot still responds
- [ ] Commit in lowercase repo

---

## Notes

- `chat.db` lives at `tools-harness/store/chat.db` alongside `uploads.db`
- Auth cookie is session-scoped (clears on browser close) — acceptable for local use
- `conversation.py` in-memory store is NOT replaced; `chat_db` is purely a persistence mirror
- The Telegram bot does not use `chat_ui.py` auth or sessions — no changes needed to `telegram_bot.py`
