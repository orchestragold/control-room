# Pitch Machine — Full Scoping Doc (consolidated)

*Pulls together every Pitch Machine decision made across the Control Room planning sessions plus the August 9, 2026 "booking agent" brainstorm, into one place. Supersedes the scattered Pitch Machine references in `Control_Room_Architecture_Spec.md` where they conflict — this document is the current source of truth for Pitch Machine specifically. The full architecture spec remains the source of truth for everything else in Control Room (shell, roles, other subprojects, the Posting Tool, hosting).*

## What Pitch Machine actually is

Not a mail-merge tool. It's meant to function as Orchestra Gold's booking agent — informed by ongoing research, a growing shared knowledge base, and a real feedback loop between Erich and Claude, not a fire-and-forget automation. The mechanical pipeline (research → draft → approve → send → follow up) is the scaffolding; the actual value is in the judgment layered on top of it, which is expected to keep improving over time rather than being "finished" at launch.

**Existing reality, not a clean-slate build.** A real version of this already runs today, documented in `CoWork/Festival Outreach/PITCH_MACHINE_RULES.md` (the live shared-rules file across four scheduled tasks and any chat session). Pitch Machine inside Control Room is a UI and automation layer built *on top of* that existing system, not a replacement starting from zero — it inherits the existing HubSpot data, the existing Zoho integration, and the existing house style/research playbook.

## Data model and systems of record

