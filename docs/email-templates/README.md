# Auth email templates — paste into the Supabase dashboard

These make the auth emails carry a **6-digit code** (`{{ .Token }}`) alongside
the existing link, in Rudy's voice, spam-safe plain formatting. The app's code
panels (`#auth-code`) already consume the codes; until these templates are
applied, the default Supabase emails still work via their links (fallback).

**Where:** Dashboard → project `tcclohxvhmwgjrtdkkuw` → Authentication →
Email Templates. Paste each file's subject + body into the matching template.
(The dashboard is firewall-blocked from CNC-1 — use another device, or the
Management API with the off-network access token per docs/LOCAL-SECRETS.md.)

| Dashboard template | File | Used by |
|---|---|---|
| Magic Link | `magic-link.html` | "Email me a 6-digit code" + "Email me a sign-in link" |
| Reset Password | `recovery.html` | "Email me a reset code" |

**Sender ("from") note:** the from-address stays `noreply@mail.app.supabase.io`
until custom SMTP is configured (needs a domain you own + Resend — see
agent/RUNBOOK.md operator item 4). Free-TLD domains are spam-scored and would
make deliverability WORSE than the default; don't use them. Subject and body
are fully brandable regardless — that's what these templates do.

**After pasting:** send yourself a code from the app and confirm the 6 digits
render. On the iPad, the code field autofills straight from Mail
(`autocomplete="one-time-code"`).
