# Kickoff: Finish renaming Control Room → The Portal

All text-level renaming is done (docs and in-app display strings). What's left needs live server/dashboard access, so it's yours to run.

## Already done (don't redo)
- `docs/Control_Room_Architecture_Spec.md`, `Control_Room_Build_Sessions.md`, `Pitch_Machine_Scoping.md` — all "Control Room" → "The Portal"
- `app/templates/base.html` (title, wordmark, mobile wordmark), `pitch_machine/board.html`, `main/dashboard.html`, `main/placeholder.html`, `auth/login.html` (title + h1), `app/auth/routes.py` (flash message), `migrations/schema.sql` (header comment), `.env.example` (comment)

## Still to do (infra — needs SSH / cPanel / Google Cloud)

1. **New subdomain**: provision `portal.orchestragold.com` in cPanel (parallel to however `control.orchestragold.com` was set up).
2. **AutoSSL**: issue a cert for the new subdomain via cPanel → Security → SSL/TLS Status (same auto flow used for the original domain — no manual cert install needed).
3. **cPanel Python App**: either repoint the existing Setup Python App entry to the new domain, or create a new app entry pointing at the same `passenger_wsgi.py` / virtualenv, then retire the old one once verified.
4. **Google OAuth redirect URI**: in Google Cloud Console, add `https://portal.orchestragold.com/auth/callback` (or whatever the exact callback path is) to the OAuth client's authorized redirect URIs. Leave the old one in place until cutover is confirmed working, then remove it.
5. **MySQL database name**: currently `controlroom`. Decide: rename in place (`RENAME TABLE` trick or dump/restore into a new `theportal` DB) vs. just leave the DB name as-is since it's an internal identifier nobody sees. Recommend leaving it as `controlroom` — not worth the migration risk for a name only you and the code will ever see.
6. **Verify env vars**: confirm cPanel's Python App environment variables (`GOOGLE_REDIRECT_URI`, any hardcoded domain refs) are updated to match the new subdomain.
7. **Cutover**: once `portal.orchestragold.com` is fully live and tested, decide whether to 301-redirect the old `control.` subdomain or just let it go stale.

## Why split this way
Text-level renaming (docs, page titles, UI strings) had zero infra dependency, so it was faster to just do directly rather than round-trip through a session. The remaining items all require SSH or dashboard clicks Claude Code already has an established workflow for (same pattern as the original SSH/AutoSSL setup).
