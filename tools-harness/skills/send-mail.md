# Sending mail

When the user asks you to write and send an email — not to read one.

1. Reading is a lookup: `get_latest_email`, `list_emails`, `read_email`. Do not
   open Gmail in a tab to read mail.
2. Sending happens on screen, so the user can watch: `app_task("gmail.send",
   {to, subject, body})`. You write the subject and the body (body under ~1500
   characters); the recipe clicks Send itself and confirms with a read-only check.
3. Before it runs, check three things against the ask: recipient, subject, body.
   A wrong recipient is not a typo you can fix after the fact.
4. `sent:false` does not mean "not sent". Check the inbox before doing anything
   else. Never send the same mail twice, and never fall back to hand-clicking
   Gmail's compose window — the AI compose box ("Describe your message") is not
   the body, and the body is "Message Body".
5. Told to send something? That is your authorisation — do it, then confirm what
   you sent and to whom. Do not ask again.
6. Anything else that leaves the machine (posting, submitting, deleting, paying)
   follows the same shape: the user's explicit ask, a check of the specifics, then
   verification afterwards.
