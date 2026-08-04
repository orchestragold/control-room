# Control Room — Build Session Roadmap

*Companion to Control_Room_Architecture_Spec.md. That document defines what Control Room is; this one partitions it into a sequence of build sessions, each sized to produce one working, usable increment rather than one giant build. Sessions are ordered by dependency, not strictly by priority — some later items (Distribution, Scheduling) matter just as much as early ones, they just need the foundation in place first.*

## Before Session A: two things to lock first

Everything below assumes these are settled, since they touch almost every session:

1. **Login mechanism for Control Room itself.** The domain (`control.orchestragold.com`) and roles model are locked, but not *how* someone signs in. Given the team is small (Erich, Maeve, eventually band members) and Google Calendar is already a core dependency, **recommend Google Sign-In (OAuth)** as the default — no password to manage, no email-sending-for-magic-links infrastructure to build, and it's low-friction for band members later. Flag if you'd rather do email/password instead.
2. **Confirm cPanel has a Cron Jobs tool on the GoDaddy plan.** Standard on most cPanel accounts but not yet explicitly checked in the walkthrough — needed from Session A onward for any background/scheduled task (inbox polling, follow-up timing, digest notifications). Quick check: cPanel → Files or Advanced section → "Cron Jobs."

---

## Session A — Foundations (no visible UI yet, but everything depends on it)

- Set up the Python app on GoDaddy via cPanel's "Setup Python App," with Git-based deploy over SSH.
- `.env`-based secrets scaffolding (HubSpot, Asana, Google Calendar, MailerLite, Dropbox keys — placeholders are fine until each integration lands).
- Control Room's own database: schema for holds, notes, approval logs, user accounts/roles, notification preferences. This is the only genuinely new data store — everything else (HubSpot, Asana, Google Calendar) stays a system of record Control Room reads/writes into, not replaces.
- Shared throttle/queue layer for outbound API calls (built once, configured per platform as each integration is added).
- Google Sign-In auth wired up against `control.orchestragold.com`.
- Test-mode/live-mode switch as a global setting (redirects outbound sends per integration — built here as infrastructure, used starting in Session D).

**Deliverable:** an empty but real, deployed, login-protected app at `control.orchestragold.com`. Nothing to look at yet, but everything after this builds on real infrastructure instead of a prototype.

## Session B — Shell & landing view

- Left sidebar: Projects (collapsible, Orchestra Gold → subprojects nested) and Tools as a peer section below.
- Roles enforcement: super admin (Erich), editor (Maeve) scoped per subproject — even though only Pitch Machine has real permission boundaries to enforce yet.
- Landing view: "what needs attention today" + upcoming — built against empty/mock data for now, wired to real sources as later sessions add them.
- Mobile-responsive pass on the shell itself, since this is the one screen everyone sees every time.

**Deliverable:** the actual navigable shell of Control Room, usable on a phone, with nothing behind most of the doors yet except Pitch Machine (next).

## Session C — Pitch Machine, phase 1: visibility

- HubSpot read integration: pull contacts/deals into a kanban board over the 8 defined stages.
- Manual card moves (drag between stages) — no automation yet, just making the existing HubSpot pipeline visible and usable in the new UI.
- This alone is useful immediately: a real, working board Erich can start looking at before any automation is built.

**Deliverable:** a live Pitch Machine board reflecting real HubSpot data, editable manually.

## Session D — Pitch Machine, phase 2: draft, approve one-by-one, test-send

