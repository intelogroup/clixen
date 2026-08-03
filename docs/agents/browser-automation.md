# Browser Automation

**BrowserOS is fully removed** (was the primary approach through mid-2026; replaced by `agent-browser`). If you see another doc/comment referencing `browseros-cli`, `_browseros.py`, hidden-tab `--hidden` mode, or `__reactProps` clicking — that's stale, this file is current.

## Current approach: agent-browser CDP (`tools/_agent_browser.py`)

```
navigate → snapshot → click/fill → get_text / eval_json
```

Shared helper module shells out to the `agent-browser` CLI with `--auto-connect`, attaching to the **user's real, already-logged-in Chrome** via CDP — no separate browser app, no vault-driven login flow, no hidden-tab React-props workarounds (those were BrowserOS-only problems).

- `_run(args, timeout_s=30)` — runs `agent-browser --auto-connect <args>`, returns stdout or a `[agent-browser] ...`-prefixed error string, never raises.
- `navigate(url)`, `eval_js(js)`, `eval_json(js)`, `get_text(selector="body")`, `snapshot()`, `click(ref)`, `fill(ref, text)`, `press(key)`, `wait(*args, timeout_s=15)`.
- No prompt-injection content-boundary stripping needed (unlike browseros-cli's wrapper) — agent-browser doesn't add one.
- Requires `agent-browser` on PATH (`npm i -g agent-browser`); missing binary surfaces as a normal tool-error string, not a crash.

**Auth model**: since it attaches to the user's live Chrome session, connectors don't drive login flows anymore. If the user isn't logged into the target site in that Chrome, the connector returns something like `"[uber] log in required"` and the user logs in there once, manually.

## Service Connectors

### DoorDash (`tools/connector_doordash.py`)
`get_my_doordash_orders()`, `get_my_doordash_cart()`, `get_doordash_order_status(order_id)`. Reads the in-memory Apollo GraphQL cache via `eval_json` rather than clicking through the UI — the cart drawer doesn't reliably render on a scripted click, but the cache holds the data regardless.

### Uber (`tools/connector_uber.py`)
`estimate_uber_ride(destination, pickup="")`, `get_my_uber_trips()`. Uses `navigate`/`snapshot`/`click`/`fill` against `riders.uber.com` / `m.uber.com`, plus regex-based ref/text parsing (`_find_ref`, `_parse_trips_from_text`) — no Apollo cache here, Uber's rider UI isn't Apollo-backed the way DoorDash's is.

### Sofascore (`tools/connector_sofascore.py`) — not browser-driven at all
7 tools, no browser involved: `sofascore_search_teams`, `sofascore_get_team`, `sofascore_scheduled_events`, `sofascore_live_scores`, `sofascore_get_event`, `sofascore_get_standings`, `sofascore_get_tournaments`.
**Primary path**: RapidAPI "SportAPI" (`sportapi7.p.rapidapi.com`), ~192ms, reliable.
**Fallback**: direct `api.sofascore.com/api/v1/` via `curl_cffi` (Chrome TLS impersonation) — plain `httpx`/`requests` gets WAF-blocked (403).
Sport slugs: football, basketball, tennis, american-football, baseball, ice-hockey, mma, esports, rugby, cricket, volleyball, handball.

### Ringback (real SIP call, not a browser tool)
`tools/connector_ringback.py`'s `call_my_phone()` places a real SIP call, gated by a 900s cooldown (`RINGBACK_CALL_COOLDOWN_SECONDS` env, default 900) tracked via `_check_and_update_cooldown()`/`_restore_cooldown()`. On cooldown, returns `"[ringback cooldown] ... callable again in Ns"` — the tool description tells the model to quote the remaining-seconds figure verbatim, not paraphrase it.

## Credential Vault (`tools/vault.py`) — unaffected by the BrowserOS removal
macOS Keychain-backed credential storage, still live: `vault_save(service, data)`, `vault_get(service)`, `vault_list()`, `vault_delete(service)`.

## Session Management (`tools/session_browser.py`) — unaffected
`session_login(service, timeout_s=120)` — interactive login (visible browser, user handles CAPTCHA/2FA); session/cookie persistence helpers for reusing a logged-in browser profile.

## API Data Extraction (`tools/api_client.py`) — unaffected
`api_fetch(domain, endpoint, params)` — httpx call with auto token refresh. Configs at `~/.config/g4l/api_configs/<domain>.json`, tokens at `~/.config/g4l/tokens/<domain>.enc` (Fernet-encrypted).

## Scrapling — adaptive web scraping (optional dependency)
`tools/scrapling_fetch.py` — `scrapling_fetch(url)`, `scrapling_stealthy_fetch(url, headless=True)`, `scrapling_extract(...)`, `scrapling_fetch_and_extract(...)`. Zero regression if `scrapling` isn't installed (`_HAS_SCRAPLING` flag). Also used internally by `connector_uber.py` for ride-price HTML parsing, with a regex fallback if Scrapling returns nothing.
Gotchas: `Fetcher.get(url)` not `.fetch()`; `Selector(content=html, adaptive=True, url=...)` uses `content=` not `text=`; result property is `.html_content`.

## Local Vision (Tesseract OCR + OpenCV, no ML model) — `tools/local_vision.py`
Replaces the old `qwen3-vl` vision-model path for most screenshot analysis. No ML model: Tesseract OCR + OpenCV contour detection finds text blocks and UI elements (buttons/inputs/cards) by pixel/contour analysis. Faster and deterministic vs. a vision model (~1s vs 15-30s+).
**When**: page layout understanding, text-position lookup, coordinate-based clicking.

## Historical note — BrowserOS era (fully removed, kept for institutional memory only)

Through mid-2026 the stack ran on BrowserOS (`browseros-cli`, hidden-tab mode via `open --hidden`, and a documented "React Props Rule" — hidden tabs silently ignore synthetic DOM events, so React-bound handlers had to be triggered via `element.__reactProps[<event>]()` directly, discovered while automating Uber's auth flow). That whole layer (`tools/_browseros.py`, BrowserOS-driven login/2FA flows, the hidden-tab typing/option-selection JS sequences) is deleted. None of it applies to the current `agent-browser` approach — it's `--auto-connect` to a real, already-logged-in Chrome, so there's no hidden-tab mode and no React-props workaround needed. Don't resurrect this pattern; if a future connector needs headless/hidden operation, that's new design work, not a revival of the old code.