- **HubSpot** stays the CRM system of record. Festival records are COMPANY objects. **HubSpot is at its custom-property cap (10/10) — no new properties can be added** without freeing one up first. Any new signal Control Room wants to track (e.g. a warm/cold flag) needs to either reuse an existing property, live in Control Room's own database instead, or require deliberately retiring an existing property.
- **Email is Zoho Mail, not Gmail.** The existing `pitch-queue-reminder` scheduled task already has a working Zoho Mail OAuth connection: it checks for replies and sent messages daily, auto-stamps HubSpot touch dates once Erich actually sends something, and creates Touch 2/3 drafts in his Zoho Drafts folder pulling his real signature/font from his last sent message. **Security note carried over: the existing task's prompt currently holds a live Zoho OAuth client_secret and refresh_token in plaintext** — flagged as sensitive, not yet remediated. This needs to move into Control Room's proper secrets handling (environment variables via cPanel's UI, per the hosting section of the main spec) as part of this build, not left as-is.
- **Progress tracking today is date properties, not a deal pipeline** — `reach_out_1` (Touch 1 planned send date), `reach_out_2_checkin` (Touch 2 check-in date, +14 days), `reach_out_2` (Touch 3 close-out date — internal name doesn't match its HubSpot label "Reach Out #3 (Close-out)," a known mismatch, not a bug). Note: an earlier version of this doc listed the Touch 2 property as `reach_out_checkin` — that name is wrong; the real HubSpot property name is `reach_out_2_checkin`. These fields contain DATE strings (e.g. "2026-11-02"), not timestamps — future date = scheduled, past date = outreach was due on that day. Control Room's kanban-style stage view (below) is a UI reinterpretation of this underlying date-based tracking, not a wholesale replacement of it.
- **Legacy data protection, must be respected:** 107 Company records had `reach_out_1`/`reach_out_2` pre-populated from an old, unrelated planning calendar. The existing automation — and anything Control Room builds on top — only acts on companies where `reach_out_1` was empty at first discovery. Never overwrite these.
- **The outreach spreadsheet** (`Orchestra_GOLD_Festival_Outreach.xlsx`) is the working list for research/triage, separate from HubSpot, and can drift out of sync with it — any batch research or drafting work should check HubSpot first per the existing pre-research check rule (skip/deprioritize anything HubSpot already shows as pitched, sync buyer info back to the spreadsheet if HubSpot has it and the spreadsheet doesn't).
- **Two Dropbox subfolders, not one:** `CoWork/Festival Outreach/` (research side — spreadsheet, rules file, research/triage scheduled tasks) and `CoWork/Pitch Machine/` (pitching side — `pitch_machine_state.json`, daily follow-up summaries). The latter reportedly never got created due to a cron mismatch as of the last rules-file update — worth verifying whether it exists yet before assuming it does.

## Knowledge base and the sync mechanism

Pitch Machine's drafting intelligence needs to draw on a continually-growing knowledge base, not a fixed prompt. Three Dropbox sources feed it: `CoWork/Festival Outreach/` (rules, research playbook, spreadsheet), `CoWork/Pitch Machine/` (state, follow-up summaries), and the email-automations folder (drafted email series, origin story, personal band history — richer narrative material for pitch drafting).

**Mechanism:** the GoDaddy-hosted app can't see the Mac's synced Dropbox folder directly, so it reads through the Dropbox API — reusing the same Dropbox integration already planned for the Posting Tool's media hosting, rather than building a second one. Content gets pulled into Control Room's own database as a queryable knowledge base on a schedule, rather than re-parsing markdown files at draft-time (slower, more fragile).

**Write-back, closing the loop:** when Erich edits a draft inside Control Room, the edit gets logged into Control Room's own database *and* appended as a short entry to `PITCH_MACHINE_RULES.md`'s "Recent decisions log" via the same Dropbox API. That file already declares itself the shared source of truth across every thread (scheduled tasks and live chats) — piping Control Room's own learning into it keeps a live Claude Code chat session and the Pitch Machine app reading from the same brain, instead of two that quietly drift apart over time.

## House style and pitching philosophy (carried over from the existing rules file — do not re-derive)

- No hyphens of any kind (em, en, or regular) anywhere in Orchestra Gold writing.
- No exclamation points in fan-facing copy. Lowercase by default; switch to sentence case for more institutional/traditional-arts recipients — judgment call, confirm with Erich if unsure.
- Album titles always all caps (DAKAN, MEDICINE, LIMANIYA). "Orchestra GOLD" or "OG" in prose.
- "Malian folklore," not "Malian soul." Mariam writes lyrics exclusively in Bambara.
- Esoteric symbols welcome (∴ ✺ ☍ ☉ 𓂀 ⦿); ∴ is the standard sign-off. Use plain Unicode symbols for link-icon bullets (✱ KEXP, ∞ Live, ⊙ Instagram) — image-style emoji render broken in Gmail's compose view and must not be used.
- Industry outreach closings: "sending my blessings." Erich's own edits to any draft override this style guide — never silently normalize his intentional choices.
- Relationship-first pitching: soft intro first, formal asks later, no hard asks in early outreach.
- Match template to festival type: psych/stoner/heavy-rock scenes get the punchier "Sabbath went to Mali" template; folk/heritage/world-music festivals need the belonging/lineage framing instead; aspirational/stretch-tier targets get a soft no-ask first touch.
- Read `2026-07 psych rock pitches - when sabbath went to Mali for four minutes.docx` (an archive of ~25 of Erich's real past pitch drafts) before drafting anything new — reuse his actual recurring sentence blocks rather than reconstructing from memory.
- Before drafting any Touch 1, deliver a research brief covering: talent buyer (name/title/how confirmed), festival vibe/history, attendance, ticket pricing tier, notable sponsors, any stated pitching preferences, lineup/comp-artist cross-references with real fit reasoning (not just genre tagging), and the buyer's/festival's stated values. Note gaps explicitly rather than skipping bullets silently.

## The booking-agent reframe (Session 12, Aug 9 2026)

The core shift: Pitch Machine's job is to move the needle on bookings *and* elevate the band's brand over the long term, in a co-creative, continually-learning partnership with Erich — not just execute a fixed mechanical process. What that means concretely:

**Outcome tracking and weighting.** Every pitch gets tagged with what was used (template, subject-line style, tone/variant) and what happened (opened if trackable, responded, positive/negative, eventually confirmed) — the substrate for weighting successful patterns more heavily over time. **Erich has delegated full ownership of designing and running this to Claude** — build it, run it, report back, rather than something he manages.

**A/B testing as a first-class habit.** Every pitch tagged with its variant so patterns become visible over time, including a send-time-testing scheme (does day/time of send correlate with response). Full autonomy given to Claude on how to structure this.

**The "secret sauce" fit doc.** A new, deliberately fuzzy knowledge asset capturing aesthetic fit beyond genre/comp-artist tagging — doesn't exist in any form yet. **Built collaboratively and periodically**, not as a one-time task: Claude asks Erich open-ended questions about what "fit" actually feels like, Erich feeds in the real list of festivals that have already booked OG, and the doc grows out of that conversation over time. No fixed format decided yet — the first session on it should probably also decide its own shape.

**Fit-confidence scoring**, surfaced per card before a target is even selected, to naturally pull attention toward best-odds targets. Erich confirmed this is valuable but explicitly a moving target, hard to quantify precisely — an ongoing intention to refine together, not something that needs a finished formula before shipping.

**Warm-intro mapping.** Leveraging comp artists' agents/managers for introductions — a separate, complementary initiative to Maeve's existing warm-contact lane (not a replacement for it). Erich confirmed this is worth pursuing, potentially with Maeve involved, but it has no build scoping yet — needs its own session before it's buildable.

**Deprioritization tier, confirmed.** Targets that go silent through 2 full outreach cycles move to a deprioritized/dead status distinct from "Declined" (which implies an explicit no) rather than being retried indefinitely — matches Erich's own philosophy of not knocking on doors that won't budge.

**Tour-routing-aware batching, confirmed as standard practice going forward.** Prioritizing/batching upcoming pitches by proximity to confirmed tour dates — Erich noted this is literally what a prior PNW research session was already doing intuitively, and it should become a repeatable step in the workflow for every tour, not something re-invented each time.

## Ownership and workflow

- **Cold outreach (new leads with no prior business history) is Erich's**, drafted, reviewed, and sent by him. **Warm contacts (prior business/booking history) are Maeve's**, from intake. Any contact transfers to Maeve once it reaches Confirmed, for advance/logistics, regardless of starting owner.
- HubSpot needs a reliable warm/cold signal per contact/organization to drive this routing — given the 10/10 property cap, this likely can't be a new HubSpot property and needs another mechanism (Control Room's own database, or reusing an existing property).

## Follow-up cadence — locked

Three different values had circulated across different conversations (7/11/22 days, 14/14 days, "10-12 days"). **Decision: 14 days / 14 days** (Touch 2 at day 14 after Touch 1, Touch 3 at day 28) — matches what's already documented as actually running in `PITCH_MACHINE_RULES.md` today, the lowest-disruption choice. Erich explicitly delegated this decision with the instruction to pick something, try it, and adjust if it's not working — treat this as a starting point, not permanent.

Stop the sequence immediately on any reply, at any stage — unchanged from the existing rule.

## Autonomy — where the human-in-the-loop line sits, and why

**Touch 1 (the initial cold pitch) requires Erich's review and explicit send approval indefinitely, for now.** This was a deliberate, reasoned decision, not just caution for its own sake: cold outreach is Pitch Machine's highest-judgment surface, the failure mode is asymmetric (a bad first impression with a festival buyer can quietly close doors elsewhere in a small, connected industry, in ways that never surface as a visible "failure"), the secret-sauce fit doc doesn't have real texture yet, and there's no track record of outcome data yet to validate that any weighting/pattern-matching is actually working. None of that is a permanent state — it's a "not yet" contingent on real data and a longer working history together, to be revisited periodically rather than decided once and forgotten.

**Touches 2 and 3 (the follow-ups) are the designated first target for real autonomy**, and this is genuinely feasible with minimal new infrastructure: they're far more formulaic ("just checking this is on your radar") and much lower-judgment than a first pitch, and a working Zoho OAuth connection, daily reply-checking, and auto-draft generation already exist today per `PITCH_MACHINE_RULES.md`. The delta to true autonomy is small: instead of leaving a generated Touch 2/3 draft sitting in Zoho's Drafts folder for Erich to manually send, a scheduled job calls Zoho's send API directly once the trigger condition is met (14 days elapsed with no reply → send Touch 2; 28 days elapsed with still no reply → send Touch 3), and the existing daily reply-check gets extended to cancel any pending scheduled send immediately upon any reply, rather than just flagging it.

**Non-negotiable safety gate before this goes live:** autosent Touch 2/3 must run through the same global test-mode/live-mode switch already built into Control Room's foundations (Session A). In test mode, these auto-sends redirect to Erich's own inbox first, so a batch of real-shaped autonomous sends can be observed safely before any of them reach an actual festival contact. Do not skip this step given how sensitive first-impression outreach is known to be.

## UI flow

1. **Login → Orchestra Gold → Pitch Machine.** Landing view shows the next 10-20 pitches to be made as small cards (name, location, comp-artist cross-references, a short description pulled from the spreadsheet) — Trello-board-like visual density, using Pitch Machine's own defined stages (not the generic Warm/Hot/Cold labels from the Trello template used as an early UI reference).
2. **"Do some pitches" → a checklist/table view** of the next 10-15 candidates (festival, location, comps, short description). Erich checks the ones he wants to work this session (check-all / check-none supported).
3. **"Get pitches ready" → an async batch job**, not a synchronous wait. Claude researches each selected festival (buyer confirmation, values, fit signals, the full research-brief checklist above) and drafts a pitch for each. A status indicator shows progress (expect ~3-4 minutes for a batch); Erich can step away during this.
4. **Issue flags, if any.** If Claude hits a genuine blocker (can't confirm a buyer, missing information, an ambiguous signal), it's flagged rather than guessed through. **These are resolved via chat, not inside Control Room's UI** — Control Room surfaces the flag and prompts Erich to go resolve it in a Claude Code/chat session, then re-run; no in-app resolution UI needed for v1.
5. **Per-contact compose view**, styled like Gmail's floating compose window but dark-themed to match Control Room, one contact at a time, advancing automatically after each is handled:
   - **Touch 1** is the full editor: subject line pre-filled, `booking@orchestragold.com` CC'd by default, target address(es) pre-filled from research (one or two, depending on what research found), draft body pre-filled with Erich's real signature, large editable text (14-16pt font, not small). Erich can edit freely; a single "good to go" button schedules the send for the researched date and advances to the next contact.
   - **Touch 2 and Touch 3 sit as collapsed preview cards**, not open editors, alongside Touch 1 — they only expand into a full edit view when their actual trigger date arrives, since their content could go stale by the time they're due to send. Confirmed as the right approach specifically to avoid editing drafts today that need re-editing again in two weeks.
6. **Edits get learned from.** Any change Erich makes to a draft gets saved back into the knowledge base (per the sync mechanism above) so future drafts can reflect what he actually changes, not just what the system originally generated.
7. **Reply at any stage kills all pending scheduled touches for that contact** — unchanged from the existing rule, now enforced automatically rather than manually tracked.

## Verification and research playbook (carried over — do not re-derive, follow this exact order of operations)

1. Google/web search first — direct search, then press/interviews with the festival's Executive/Creative Director (often the actual buyer even when their title doesn't say so).
2. Identify the underlying production/promotion company — festivals are frequently run by a separate company; the real named buyer often sits there, not on the festival's own site.
3. LinkedIn search — good for identity/role confirmation, rarely yields an email directly.
4. The production company's own website (staff/team/about page) if LinkedIn doesn't surface a name.
5. Pattern-guess an email from a known same-domain address once a name + org are known, then search for the guessed address directly (this often surfaces independent confirmation). Pattern guesses are not verified emails — log them clearly as guesses.
6. **Verify via Verifalia first, Hunter.io last resort.** Always test 3-5 permutations per person through the same verifier, not just one — a single "deliverable" result doesn't rule out a catch-all domain, but a mixed result (some permutations deliverable, others not) is real differentiating signal that the domain does genuine per-mailbox verification. Verifalia's logged-in account has 25 free daily credits; an anonymous single-check tool at verifalia.com/validate-email is a fallback with a separate quota (has its own occasional CAPTCHA gate that needs a human to clear).
7. Hunter.io only as a true last resort, only to verify an email for a person already identified — never to discover who the buyer is in the first place.

Other useful angles: social media bios/DMs as a fallback channel, conference speaker bios for identity confirmation (rarely an email), a `filetype:pdf` search on the person's name (low-yield but occasionally useful), and watching for stage-name-based personas.

## What's confirmed vs. still open

**Confirmed and locked:** the knowledge-sync architecture (Dropbox API → Control Room DB, with write-back into `PITCH_MACHINE_RULES.md`), outcome tracking and A/B testing under Claude's full ownership, the deprioritization tier after 2 silent cycles, tour-routing-aware batching as standard practice, the 14/14-day follow-up cadence, the Touch 1-human/Touch 2-3-autonomous split (gated behind test-mode verification), the collapsed-by-default three-touch window UI, and the full UI flow described above.

**Still open, deliberately deferred:**
- Warm-intro mapping has no build scoping yet — needs its own session.
- The secret-sauce fit doc has no defined format — first collaborative session on it should decide its own shape.
- Fit-confidence scoring has no formula — intentional, ongoing refinement rather than a spec-now item.
- Whether `CoWork/Pitch Machine/` (the second Dropbox subfolder) actually exists yet needs verification before assuming its structure.
- The plaintext Zoho credential in the existing scheduled task's prompt needs remediation as part of this build, not left as-is.