- Pitch drafting (reuse whatever's already working in the existing Claude Code scheduled-task setup).
- Review/approval UI — **built for single-item approval first**, per the phased rollout decision, with the batch/bulk UI added later in the same component rather than as a rebuild.
- Test-mode sending: every approved pitch redirects to `orchestragold@gmail.com` instead of the real contact, using the switch built in Session A.
- Approval audit log: what was approved, by whom, exact text sent.

**Deliverable:** Erich can draft-review-approve-send a real pitch end to end, safely, with nothing reaching a real festival yet.

## Session E — Pitch Machine, phase 3: automation goes live

- Inbox reply classification (positive → In negotiation, decline → Declined, ambiguous → parked for Erich, per the confident-only rule).
- 7/11/22-day follow-up cadence, automated.
- Permanent opt-out enforcement (hard gate, not a convention) checked before every send and recycle.
- Flip from full test-mode to the intermediate whitelist step (a few trusted real contacts) before going fully live.

**Deliverable:** the real automation loop, proven safe first against redirected test sends, then against a small trusted real sample before opening up fully.

## Session F — Pitch Machine, phase 4: warm/cold routing + bulk

- One-time import of Erich's historical gigs spreadsheet to tag existing HubSpot contacts/organizations warm/cold.
- Ownership routing (warm → Maeve from intake, cold → Erich, Confirmed → Maeve regardless).
- Graduate the approval UI to small batches, then full 5–7 bulk approval.
- Simple capacity/pace view for the November–December pitch-sprint planning.

**Deliverable:** Pitch Machine fully matches the spec — this is the point where it's "done" for v1, not just usable.

## Session G — Distribution

- Confirm the 539/426 HubSpot contact/company structure question first (quick check, not a build task).
- One-time spreadsheet-to-HubSpot import for record stores.
- Reuse Pitch Machine's stage-machine component as-is, new stage labels and contact list, same automation logic.

**Deliverable:** Distribution live, proving the pipeline component actually generalizes.

## Session H — Scheduling

- Google Calendar wrap (read/write against the real calendar).
- Hold-date objects with note threads, visible to whoever's attached.
- Opt-in notification preferences (instant/daily/weekly/biweekly digest).

**Deliverable:** hold-date tracking and rehearsal-availability collection live, replacing the manual email-people-individually pattern.

## Session I — Advance

- Confirmed-show info hub, auto-populated when a Pitch Machine card hits Confirmed.
- Shareable "list of confirmed gigs/rehearsals" view for band members.

**Deliverable:** the advance packet lives in the dashboard instead of scattered docs.

## Session J — Tasks (Asana sync)

- Two-way sync: Asana tasks appear in Control Room, Control Room tasks push to Asana.
- Feeds the landing view's "what's due today" section with real data.

## Session K — Posting Tool, phase 1: foundation + Meta

- Project/brand ↔ account mapping layer (the main net-new architecture beyond the existing OG Publisher scoping).
- Content-package format matching ContentStudio's confirmed shape (common content + per-channel overrides + account list + schedule).
- Dropbox media hosting wired in.
- Meta publishing (Instagram + Facebook Pages), test mode posting to a private/test account first.

## Session L — Posting Tool, phase 2: YouTube + Vault + podcast

- YouTube direct publishing.
- Thinkific and WordPress (MemberPress/WooCommerce) as the two "course content" destinations.
- Podcast episode + script scheduling folded in as another channel type.

## Session M — Posting Tool, phase 3: Bandcamp + multi-project

- Bandcamp semi-automated flow via Claude in Chrome.
- Onboard Calm Quiet Knowing and Mercury Red as additional brands with their own voice/style settings and account mappings.
- ContentStudio sunset once channel coverage matches.

---

## Notes on sequencing

Sessions C–F (Pitch Machine) are the priority chain — it's the most fully specified subproject and the one with the clearest immediate payoff. Distribution (G) is cheap once F is done, since it reuses the same component. Scheduling/Advance/Tasks (H–J) can happen in parallel with the Posting Tool track (K–M) once Pitch Machine is stable, since they don't depend on each other. The Posting Tool track can also start earlier than this ordering suggests if there's more urgency there (e.g. the Meta permission review clock) — it's sequenced after Pitch Machine here mainly because Pitch Machine already has more resolved detail to build against.

This roadmap, like the architecture spec, is expected to change — a session may split further once it's actually being built, or reveal a new decision that needs folding back into the spec. Treat each session heading as a scoping starting point, not a fixed contract.
