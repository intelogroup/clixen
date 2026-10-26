# 0007 — Every action is visible; headless is an implementation detail

**Status:** accepted (2026-10-25)

## Context

The orb is a *visual* agent. People run it because they want to watch the work happen on
their own screen. A headless browser agent answers "what did it do?" with a transcript; a
visual agent answers it by being lookable.

We drifted. Headless capability arrived fast and each addition was invisible:
`scrapling_api_capture`, `scrapling_fetch_and_extract`, `app_task("web.read")`,
`read_page_text`, `workspace_download`. Measured before this decision: **4 of 51** orb
tools produced any bunny motion, and only 7 emission sites existed in 4 modules — two of
them in `headed_browser.py`, which the orb can no longer reach. A six-call research task
(page_probe → scrapling_api_capture → workspace_download) showed the bunny sitting still
for twenty seconds while the answer appeared in text.

The feed itself was also broken in three ways, each of which alone was enough to freeze it:

- `point()` wrote `{x,y}`; `scrolling()`/`typing()`/`fail()` **replaced** the file with a
  payload carrying no coordinates, so the position was erased;
- the orb polls every 80 ms, and a `point()` overwritten inside that window was never read;
- Rust `continue`d after handling a gesture, so the `x`/`y` branch was unreachable for any
  payload that also carried a gesture.

## Decision

1. **Every orb tool declares a visual gesture**, in one table
   (`tools/visual_contract.py`). A tool without an entry fails `test_visual_contract`, so
   silence is a build error rather than a habit.
2. **Headless work is anchored to where its facts came from.** `scrapling_api_capture`
   does its work over HTTP, but the bunny flies to the *browser window* and reads there:
   the user learns provenance, not just motion. Tools with nothing on screen
   (`system_status`, `remember`) think at the orb rather than pretending to look elsewhere.
3. **A gesture never erases the anchor.** Every payload carries `x,y`; gesture keys do not
   persist, so a stale `click:true` can never re-fire.
4. **One payload per call.** Emitting `point()` then a gesture raced the 80 ms poll. The
   anchor travels with the gesture, and Rust flies from that single event.
5. **A failure is shown where it happened** (`fail` at the same anchor) — never a success
   flourish on a lie.
6. **No new art per gesture.** `read`, `work`, `carry` and `think` are an existing sprite
   pose plus a drawn ornament (magnifier + scan sweep, progress arc, rising file chip,
   three dots). This keeps the vocabulary extensible without an art dependency.

## Consequences

- `type_text` flies to the window first. It previously typed "at the parked cursor", so
  text appeared while the bunny looked idle — the exact inversion of the product.
- Repeat gestures at the same anchor carry `nofly` and skip the 0.75 s flight; otherwise a
  0.2 s read was followed by an animation that arrived after the fact.
- A gesture costs one extra tool call per action in the overlay, and ~1.2 s of on-screen
  motion for a read. That is the product, not overhead.
- The `think` gesture is deliberately static: no teleport, because "what time is it" did
  not happen at a window.

## Rejected alternatives

- **New sprite frames for each gesture.** Correct eventually, but it makes visibility
  depend on an art queue; ornaments ship today and can be replaced frame by frame later.
- **Making every tool headless-but-logged instead of animated.** That is the transcript
  model this product exists to reject.
- **A second event stream for "visual only" tools.** One feed and one contract is why the
  coverage test can be exhaustive.
