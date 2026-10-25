# 0006 — Debug ports are for us; the extension is for shipping

Status: accepted (2026-10-25). Code: `scripts/dev_cdp_chrome.sh`,
`tools/headed_browser.py` (`CLIXEN_BROWSER_CDP_URL`), `tools/console_buffer.py`.

## Context

Console logs, network failures and DOM internals need Chrome DevTools Protocol. CDP
needs the browser started with `--remote-debugging-port`, which is a debugging
surface, opens a local port, and cannot be asked of a paying user on a DMG.

Two pressures pull in opposite directions:

- **Testing needs eyes.** "Audit this site's console" is unanswerable through
  AppleScript/AX; we proved that by trying (`browser_*` was a *separate* Playwright
  session, and the user's Chrome had no port).
- **Shipping must stay boring.** A desktop app that asks you to relaunch Chrome with
  a debug port, or that wants "read and change all your data on all websites", has
  lost before it started.

## Decision

**Never ship a debug port. Use one only in development.**

`scripts/dev_cdp_chrome.sh` launches a throwaway Chrome — separate `--user-data-dir`
under `/tmp`, port 9333 — so the developer's real profile is never opened with a
debugging port. `CLIXEN_BROWSER_CDP_URL` makes the harness attach to it, and console
capture then works there (`browser_open` → `browser_navigate` → `browser_console`).

Attaching *after* a page has loaded only sees messages from that moment on, so an
audit reloads the page first. That is a property of CDP, not a bug to paper over.

**For the shipped app, the extension is the delivery vehicle** — and a thin one:

- `activeTab` + `scripting.executeScript`, **no host permissions**. Nothing is
  readable until the user acts (clicks the bunny / presses the hotkey), and only on
  the tab they are looking at.
- What that buys: console + page errors for the tab the user is watching, DOM
  structure better than AX, reliable tab identity (no more two-profile ambiguity),
  and no relaunch, no port.
- What it costs: an install step, a permission prompt at first use, MV3
  service-worker lifecycle, and — honestly — only data captured from injection
  onward unless the user re-loads the page.

## Consequences

- The DMG's permission story stays small: microphone, Accessibility, Apple Events.
  No debugging port, no "read all sites".
- Our own testing gets full CDP without weakening the product's posture.
- A console/network audit of the user's own tab becomes possible only through the
  extension, and only on demand — the standing-access alternative (host permissions
  + always-on content script) is rejected because it reads everything the user types.

## Addendum: what the dev build taught us (2026-10-25)

Verified end to end against a real Chromium + real page: the extension polls the core
(`/ext/poll`), executes in the watched tab, and reports back; the orb called
`page_probe` twice and got a real page error on the second call. Four findings worth
recording, because each one is a trap for the shipped version too:

1. **MV3 suspends the worker after ~30 s.** An alarm heartbeat is the only wake-up
   Chrome guarantees, so the bridge is poll-on-heartbeat, not a live socket. A tool
   call may therefore wait up to one tick; the core waits instead of failing.
2. **`executeScript` runs in the ISOLATED world by default.** Patching `console`
   there patches a copy the page never calls. Console capture needs `world: "MAIN"`.
3. **A missing `storage` permission fails silently** — `chrome.storage` is undefined,
   `config()` throws, and so does the code path that would have reported it. The
   bridge now reports its own failures to `/ext/log`, which is how this was found.
4. **`--load-extension` no longer works on stable Chrome (156).** Automated testing
   needs Chrome for Testing or Playwright's Chromium; a human loading unpacked in
   Developer mode is the supported path for real use.

The permission model is unchanged and still the point: `activeTab` + `scripting`,
so a tab is readable only after the user acts on it.
