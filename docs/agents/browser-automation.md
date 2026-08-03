# Browser Automation — 3 Approaches plus Local Vision

Three browser automation methods, plus local vision for screenshot analysis:

## 1. Playwright DOM (fast, detectable)
```
browser_navigate → browser_snapshot → browser_click → browser_get_content
```
Uses gemma4 to reason over HTML text snapshots. Stable CSS selectors. Fast (<2s/action).
**When**: SaaS dashboards, internal tools, any site without bot detection.
**Limitation**: CDP automation flags (navigator.webdriver). Blocked by DoorDash/Uber/banking.

## 2. BrowserOS CLI (undetectable, no vision)
```
browseros_navigate → browseros_snap → browseros_click/fill → browseros_text
```
Controls real Chromium desktop app via BrowserOS CLI. No bot detection. Element IDs are per-session (always snap before click).
**When**: DoorDash, Uber, Google, banking, any site that blocks CDP.
**Tools**: `browseros_navigate`, `browseros_snap`, `browseros_click`, `browseros_fill`, `browseros_text`, `browseros_screenshot`, `browseros_eval`, `browseros_pages`, `browseros_close`

## 3. Vision (BrowserOS + qwen3-vl:8b)
```
vision_open → vision_snap (screenshot + qwen3-vl analysis) → vision_click → vision_extract
```
Sends screenshots to qwen3-vl:8b vision model. Understands visual layout, canvas content, images. Slower (~10s per snap).
**When**: Canvas-rendered pages (Figma, games, maps), verifying visual appearance, image-heavy sites.
**Tools**: `vision_open`, `vision_snap`, `vision_click`, `vision_type`, `vision_extract`, `vision_close`

## 4. Local Vision (Tesseract OCR + OpenCV, no ML model)
```
local_vision_snap → local_vision_highlight
```
No ML models. Uses Tesseract OCR + OpenCV contour detection on screenshots.
Finds text blocks with positions (116 blocks in 1.2s from a DoorDash page).
Detects UI elements (buttons, inputs, cards) via pixel/contour analysis.
**When**: Need to understand page layout, find text positions, or click by coordinates.
Faster than qwen3-vl (~1s vs 15-30s) and deterministic.

## Migration Flow (User Login → Automated Actions)

```
1. session_login("doordash")           → opens visible browser, user logs in manually (handles 2FA/CAPTCHA)
2. browser_navigate("...")              → use Chrome profile (cookies persist) or BrowserOS (undetectable)
3. browseros_snap / browseros_click     → interact via BrowserOS CLI (no CDP flags)
4. browseros_text / browser_get_content → extract data
```

BrowserOS installed at `/Applications/BrowserOS.app`. MCP server at `http://127.0.0.1:9000/mcp`.
CLI at `~/.browseros/bin/browseros-cli`. Launch: `browseros-cli launch`.

## Hidden Tab Mode (Headless Automation)

New as of May 2026: `browseros-cli open <url> --hidden` creates invisible tabs.
All CLI commands (snap, eval, click, fill, text) work against hidden tabs via `-p <page_id>`.
Page ID extracted from `open --hidden` output; subsequent commands pass `-p <id>` to target it.

**Key discoveries from hidden-tab testing (Uber)**:

| Method | Visible tab | Hidden tab | Works? |
|--------|------------|------------|--------|
| `browseros-cli click <id>` | OK (real coords) | (0,0) for many elements | Broken |
| `browseros-cli fill <id> "text"` | OK (real coords) | Types at (0,0), input stays empty | Broken |
| `browseros-cli key <char>` | OK | No effect | Broken |
| JS `el.click()` + `dispatchEvent(MouseEvent)` | OK | React ignores synthetic events | Broken |
| JS `dispatchEvent(KeyboardEvent)` | OK | React ignores | Broken |
| JS `native value setter` + `input`/`change` events | OK | Sets value, triggers autocomplete (sometimes) | Partial |
| JS `__reactProps.onClick()` | OK | **Reliable when onClick is bound** | Works |
| JS `__reactProps.onMouseDown()` | OK | **Required when only onMouseDown is bound** | Works |

