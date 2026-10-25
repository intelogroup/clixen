# Form filling

When the page has a form with more than two fields and the user wants it filled.

1. `observe` once. Read the numbered elements; note the *role* (TextField, CheckBox,
   PopUpButton) and any current value.
2. Call `act(steps=[{target, action, text}, …])` — one call for the whole form,
   in visual order. It stops at the first failure and returns per-step results.
   Do not call `act` once per field; every round costs the user time.
3. A field that already has text needs `replace=true`. Typing into a non-empty
   field without it appends, and the old value stays in front.
4. After the last step, prove it: `observe` (or `act(expect={"appears": "…"})` for a
   confirmation message). Never say "filled" because the calls returned ok — say it
   when the field values read back.
5. If a step fails: `not_found` → `observe` again (the page may have re-rendered);
   `not_editable` → target the field itself, not its label; `ambiguous` → pick an
   element id from the candidates, never a label guess.
6. Submit only if the user asked you to submit. Filling is reversible; submitting
   is not.
