# Clixen dev bridge (unpacked extension)

Gives the local core console output, page errors, DOM and click-by-label **on the tab
you are looking at** — the one thing AppleScript/AX cannot see. Dev-only for now
(ADR 0006); the shipped app never asks for a debug port.

Scope by construction: `activeTab` + `scripting`, no host permissions beyond
localhost. Nothing is readable until you click the toolbar button, and only on the
tab you clicked it on.

## Load it (3 steps)

1. `chrome://extensions` → turn on **Developer mode** → **Load unpacked** → pick this
   `extension/` folder.
2. Right-click the extension → **Options** → paste the core token:
   ```bash
   security find-generic-password -s clixen.localhost.token -w | python3 -c 'import json,sys; print(json.load(sys.stdin)["value"])'
   ```
   Core URL stays `http://127.0.0.1:9234` unless you moved it.
3. Open the tab you want the orb to see, click the extension's toolbar button **once**
   (that click is the user gesture that grants the tab), then ask:

   ```bash
   python -c 'import sys; sys.path.insert(0,"tools-harness"); from tools.registry import execute_tool; print(execute_tool("page_probe", {"kind":"console","level":"error"}))'
   ```

## What it answers

| `page_probe` kind | Returns |
|---|---|
| `console` | console messages + `window.onerror` / unhandled rejections, with an error count |
| `dom` | visible elements (tag, role, label, href) + any consent-looking controls |
| `text` | `document.body.innerText` |
| `click` | presses the visible control whose label matches, and says which one |

## What it took to make this work (four real failures, measured)

| Symptom | Cause | Fix |
|---|---|---|
| `page_probe` timed out ~1 min after load | Chrome suspends the MV3 worker after ~30 s idle | 30 s `alarms` heartbeat + a bounded 25 s poll burst; the core waits out one tick instead of failing |
| Nothing polled at all, silently | manifest never declared `storage`; `chrome.storage` was undefined, so `config()` threw — *and so did the error report* | `"storage"` added |
| Two probes, zero entries, page logging every 4 s | `executeScript` defaults to the ISOLATED world, so we patched a `console` the page never uses | `world: "MAIN"` |
| `--load-extension` produced no extension | Chrome 156 (stable) ignores that flag | load unpacked by hand, or run Chrome for Testing |

## Limits, stated plainly

- **From injection onward.** The page hooks are installed when you click the button,
  so messages logged before that are gone. Reload the page after granting it if you
  need the full boot sequence.
- **No standing access.** Every tab needs its own click; there is no "watch all
  sites" mode, by design.
- **Not shipped.** Loading unpacked is a developer step. Distribution (store or
  managed install) is a separate decision.
- A dead core or a stale token makes the extension back off and retry every 1.5 s —
  it never spins hot, and `page_probe` reports `not connected` instead of hanging.