**The React Props Rule**: For React-controlled elements in hidden tabs, the only way to trigger the bound handler is to call `element.__reactProps[<event>]()` directly. All synthetic DOM events (click, mousedown, keydown, input, change) are silently ignored by React's event system when `visibilityState === 'hidden'`. **Always probe `__reactProps` to see which event is bound** — Uber's auth.uber.com Continue button binds to `onMouseDown` (NOT `onClick`), which is why naive React click silently fails. Use the `_react_click()` helper in `tools/_browseros.py:211` which auto-tries onClick → onMouseDown → onPointerDown.

**Typing into inputs in hidden tabs**: `fill` and `key` are broken. Use JS native value setter + PointerEvent + FocusEvent sequence:
```js
inp.dispatchEvent(new PointerEvent('pointerdown', {bubbles: true, cancelable: true}));
inp.dispatchEvent(new PointerEvent('pointerup', {bubbles: true, cancelable: true}));
inp.dispatchEvent(new MouseEvent('mousedown', {bubbles: true}));
inp.dispatchEvent(new MouseEvent('mouseup', {bubbles: true}));
inp.dispatchEvent(new MouseEvent('click', {bubbles: true}));
inp.focus();
inp.dispatchEvent(new FocusEvent('focusin', {bubbles: true}));
const native = Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, 'value').set;
native.call(inp, text);
inp.dispatchEvent(new Event('input', {bubbles: true, composed: true}));
inp.dispatchEvent(new Event('change', {bubbles: true, composed: true}));
```

**Option selection in hidden tabs** (Uber suggestion list items):
```js
// Find LI with role=option, then call React's onClick directly
const items = document.querySelectorAll('li[role="option"]');
for (const li of items) {
    if (li.textContent.toLowerCase().includes(target)) {
        li.click();  // native (for good measure)
        const pk = Object.keys(li).find(k => k.startsWith('__reactProps'));
        if (pk && li[pk] && li[pk].onClick) {
            li[pk].onClick(new MouseEvent('click', {bubbles: true}));
        }
    }
}
```

Implementation: `tools/_browseros.py` (317 lines, reusable BrowserOS interaction layer).
Uber connector: `tools/connector_uber.py` (389 lines) imports from `_browseros.py`.

## Hidden Tab Architecture

```
tools/_browseros.py          tools/connector_uber.py
─────────────────────        ────────────────────────
_run() — subprocess CLI      estimate_uber_ride()
_open_hidden() — --hidden    _set_location()
_parse_snap() — structured   _click_first_real_option()
_discover_page_state()       get_my_uber_trips()
_find_clickable_by_text()    schemas
_type_into_input_adaptive()
_click_snap_id()
```

`_HIDDEN_PAGE` global (set by `_open_hidden`) is appended as `-p <id>` to all subsequent `_run()` calls.

## Scrapling — Adaptive Web Scraping (June 2026)

Scrapling (`pip install scrapling`) integrated as **optional** dependency. Zero regression if uninstalled (`_HAS_SCRAPLING` flag in both `tools/connector_uber.py:25-30` and `tools/scrapling_fetch.py:23-25`).

**Two roles:**

### 1. Internal: Uber price extraction
`_scrapling_ride_prices(html)` in `connector_uber.py:162-200` is the primary path for parsing Uber product-selection HTML. Adaptive mode relocates ride-name containers by element identity (40% similarity threshold) when Uber A/B-tests a class rename. Regex fallback in `connector_uber.py:402-413` kicks in only if Scrapling returns nothing or the lib isn't installed.

Verified on real Uber HTML (`/tmp/uber_pricing_with_prices.html`): extracts all 12 ride types with names + prices in one call.

