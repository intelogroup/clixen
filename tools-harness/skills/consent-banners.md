# Consent banners and popups

When a dialog is in the way: a cookie/consent banner, a Chrome account bubble, a
newsletter modal, a "before you continue" wall.

1. `observe` first. It reports them in `overlays` — either `popover: <title>` (a
   browser-level bubble) or `consent banner (…)` (inside the page). Read that field
   before clicking anything; a banner covers the thing you were about to click.
2. A browser bubble (e.g. "Continue to example.com with google.com") has its own
   buttons in the element list, usually `Close` / `Not now`. Dismiss it that way, not
   with `click_text`.
3. A consent banner often lives in an iframe, so its Accept/Reject can be missing
   from the element list even though the text is on screen. The overlay message says
   so; then `read_page_text` or `click_text` by the visible label.
4. **Never choose accept, reject or "do not sell" on the user's behalf.** Those are
   privacy elections about their data. If the banner offers them, dismiss it neutrally
   (a Close/Closer/manage) or ask the user which they want. Say plainly which choice
   was made — "dismissed, no preference set" is honest; "accepted cookies" is not,
   unless you actually clicked accept.
5. After dismissing, `observe` again and read `effect`: the banner's labels should
   appear under `gone` and the page content under `appeared`. That is the proof the
   dialog is really closed — not the fact that your click returned ok.
6. Popups can reappear on navigation (many news sites gate per-section). One
   dismissal per page is normal; a third is a signal to stop and tell the user.
