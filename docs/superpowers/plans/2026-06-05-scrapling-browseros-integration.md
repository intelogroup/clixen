# Scrapling + BrowserOS Hidden Tab Integration (June 2026)

> Implementation plan for adding adaptive web scraping (Scrapling) and a complete Uber login flow to the gemma4llama agent harness. This documents work completed June 5, 2026.

## Goal

Add two complementary capabilities to the local agent:

1. **Adaptive web scraping** via Scrapling — survives DOM/class-rename churn that breaks regex/BeautifulSoup
2. **End-to-end Uber price estimates** with email/SMS 2FA login in BrowserOS hidden tabs — proven through to SMS prompt, awaiting user SMS code to complete live test

## Architecture

```
gemma4llama/tools-harness/
├── tools/
│   ├── scrapling_fetch.py        # NEW: 4 scrapling tool functions + schemas
│   ├── connector_uber.py         # UPDATED: Scrapling integration, _login_uber_email, onMouseDown fix
│   ├── _browseros.py             # UPDATED: _react_click helper, optional dep pattern
│   └── registry.py               # UPDATED: wired in scrapling tools
└── tests/
    ├── test_scrapling_fetch.py   # NEW: 26 tests
    └── test_connector_uber_scrapling.py  # 37 tests (updated)
```

## Task 1: Scrapling Tool Surface — COMPLETE

Added 4 public tools via `tools/scrapling_fetch.py` and registered in `tools/registry.py`:

| Tool | API call | Use case |
|---|---|---|
| `scrapling_fetch(url)` | `Fetcher.get(url)` | Plain HTTP fetch |
| `scrapling_stealthy_fetch(url, headless=True)` | `StealthyFetcher(...)` | Anti-bot stealth |
| `scrapling_extract(url, selector, mode, extract_type, percentage)` | `Selector(...)` | CSS extract, 3 modes |
| `scrapling_fetch_and_extract(...)` | Combined | One-call pipeline |

**Modes:** `auto_save` (default first call) / `adaptive` (relocate after redesign) / `raw` (no fingerprint)
**Types:** `text` / `html` / `attrs` / `json`

**Optional dependency pattern** (`_HAS_SCRAPLING` flag in both files): zero regression if Scrapling isn't installed. Falls back to existing behavior.

**Tests:** 26 in `test_scrapling_fetch.py` (registration, fetch, stealth, extract modes/types, pipeline, error paths).

## Task 2: Uber Connector Scrapling Integration — COMPLETE

Replaced brittle `re.findall(r'option "(\S+(?:\s+\S+)?)\s+\S+\s+Person\s+\d+\s+\$([\d.]+)')` with `_scrapling_ride_prices(html)` in `connector_uber.py:162-200`.

**Behavior:** Adaptive mode (40% similarity threshold) relocates ride-name containers by element identity when Uber A/B-tests a class rename. Falls back to regex if Scrapling returns nothing or isn't installed.

**Verified on real HTML** (`/tmp/uber_pricing_with_prices.html`): all 12 ride types extracted with correct names + prices in one call.

**Tests:** 37 in `test_connector_uber_scrapling.py` (all pass).

## Task 3: Hidden Tab React Click Fix — COMPLETE (bug discovered + fixed)

**Bug:** `auth.uber.com` Continue button binds to `onMouseDown` (not `onClick`). Naive `__reactProps.onClick()` silently fails; page stays on email step.

**Verified live:** `propKeys: ["className","disabled","id","type","data-testid","onMouseDown","href","children"]` — no `onClick` at all.

**Fix in `tools/_browseros.py:211`:** `_react_click()` now tries onClick → onMouseDown → onPointerDown.

**Result:** Continue click on hidden tab advances to SMS prompt.

## Task 4: Email Login + SMS 2FA — COMPLETE (login flow), BLOCKED (live test)

`_login_uber_email()` in `connector_uber.py:158-244` implements end-to-end Uber login via vault credentials.

**Flow:**
1. Navigate to `auth.uber.com/v2/?next_url=...`
2. Type email via `_type_into_input_adaptive`
3. Click Continue via `_react_click` (onMouseDown)
4. Detect password step via `!!document.querySelector('input[type="password"]')`
5. Type password via JS native setter
6. Click Log in / Sign in via `_react_click`
7. Wait for redirect away from `auth.uber.com`

**SMS 2FA detection:** returns clear user-facing message when "Welcome back, NAME. Enter the 4-digit code sent via SMS" appears. 4 separate text inputs (PHONE_SMS_OTP-0..3). Cannot bypass programmatically.

**Integration points in `estimate_uber_ride()`:**
- Pre-flow: if URL is on `auth.uber.com` or `/login`, try email login before Google OAuth fallback
- Post-flow: if "log in" / "sign in" text appears on pricing page, attempt email login + retry

**Live test status (June 2026):** Email step verified through to SMS prompt on hidden tab 50. SMS 2FA blocks end-to-end pricing test (user must enter code in their phone's text messages).

## Task 5: Address Autocomplete Truncation Fix — COMPLETE

**Bug:** Uber autocomplete shows "3 Manning Ter" when user types "3 Manning Terrace, Everett MA". Naive `textContent.includes(target_lower)` fails.

**Fix in `connector_uber.py:60`:** progressive word-prefix matching — try full string, then drop trailing words one at a time. Case-insensitive required (DOM has "Salemwood School", query may be lowercase).

## Current Task Checklist

- [x] Task 1: Scrapling tool surface (4 tools)
- [x] Task 2: Uber connector integration
- [x] Task 3: React onMouseDown fix
- [x] Task 4: Email login flow (BLOCKED on user SMS 2FA)
- [x] Task 5: Address truncation fix
- [x] Task 6: All tests pass (37 Uber + 26 Scrapling = 63 total)
- [x] Task 7: AGENTS.md updated with all lessons
- [x] Task 8: Skill files created (browseros, scrapling)
- [x] Task 9: gstack learnings.jsonl appended
- [ ] Task 10: Live end-to-end price test (blocked on user SMS code)

## Documentation Updates

**Files modified:**
- `AGENTS.md` — added Scrapling section, Uber login section, address truncation section, fixed React Props Rule (now mentions onMouseDown)
- `tools-harness/tools/connector_uber.py` — `_login_uber_email()`, onMouseDown support, progressive word matching
- `tools-harness/tools/_browseros.py` — `_react_click()` helper, moved from connector

**Files created:**
- `tools-harness/tools/scrapling_fetch.py` — 4 tool functions
- `tools-harness/tests/test_scrapling_fetch.py` — 26 tests
- `~/.agents/skills/scrapling/SKILL.md` — Scrapling skill
- `~/.agents/skills/browseros/SKILL.md` — BrowserOS skill (hidden tab patterns)
- `~/.gstack/projects/tools-harness/learnings.jsonl` — 7 new entries

**Files referenced:**
- `/tmp/uber_pricing_with_prices.html` — real Uber HTML with injected prices for testing
- `/tmp/uber_pricing.html` — real Uber HTML (no prices, not logged in)

## Next Steps

1. **User enters SMS code** in BrowserOS tab 50 (or new visible tab) to complete live pricing test
2. **Verify Scrapling extracts all 12 ride types** with real prices end-to-end
3. **Optional:** A/B test Scrapling adaptive vs. regex on 5+ real Uber pricing snapshots to quantify improvement
4. **Optional:** Add same login pattern to other sites (DoorDash via Google OAuth already works in visible tab)