### 2. Public: `scrapling_fetch` tool (4 tools)
New tool surface in `tools/scrapling_fetch.py` for general adaptive scraping — registered in `tools/registry.py` and exposed via the agent harness:

| Tool | Purpose |
|------|---------|
| `scrapling_fetch(url)` | Plain HTTP fetch via `Fetcher.get()` |
| `scrapling_stealthy_fetch(url, headless=True)` | Stealth fetcher with `headless=True` (only StealthyFetcher accepts it — `Fetcher` rejects `headless` kwarg) |
| `scrapling_extract(url, selector, mode, extract_type, percentage)` | Extract by CSS selector, adaptive mode |
| `scrapling_fetch_and_extract(url, selector, mode, extract_type, percentage)` | One-call fetch + extract pipeline |

**Three extract modes:**
- `auto_save` (default on first call) — extracts and stores element fingerprint at `~/.scrapling/adaptors/<domain>/<selector>.json`
- `adaptive` — uses saved fingerprint to relocate elements that may have moved/renamed
- `raw` — extracts without fingerprint, no relocation

**Four extract types:** `text`, `html`, `attrs`, `json`.

**Gotchas** (caught during integration):
- `Fetcher.get(url)` — NOT `.fetch()`. Raises `TypeError: Session.request() got an unexpected keyword argument 'headless'` if you pass `headless`.
- `Selector(content=html, adaptive=True, url=f"https://{domain}/")` — content kwarg (not `text=`); url needed for adaptive storage key.
- `.html_content` — the property is `.html_content` (NOT `.html` or `.text_content`).

**Tests:** 26 in `tools-harness/tests/test_scrapling_fetch.py` (registration, fetch, stealth, extract modes/types, pipeline, error paths), 37 in `tools-harness/tests/test_connector_uber_scrapling.py` (Uber integration). All pass.

