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
