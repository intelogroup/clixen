# Browse a page like a human, then verify

For multi-step navigation: scroll to an item, fill a form, type a comment.

1. LOOK→ACT→VERIFY: one action, then a fresh look (`observe`, or
`take_screenshot` for canvas, maps and icon-only buttons). Never act on ids
from a stale screen — after any click or navigation, observe again. A ref
that errors as not found means re-snapshot, never blind retry.
2. FIND FIRST: to locate text or a link, call `find_on_page(text)` — it
scrolls to the bottom itself. Only its `found:false` means absent. Then act
by the returned id.
3. LOGIN WALL: a "Login to comment" button, a signup modal, a redirect to
/login → STOP and report blocked. Never log in, never create an account,
never submit/send/post unless the user explicitly said send. A draft left
unsubmitted is a correct stop, not a failure.
4. VERIFY: confirm the url/window after every navigation before the next
step; `mark_done` only when the screen shows it done.
5. WEB TEXT: to find a product, link or phrase on a web page use
`read_page_text(query)`, not `find_on_page` — the screen tree holds only the
visible controls and tab titles, so `find_on_page` can say "bottom reached" with
the item still on the page.
6. NEAR MATCH: when the exact item is absent but a related result mentions it
(an accessory, a different model), open the closest one, read it, and answer with
what it is. Never end at "not found" while a related listing is on screen.
7. STRAY MODAL: if a dialog you did not mean to open is on screen after a click
(location picker, cookie or sign-in box), close it with its X or Cancel and
`observe` before the next step. Never leave one open, and never pick an address,
account or option inside it.
8. NO HIT: a search with no match is not an answer yet. Retry the search box with
spelling variants (split or join words: "Heropup" -> "Hero Pup"; drop a word) and
read the results again; say "not found" only after at least two variants.
9. VALUE NOT IN TEXT: a price, count or date missing from `read_page_text` is not
"cannot verify" yet. Read it from the screen: `observe` with visual:true or
`analyze_image` on the front window, and check the result tile that led to the
page (its label often carries the price). Say you could not verify only after both.