**When to use Scrapling vs. existing tools:**
- Scrapling wins on sites with class-name churn (SPA A/B tests, marketing sites) where regex is brittle
- BrowserOS still wins on login flows, typing, clicking (Scrapling doesn't drive browsers)
- Plain `requests` + `BeautifulSoup` wins on simple static sites (faster, no install)

## Uber Login — Email + SMS 2FA (June 2026)

`_login_uber_email()` in `connector_uber.py:158-244` implements end-to-end Uber login via vault credentials (`tools.vault._kc_get("uber")` returns `{username, password}`).

**Flow:**
1. Navigate to `https://auth.uber.com/v2/?next_url=https%3A%2F%2Fm.uber.com%2Fgo%2Flogin-redirect%2F`
2. Type email via `_type_into_input_adaptive` (or JS native setter as fallback)
3. Click Continue via `_react_click` (onMouseDown, NOT onClick — see React Props Rule above)
4. Detect password step via `!!document.querySelector('input[type="password"]')` (more reliable than text scan)
5. Type password via JS native setter
6. Click Log in / Sign in (also via `_react_click`)
7. Wait for redirect away from `auth.uber.com`

**If SMS 2FA triggered** ("Welcome back, NAME. Enter the 4-digit code sent via SMS at ******8324."):
- Returns clear error: `"[uber] SMS 2FA required. Open the BrowserOS tab and enter the SMS code, then call estimate_uber_ride() again."`
- 4 separate text inputs (`PHONE_SMS_OTP-0` through `PHONE_SMS_OTP-3`) for the 4-digit code
- User must complete SMS manually; cannot be bypassed programmatically

**Integration points in `estimate_uber_ride()`:**
- Pre-flow: if URL is on `auth.uber.com` or `/login`, try email login before Google OAuth fallback
- Post-flow: if "log in" / "sign in" text appears on pricing page, attempt email login + retry full flow

**Live test status (June 2026):** Email step verified through to SMS prompt on hidden tab. SMS 2FA blocked end-to-end pricing test (requires user to enter code in their phone's text messages).

## Uber Address Autocomplete — Truncation Gotcha

Uber's address autocomplete UI truncates long street names. User types "3 Manning Terrace, Everett MA" but the suggestion list shows "3 Manning Ter". Naive `textContent.includes(target_lower)` fails.

**Fix in `_click_first_real_option` (`connector_uber.py:60`):** progressive word-prefix matching — try full address, then drop trailing words one at a time until a match is found:

```python
for n in range(len(words), 0, -1):
    candidate = ' '.join(words[:n]).lower()
    if candidate in text_lower:
        # match found
```

Same `case-insensitive` matching required (DOM has "Salemwood School" but user query may be lowercase).

## BrowserOS Search Tools (undetectable web search)

Fallback web/image search via real browser (no API keys, undetectable):
- `browseros_search("query", engine="duckduckgo"|"brave")` — text search, ~3-4s
- `browseros_search_images("cute cats", count=5)` — image search via Google Images, downloads locally

Use when SearXNG/DDG are unavailable or you need undetectable search.

## Session Management

Sessions saved to `~/.config/g4l/sessions/<name>.json`:
- `browser_save_session(name)` — save cookies + localStorage
- `browser_load_session(name)` — restore into incognito browser
- `session_login(service)` — interactive login (visible browser, user handles CAPTCHA/2FA)
- `session_check(service)` — verify session still valid

When Chrome is closed, browser.py auto-detects the real Chrome profile at
`~/Library/Application Support/Google/Chrome` and uses `launch_persistent_context`
for stealth + persistent cookies. Falls back to incognito when Chrome is running.

## Credential Vault

macOS Keychain-backed credential storage for automated logins.

Tools:
- `vault_save(service, data)` — store credentials (encrypted at OS level)
- `vault_get(service)` — retrieve credentials
- `vault_list()` — list stored services (never exposes passwords)
- `vault_delete(service)` — remove credentials
- `vault_login(service)` — auto-fill BrowserOS login form from vault credentials

Vault entries stored in macOS Keychain, accessible via `security` CLI.
Session tokens stored separately in `~/.config/g4l/tokens/<domain>.enc` (Fernet encrypted).

## API Data Extraction Agent

4 tools for API-first data extraction (skips browser rendering):
- `api_fetch(domain, endpoint, params)` — httpx call with auto token refresh
- `api_discover(domain)` — guide for manual API reverse-engineering
- `api_config_from_curl(domain, curl_command)` — convert cURL to domain config
- `api_list_domains()` — list configured domains with token status
- `api_extract(domain, data_type)` — extract from SPA Apollo/React cache via BrowserOS JS eval

Configs stored at `~/.config/g4l/api_configs/<domain>.json`.
Tokens stored at `~/.config/g4l/tokens/<domain>.enc` (Fernet-encrypted).
Data saved to `~/.config/g4l/data/<domain>_<endpoint>_<timestamp>.json`.

Token refresh flow: 401 → BrowserOS re-auth → extract cookie → retry.

## Service Connectors (single-call, small-model friendly)

High-level tools that handle all complexity internally — no multi-step orchestration needed.

### `get_my_orders(service="doordash")`

Single call. Returns your orders from DoorDash.

**What it does internally:**
1. Navigates to the orders page via BrowserOS CLI
2. If not logged in (redirected to `identity.doordash/auth`):
   - Clicks "Continue with Google"
   - Selects the matching account from Google account picker (by email from vault)
   - Clicks "Continue" on OAuth consent screen if shown
   - Waits for redirect back to DoorDash
3. If SMS 2FA is triggered by Google OAuth flow: tells you to enter the code in BrowserOS
4. Extracts order data from the in-memory Apollo GraphQL cache
5. Saves full JSON to `~/.config/g4l/data/`
6. Returns formatted human-readable summary

**Design principle:** ONE function call. No multi-step tool chains. Google-only auth — no email/password path. Small models (gemma4, qwen3) can't reliably orchestrate 5+ browser tool calls — this eliminates the failure mode entirely.

### Account Types

| Service | Auth method | How it works |
|---------|------------|---------------|
| DoorDash | Google OAuth | Clicks "Continue with Google" → Google account picker → selects saved account → OAuth consent |
| Uber | Email/password | Auto-fills from vault, handles redirects |

### Data Extraction Strategy

DoorDash (and similar SPAs like Uber) use Apollo GraphQL client. The order data is loaded into an in-memory cache after page navigation. `get_my_orders` extracts from this cache via `window.__APOLLO_CLIENT__.cache.extract()` rather than re-fetching from the API — no GraphQL query reverse-engineering needed.

## DoorDash Discovery Notes (from testing)

- **Login**: Google OAuth via `identity.doordash.com/auth` iframe in a modal
- **API**: GraphQL at `iguazu-edge/v1/p2` (batch endpoint)
- **Data**: Apollo cache has `ConsumerOrderWithDetails`, `ConsumerOrderOrderItem`, `ConsumerOrderStore` entities
- **Session**: Cookies persist in BrowserOS profile after Google OAuth
- **Orders URL**: `https://www.doordash.com/orders/`

## Sofascore Sports Data Connector

7 tools in `tools/connector_sofascore.py`. Uses Sofascore's undocumented API at `api.sofascore.com/api/v1/` with `curl_cffi` (Chrome TLS impersonation) to bypass the WAF. No API key needed.

| Tool | What it does |
|------|---------------|
| `sofascore_search_teams(query, sport)` | Search teams by name, returns team IDs |
| `sofascore_get_team(team_id)` | Team details, last results, upcoming fixtures |
| `sofascore_scheduled_events(sport, date)` | All matches for a sport+date |
| `sofascore_live_scores(sport)` | Currently live matches with scores |
| `sofascore_get_event(event_id)` | Full match: score, incidents, lineups, stats |
| `sofascore_get_standings(tournament_id)` | League table (auto-detects latest season) |
| `sofascore_get_tournaments(sport)` | Top tournaments/leagues for a sport |

**Sport slugs:** football, basketball, tennis, american-football, baseball, ice-hockey, mma, esports, rugby, cricket, volleyball, handball

**Primary path:** RapidAPI "SportAPI" subscription at `sportapi7.p.rapidapi.com` — fast (~192ms), reliable, no WAF issues. The user's key (`f497b9c6a8mshd2a0081e2034c15p1db7b2jsn132c383e69d3`) is embedded in the connector. BASIC plan ($0/mo) is sufficient.

**Fallback:** Direct API at `api.sofascore.com/api/v1/` with `curl_cffi` (Chrome TLS impersonation) — only used if RapidAPI fails. Plain `httpx`/`requests` triggers Sofascore WAF (403).

## Uber Discovery Notes (from deep integration testing)

### Key Architectural Differences from DoorDash

| Aspect | DoorDash | Uber |
|--------|----------|------|
| Data framework | Apollo GraphQL cache (`__APOLLO_CLIENT__`) | Baseweb React components, no Apollo |
| Trip data extraction | Cache extract via JS | Page text from `riders.uber.com/trips` |
| Login | Google OAuth via iframe modal | Google OAuth via `auth.uber.com` |
| Price estimates | N/A (already in orders) | Separate page: `m.uber.com/go/product-selection` |

### Uber Login Flow

Same pattern as DoorDash — Google OAuth:
1. Navigate to `www.uber.com/login/`
2. Click "Log in to your Uber account"
3. Click "Continue with Google" (button with ID from snap)
4. Google account picker appears → select `jimkalinov@gmail.com`
5. OAuth consent → redirected to rider home

### Uber Ride Price Estimation Flow

`estimate_uber_ride()` in `tools/connector_uber.py`. Hidden-tab mode (no visible windows). Flow:

1. **Open hidden tab** → `browseros-cli open <url> --hidden` → page ID tracked in `_HIDDEN_PAGE`
2. **Navigate** to `m.uber.com/go/home` — starts at the mobile booking form
3. **Set pickup** (optional — defaults to current location if omitted):
   - `_discover_page_state()` classifies current page (home/pickup/pricing/etc.)
   - If home: JS click "Pickup location" button → navigates to `/go/pickup`
   - `_type_into_input_adaptive(address, searchbox_label="Pickup location")` — types via JS native setter + PointerEvent + FocusEvent sequence
   - Wait for autocomplete suggestions to appear
   - `_click_first_real_option(target)` — finds LI[role=option] containing address, calls `__reactProps.onClick()` directly
   - Progressive shortening: "salemwood school malden" → "salemwood school" → "salemwood" (Uber truncates, case-insensitive matching)
4. **Set dropoff**:
   - After pickup selected, page auto-navigates to `/go/drop?pickup=...`
   - Same click + type + React props selection pattern as pickup
   - Dropoff input is index 1 (second `input[type="search"]`), matched by placeholder text
5. **Click "Pickup now"** → goes to date/time page
6. **Click "Next"** (time defaults to "Now") → goes to `/go/product-selection` pricing page
7. **Read prices** — Parse from snap output (e.g. `option "UberX UberX Person 4 $23.94"`)

### Key Implementation Details

- **Hidden tabs only**: All interaction uses `open --hidden` + `_HIDDEN_PAGE` tracking. No visible windows.
- **React props required for option selection**: BrowserOS `click` sends (0,0) in hidden tabs. JS synthetic events (MouseEvent, KeyboardEvent) are ignored by React. Only `element.__reactProps.onClick()` directly triggers Uber's selection handler.
- **Full event sequence for typing**: `fill` and `key` are broken in hidden tabs. Must use JS native value setter + PointerEvent(`pointerdown`/`pointerup`) + MouseEvent(`mousedown`/`mouseup`/`click`) + `focus()` + `FocusEvent('focusin')` + `input`/`change` events to trigger autocomplete.
- **Case-insensitive option matching**: `textContent` comparison must use `.toLowerCase()` — DOM has "Salemwood School" but user query may have "salemwood school".
- **Pickup first, then dropoff**: After pickup selection, Uber navigates to `/go/drop?pickup=...` automatically. Both searchboxes exist on all pages.
- **Progressive option matching**: Tries full address first, then progressively shorter word fragments (Uber truncates long street names).
- **Element IDs are per-session**: Always snap before every interaction. Discovery layer auto-adapts to DOM shifts.
- **File split**: Reusable BrowserOS layer in `tools/_browseros.py` (317 lines). Uber-specific logic in `tools/connector_uber.py` (389 lines).

### Price Results (from test: 840 Harrison Ave → 3 Manning Terrace, Everett)

| Ride Type | Price | ETA | Seats |
|-----------|-------|-----|-------|
| UberX | $32.96 | 3 min | 4 |
| Share | $28.91 | 3 min | 1 |
| Comfort | $36.98 | 2 min | 4 |
| UberXL | $44.97 | 4 min | 6 |
| Electric | $32.94 | 10 min | 4 |
| Black | $59.97 | 5 min | 4 |
| Black SUV | $71.99 | 5 min | 6 |
| Taxi | $42-55 | - | 4 |

### Uber Wallet / Payment Methods

**URL**: `https://wallet.uber.com/` (separate subdomain, NOT `/payments` on riders)

**Access**: Jim profile menu → "Wallet" on `riders.uber.com/trips`

**Known payment methods** (from jimkalinov@gmail.com account):

| Card | Status |
|------|--------|
| Visa ****8864 (Benbofa) | Preferred (current default personal) |
| MasterCard ****7011 (Credit) | Active |
| American Express ****1894 | Active |
| Apple Pay | Active |
| Visa ****3299 (Chime Jim) | Active |
| Visa ****4545 (Bofa Jim) | Active |
| Visa ****7430 (Jim) | Active |

**Actions available**:
- Add Payment Method — add new card
- Personal → Visa - 8864 (Benbofa) — manage profile defaults
- Add bank account — for receiving payouts
- Gift card — add gift cards
- Add voucher — add vouchers

**Gotchas**:
- `riders.uber.com/payments` redirects to `/trips` — real payment page is separate subdomain `wallet.uber.com`
- `m.uber.com/go/wallet` and all `m.uber.com/go/*` account URLs redirect to home — no payment management via mobile web

### Uber Eats

**URL**: `https://www.ubereats.com/`

**Auth**: Separate from Uber rides. User logged into rides (Jim) is NOT logged in on Uber Eats.

**Flow**:
1. Enter delivery address in combobox
2. Address autocomplete shows suggestions with proper formatting
3. Select an address → redirects to `ubereats.com/feed?diningMode=DELIVERY&pl=...`
4. Restaurant/market feed loads with 200+ listings sorted by relevance/distance/offers

**Categories**: Restaurants, Grocery, Convenience, Alcohol, Health, Retail, Pet, Flowers, Baby, Personal Care, Electronics + cuisine filters (Pizza, Sushi, Chinese, Indian, Thai, Mexican, etc.)

**Restaurant listing format**: `[name]Heart outline[rating]Star([reviews]) • [min] min` with delivery fee and offers

**Example restaurants** (840 Harrison Ave): KFC, Chipotle, Domino's, Popeyes, McDonald's, Shake Shack, Five Guys, Panera, Chick-fil-A, Panda Express, CAVA, sweetgreen, Starbucks + 200 more

**Grocery/Retail**: Target, CVS, Best Buy, Home Depot, PetSmart, Sephora, Ulta, Total Wine, Star Market, Wegmans, ALDI, H Mart, 99 Ranch Market

**Not explored**: Order placement flow (requires login), cart management

### Uber Courier (Package Delivery)

**URL**: `https://m.uber.com/go/connect/home` (mobile web only)

**Access**: From `m.uber.com/go/home` → tap "Courier" tab

**Auth**: Uses the same Uber rides session (Jim is logged in)

**3 tabs**:

| Tab | Purpose |
|-----|---------|
| Send | Have a courier deliver something for you |
| Receive | Get packages delivered to you |
| Store Pickup | Pick up from stores |

**Flow**:
1. Set sender/pickup location (click combobox, type address or pick from saved/recent)
2. Click "Confirm pickup" after selecting sender
3. Set recipient/dropoff location (same pattern)
4. Click "Search" to see available courier options

**Location selection pattern**:
- Click the sender/dropoff clickable (NOT the combobox directly) — the clickable has text like `"Radio button selectedChoose sender's location"` or `"DropoffChoose recipient's location"`
- A dropdown expands with recent addresses + "Saved places" + "Set location on map"
- Type into combobox to search addresses
- Select an option → "Confirm pickup" button appears
- Click confirm → returns to main courier page with location set

**Addresses tested and working**:
- Typed "830 Harrison Ave, Boston" → showed 3 suggestions: BMC Moakley Building, Surgical Oncology at BMC, and the main address
- Recent addresses list includes: HOME, 17 Franklin St Somerville, 243 Charles St Boston, 800 Huntington Ave, 43 Norwood St Everett, Forest Hills MBTA, etc.

**Profile**: Shows "For me" profile by default — click to switch to Business profile

**Pricing**: Not explored (need to complete both locations + click Search)

**Gotchas**:
- `partners.uber.com` and `drivers.uber.com` redirect to `bonjour.uber.com` vehicle onboarding (for driver signup, NOT courier delivery)
- Uber Courier is a package delivery service (like DoorDash DashLink), NOT food delivery driver signup
- The Courier page is only on mobile web (`m.uber.com`), not on desktop
- Element IDs change per session — always snap before click
- "Search" button stays disabled until both sender AND recipient locations are set
