# Finding something on a long page

The failure this is written for: asked for the January 2025 issue of a journal
archive, the orb twice answered "not on this page" — while the issue existed behind
a period filter. Saying "not there" after one look is the single most common way an
agent is confidently wrong.

## The ladder — in this order, and do not skip a rung

**1. `find_on_page(text)`** — it scrolls and observes until the label is on screen,
and reports how many pages it scrolled. It stops at the bottom by noticing the view
stopped changing, so `found: false` means *it actually went to the bottom*, not that
it glanced once.

**2. If not found, read the `filters` it returns.** An archive that only shows recent
entries keeps older ones behind a **period / year / category** control. A closed
dropdown lists nothing: `observe` cannot show you its options until it is open.
- click it (`act` on its id, or `click_text` if it is not in the accessibility list)
- `observe` again — the year options are now real elements
- pick the year, then `find_on_page` the month

**3. Only then say it is absent.** And say what you did: "scrolled to the bottom of
the 24 shown, and opened the Période filter which offers 2025 / 2024 — no January
2025 entry under either."

## Things that look like the answer and are not

| trap | why it fails |
|---|---|
| typing into the search box | a site search may be a different index; the archive filter is authoritative |
| `read_page_text` returning little | a React/SPA page renders after load, and its data often comes from JSON |
| `observe()` not listing a card | the accessibility tree holds **only the viewport** — no scroll extent is reported, which is why step 1 exists |
| a screenshot showing nothing | true and useless: the cards are below the fold. This confirms the fold, not the absence |
| clicking a card that "looks like" the right one | two volumes can differ by two digits (No 44 vs No 48). Confirm the exact label before clicking |

## When the data is easier than the UI

If the page renders from an API, `scrapling_api_capture(url, contains="2025-01")`
returns the row itself — id, date, and any file link — in one call. Prefer that over
clicking through filters when the goal is the data rather than the demonstration.
