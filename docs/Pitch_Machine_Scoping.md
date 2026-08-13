# Pitch Machine — Full Scoping Doc (consolidated)

*Pulls together every Pitch Machine decision made across the The Portal planning sessions plus the August 9, 2026 "booking agent" brainstorm, into one place. Supersedes the scattered Pitch Machine references in `Control_Room_Architecture_Spec.md` where they conflict — this document is the current source of truth for Pitch Machine specifically. The full architecture spec remains the source of truth for everything else in The Portal (shell, roles, other subprojects, the Posting Tool, hosting).*

## What Pitch Machine actually is

Not a mail-merge tool. It's meant to function as Orchestra Gold's booking agent — informed by ongoing research, a growing shared knowledge base, and a real feedback loop between Erich and Claude, not a fire-and-forget automation. The mechanical pipeline (research → draft → approve → send → follow up) is the scaffolding; the actual value is in the judgment layered on top of it, which is expected to keep improving over time rather than being "finished" at launch.

**Existing reality, not a clean-slate build.** A real version of this already runs today, documented in `CoWork/Festival Outreach/PITCH_MACHINE_RULES.md` (the live shared-rules file across four scheduled tasks and any chat session). Pitch Machine inside The Portal is a UI and automation layer built *on top of* that existing system, not a replacement starting from zero — it inherits the existing HubSpot data, the existing Zoho integration, and the existing house style/research playbook.

## Data model and systems of record

- **HubSpot** stays the CRM system of record. Festival records are COMPANY objects. **HubSpot is at its custom-property cap (10/10) — no new properties can be added** without freeing one up first. Any new signal The Portal wants to track (e.g. a warm/cold flag) needs to either reuse an existing property, live in The Portal's own database instead, or require deliberately retiring an existing property.
- **Email is Zoho Mail, not Gmail.** The existing `pitch-queue-reminder` scheduled task already has a working Zoho Mail OAuth connection: it checks for replies and sent messages daily, auto-stamps HubSpot touch dates once Erich actually sends something, and creates Touch 2/3 drafts in his Zoho Drafts folder pulling his real signature/font from his last sent message. **Security note carried over: the existing task's prompt currently holds a live Zoho OAuth client_secret and refresh_token in plaintext** — flagged as sensitive, not yet remediated. This needs to move into The Portal's proper secrets handling (environment variables via cPanel's UI, per the hosting section of the main spec) as part of this build, not left as-is.
- **Progress tracking today is date properties, not a deal pipeline** — `reach_out_1` (Touch 1 planned send date), `reach_out_2_checkin` (Touch 2 check-in date, +14 days), `reach_out_2` (Touch 3 close-out date — internal name doesn't match its HubSpot label "Reach Out #3 (Close-out)," a known mismatch, not a bug). Note: an earlier version of this doc listed the Touch 2 property as `reach_out_checkin` — that name is wrong; the real HubSpot property name is `reach_out_2_checkin`. These fields contain DATE strings (e.g. "2026-11-02"), not timestamps — future date = scheduled, past date = outreach was due on that day. The Portal's kanban-style stage view (below) is a UI reinterpretation of this underlying date-based tracking, not a wholesale replacement of it.
- **Legacy data protection, must be respected:** 107 Company records had `reach_out_1`/`reach_out_2` pre-populated from an old, unrelated planning calendar. The existing automation — and anything The Portal builds on top — only acts on companies where `reach_out_1` was empty at first discovery. Never overwrite these.
- **The outreach spreadsheet** (`Orchestra_GOLD_Festival_Outreach.xlsx`) is the working list for research/triage, separate from HubSpot, and can drift out of sync with it — any batch research or drafting work should check HubSpot first per the existing pre-research check rule (skip/deprioritize anything HubSpot already shows as pitched, sync buyer info back to the spreadsheet if HubSpot has it and the spreadsheet doesn't).
- **Two Dropbox subfolders, not one:** `CoWork/Festival Outreach/` (research side — spreadsheet, rules file, research/triage scheduled tasks) and `CoWork/Pitch Machine/` (pitching side — `pitch_machine_state.json`, daily follow-up summaries). The latter reportedly never got created due to a cron mismatch as of the last rules-file update — worth verifying whether it exists yet before assuming it does.

## Knowledge base and the sync mechanism

Pitch Machine's drafting intelligence needs to draw on a continually-growing knowledge base, not a fixed prompt. Three Dropbox sources feed it: `CoWork/Festival Outreach/` (rules, research playbook, spreadsheet), `CoWork/Pitch Machine/` (state, follow-up summaries), and the email-automations folder (drafted email series, origin story, personal band history — richer narrative material for pitch drafting).

**Mechanism:** the GoDaddy-hosted app can't see the Mac's synced Dropbox folder directly, so it reads through the Dropbox API — reusing the same Dropbox integration already planned for the Posting Tool's media hosting, rather than building a second one. Content gets pulled into The Portal's own database as a queryable knowledge base on a schedule, rather than re-parsing markdown files at draft-time (slower, more fragile).

**Write-back, closing the loop:** when Erich edits a draft inside The Portal, the edit gets logged into The Portal's own database *and* appended as a short entry to `PITCH_MACHINE_RULES.md`'s "Recent decisions log" via the same Dropbox API. That file already declares itself the shared source of truth across every thread (scheduled tasks and live chats) — piping The Portal's own learning into it keeps a live Claude Code chat session and the Pitch Machine app reading from the same brain, instead of two that quietly drift apart over time.

### Outcome-weighted context retrieval (Session 13 continued)

This is the mechanism that connects the outcome-tracking substrate (see "Outcome tracking and weighting" under the booking-agent reframe above) to actual draft generation — without it, tracking outcomes is just a report nobody acts on. Confirmed as a required piece, not a nice-to-have, before further building: **pitches that actually worked need to be weighted more heavily in what the system draws on when writing the next pitch.**

**How it works at draft time.** When assembling context for a new pitch, the retrieval step doesn't just pull the static house-style rules and the curated archive (`2026-07 psych rock pitches...docx`) — it also pulls a small set of past real pitches as few-shot examples, ranked by two factors together: how well that past pitch's outcome went (replied, positive tone, eventually confirmed — ranked above no-reply or declined), and how similar its context is to the current target (same template category — Sabbath/Mali psych-scene vs. standard submission vs. folk/heritage framing — same rough festival tier). Successful, similar pitches get priority placement as examples; each example gets tagged explicitly in the prompt with what happened ("this pitch got a reply and led to a booking" / "this exact phrasing has been used without success, don't lean on it") — telling Claude *why* an example is worth learning from generalizes better than just silently feeding in winning text and hoping the pattern gets picked up implicitly.

**Cold-start handling.** Early on, there won't be enough concluded pitches per template/category for outcome-weighting to mean anything — a template with 2 data points shouldn't be treated as "proven." Retrieval falls back to the static house-style/archive sources alone until a template category crosses a minimum sample size (proposed starting threshold: 10 concluded pitches — i.e. reached either a reply or the end of the follow-up sequence — per template category; adjustable once real data exists to judge whether that's too conservative or too loose).

**Periodic reporting, not just silent weighting.** Per the existing delegation (Erich owns none of the tracking mechanics, Claude owns building/running/reporting), this should surface as actual findings shared back — e.g. "subject lines using direct comp-artist name-drops are getting roughly 2x the reply rate of generic ones this quarter" — not just an invisible scoring adjustment happening in the background. Ties directly into the A/B testing habit already locked.

**Cost control, confirmed.** Erich has an existing Anthropic API key already set up (used for the podcast tool) and is comfortable extending it to Pitch Machine, with the explicit goal of not blowing out API spend as context payloads get richer. **Prompt caching is confirmed as the mechanism for this** — the large, mostly-static blocks (the full house-style guide, the curated pitch archive, the outcome-ranked example set once it's assembled for a batch) get cached rather than re-sent and re-billed at full price on every single draft call within a batch/session.

**Refinement to cold-start handling: seed with Erich's tacit knowledge, not just wait for fresh data.** Erich pointed out there's a meaningful amount of "data" that already exists — not in the system, but in his own head, from years of actually doing this. Rather than treating the cold-start period as a blank wait for 10 concluded pitches per category, a short session where Erich identifies which of his own past pitches he *knows* worked well (independent of what The Portal has tracked so far) can manually seed a small set of trusted, tagged examples immediately. These get tagged as "Erich-confirmed effective," distinct from statistically-derived outcome data, and used in retrieval right away rather than waiting for the system to rediscover what he already knows. This overlaps naturally with the secret-sauce fit doc session already planned (same source material — real festivals/pitches that have actually worked) — worth treating as one combined session rather than two separate ones when it happens.

## Build sequencing (Session 13, confirmed)

Given the goal of shipping a real MVP quickly, not everything specced above needs to be built before the next milestone. Confirmed split:

**Build now — Session D complete (Aug 2026):**
- ✅ The static half of the knowledge sync — `PITCH_MACHINE_RULES.md` and the pitch archive `.docx` pulled into The Portal's `dropbox_sync` table via `flask sync-knowledge`. Content fed into the draft-generation system prompt as prompt-cached blocks.
- ✅ Draft generation for **Festival pitches only** — `DraftGenerator` in `app/integrations/claude_drafts.py`. Produces a research brief + Touch 1 draft per festival using the knowledge base as context. Synchronous batch of up to 5 per run; async via `api_task_queue` is the next upgrade.
- ✅ Touch 1 review/approve UI — `/draft-queue` (festival selection), `/review` (edit/approve/reject per draft), approve and reject POST endpoints. Approve logs to `approval_logs` and enqueues a `zoho_mail/send_pitch_touch1` task. Zoho send processor not yet built (Session E).
- ✅ Test-mode gate wired into the approval flow. Mode badge and redirect note are visible in the review UI.
- **Touch 1 stays human-reviewed indefinitely, explicitly reconfirmed.** No autopilot-style mode for now, regardless of anything seen in the Instantly audit — every pitch goes through Erich before sending, full stop, until there's a real track record to reconsider that from.

**What Session D does not build (still deferred):**
- Zoho email send processor — queue entries are created but not yet dispatched. Session E.
- Async draft generation — synchronous for now, 5-company cap. Session E.
- Research pipeline with live web search / buyer verification — Claude uses training knowledge and flags gaps. Full verification playbook (Verifalia, LinkedIn, etc.) is a manual step until further built.
- Outcome-weighted retrieval ranking — no concluded-pitch data exists yet to rank by.
- The multi-project "Tool" reframing and additional pitch types (WAA, show invites, Distribution) — real and worth having written down, not worth building until Festival pitching is proven.
- Touch 2/3 autonomy — gated behind a real Touch 1 track record.
- Warm-intro mapping, fit-confidence scoring, sacred-geometry UI direction — already marked deferred, unchanged.
- Dollar-value tracking per confirmed booking — explicitly premature until there's enough volume.

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
- HubSpot needs a reliable warm/cold signal per contact/organization to drive this routing — given the 10/10 property cap, this likely can't be a new HubSpot property and needs another mechanism (The Portal's own database, or reusing an existing property).

## Follow-up cadence — locked

Three different values had circulated across different conversations (7/11/22 days, 14/14 days, "10-12 days"). **Decision: 14 days / 14 days** (Touch 2 at day 14 after Touch 1, Touch 3 at day 28) — matches what's already documented as actually running in `PITCH_MACHINE_RULES.md` today, the lowest-disruption choice. Erich explicitly delegated this decision with the instruction to pick something, try it, and adjust if it's not working — treat this as a starting point, not permanent.

Stop the sequence immediately on any reply, at any stage — unchanged from the existing rule.

## Autonomy — where the human-in-the-loop line sits, and why

**Touch 1 (the initial cold pitch) requires Erich's review and explicit send approval indefinitely, for now.** This was a deliberate, reasoned decision, not just caution for its own sake: cold outreach is Pitch Machine's highest-judgment surface, the failure mode is asymmetric (a bad first impression with a festival buyer can quietly close doors elsewhere in a small, connected industry, in ways that never surface as a visible "failure"), the secret-sauce fit doc doesn't have real texture yet, and there's no track record of outcome data yet to validate that any weighting/pattern-matching is actually working. None of that is a permanent state — it's a "not yet" contingent on real data and a longer working history together, to be revisited periodically rather than decided once and forgotten.

**Touches 2 and 3 (the follow-ups) are the designated first target for real autonomy**, and this is genuinely feasible with minimal new infrastructure: they're far more formulaic ("just checking this is on your radar") and much lower-judgment than a first pitch, and a working Zoho OAuth connection, daily reply-checking, and auto-draft generation already exist today per `PITCH_MACHINE_RULES.md`. The delta to true autonomy is small: instead of leaving a generated Touch 2/3 draft sitting in Zoho's Drafts folder for Erich to manually send, a scheduled job calls Zoho's send API directly once the trigger condition is met (14 days elapsed with no reply → send Touch 2; 28 days elapsed with still no reply → send Touch 3), and the existing daily reply-check gets extended to cancel any pending scheduled send immediately upon any reply, rather than just flagging it.

**Non-negotiable safety gate before this goes live:** autosent Touch 2/3 must run through the same global test-mode/live-mode switch already built into The Portal's foundations (Session A). In test mode, these auto-sends redirect to Erich's own inbox first, so a batch of real-shaped autonomous sends can be observed safely before any of them reach an actual festival contact. Do not skip this step given how sensitive first-impression outreach is known to be.

## UI flow

1. **Login → Orchestra Gold → Pitch Machine.** Landing view shows the next 10-20 pitches to be made as small cards (name, location, comp-artist cross-references, a short description pulled from the spreadsheet) — Trello-board-like visual density, using Pitch Machine's own defined stages (not the generic Warm/Hot/Cold labels from the Trello template used as an early UI reference).
2. **"Do some pitches" → a checklist/table view** of the next 10-15 candidates (festival, location, comps, short description). Erich checks the ones he wants to work this session (check-all / check-none supported).
3. **"Get pitches ready" → an async batch job**, not a synchronous wait. Claude researches each selected festival (buyer confirmation, values, fit signals, the full research-brief checklist above) and drafts a pitch for each. A status indicator shows progress (expect ~3-4 minutes for a batch); Erich can step away during this.
4. **Issue flags, if any.** If Claude hits a genuine blocker (can't confirm a buyer, missing information, an ambiguous signal), it's flagged rather than guessed through. **These are resolved via chat, not inside The Portal's UI** — The Portal surfaces the flag and prompts Erich to go resolve it in a Claude Code/chat session, then re-run; no in-app resolution UI needed for v1.
5. **Per-contact compose view**, styled like Gmail's floating compose window but dark-themed to match The Portal, one contact at a time, advancing automatically after each is handled:
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

**Confirmed and locked:** the knowledge-sync architecture (Dropbox API → The Portal DB, with write-back into `PITCH_MACHINE_RULES.md`), outcome tracking and A/B testing under Claude's full ownership, the deprioritization tier after 2 silent cycles, tour-routing-aware batching as standard practice, the 14/14-day follow-up cadence, the Touch 1-human/Touch 2-3-autonomous split (gated behind test-mode verification), the collapsed-by-default three-touch window UI, and the full UI flow described above.

**Still open, deliberately deferred:**
- Warm-intro mapping has no build scoping yet — needs its own session.
- The secret-sauce fit doc has no defined format — first collaborative session on it should decide its own shape.
- Fit-confidence scoring has no formula — intentional, ongoing refinement rather than a spec-now item.
- Whether `CoWork/Pitch Machine/` (the second Dropbox subfolder) actually exists yet needs verification before assuming its structure.
- The plaintext Zoho credential in the existing scheduled task's prompt needs remediation as part of this build, not left as-is.

## Session 13 (Aug 10, 2026) — Pitch Machine as a modular The Portal tool

A voice-message brainstorm reframed Pitch Machine's place in The Portal's structure. Captured here before moving into the follow-up architecture discussion.

**Core idea: move Pitch Machine from "an Orchestra Gold subproject" to "a Tool," configurable per project.** Same reframing logic as the Posting Tool — cross-project utilities live under Tools, not nested inside one project's tree. Needs a settings/configuration layer per project: inputs, sample pitches, and pitch-type definitions.

**Multiple pitch types within a single project, not just one.** For Orchestra Gold alone, Erich named four distinct pitch types that need to coexist: festival pitches (the one fully built out so far), Western Arts Alliance pitches, show invites, and — later — Distribution's bulk vinyl pitches to record stores. Each has been run as a one-off so far; the tool needs to formalize "pitch type" as a first-class concept rather than treating festival outreach as the only shape.

**Multiple input sources per pitch type.** So far: HubSpot (already connected), the festival outreach spreadsheet, and one-off spreadsheets built for specific campaigns (WAA, show invites). Sources should be treated as configurable per pitch type, not hardcoded — more may get added later.

**Competitive reference: Instantly.ai.** Erich's friend Kurt pointed him to it as a comparable product, already further along mechanically. Erich set up a free account and asked for a full audit (findings logged separately, see the Session 13 addendum below) specifically to inform UI choices and figure out what to adopt vs. skip — not to copy its approach to pitch quality, which Erich explicitly does not want (Instantly optimizes for volume; Erich wants quality and a human feel, and confirmed the actual generated copy he saw was weak).

**Visual direction, explicit:** black background, typewriter font, a streamlined/simplified/minimalist version of Instantly's UI patterns — using only the functionality The Portal's Pitch Machine actually needs, not the full feature surface.

**Editable automation pathway per pitch type, confirmed as a real (later) build item.** Erich wants to be able to view and edit the touch sequence (e.g. festival pitching's submission → follow-up → second follow-up) per pitch type, using the existing sequence as a starting template. Not urgent for the festival pitch type specifically (already simple and defined), but necessary infrastructure once WAA/show-invite/distribution pitch types come online, since they may need different cadences or step counts.

**Open architecture question, not yet resolved — Erich wants input:** how should Pitch Machine draw on project-specific context (band voice, history, research) when generalized across multiple projects? Three options floated, undecided:
1. Mirror the current pattern — a spreadsheet per project that Claude reads from, same as the Festival Outreach setup.
2. Build the context/knowledge logic natively inside the Pitch Machine tool itself, calling the Claude API directly rather than routing through spreadsheet intermediaries.
3. A middle ground — initial open-ended research happens in a Cowork chat session per new project (building a seed list), which Pitch Machine then reads and branches out from, asking clarifying questions as needed.

Erich explicitly does not want Pitch Machine to guess at context the way Instantly does (scraping a website and inferring offers/positioning) — the quality of that auto-derived context was poor. Whatever mechanism is chosen needs to be grounded in real, deliberately-provided context (the same principle behind the Dropbox knowledge-sync architecture already locked above), not inference from a website scan.

### Instantly.ai audit findings (Session 13 addendum)

Full tour of Copilot, Engage (campaigns), CRM/pipeline, Reports, the AI Sales Agent dashboard (Live Feed, Leads, Memory, Settings), a live lead-review modal, and the sequence editor.

**Worth adopting:**
- **Memory architecture** — three-part split per agent: Business Details (company description + toggleable "offers"), Customer Profiles (target ICP), Guidance (freeform communication rules). Directly answers the context-sharing question above with a concrete, reusable shape.
- **Each agent is a discrete, cloneable unit** (Rename/Duplicate/Remove on the agent itself) — the mechanism for "one Pitch Machine instance per project or per pitch type," built by duplicating a template rather than reconfiguring shared logic.
- **Sequence editor is simple and worth copying near-exactly:** a "Wait [number] [Days/Weeks ▾]" control between each step, with later steps defaulting to "leave empty to use previous step's subject" for thread continuity. This is the editable-automation-pathway feature described above, essentially already solved.
- **Live Feed's approve/reject cards and lead-review modal** are close to the already-designed compose flow — each pending email shows "waiting for approval," and the review modal shows the full touch sequence alongside a rich research sidebar, including a short AI-written "Summary" paragraph per lead (a good compact model for the research-brief output).
- **Autopilot mode: a single global toggle, off by default** — validates the Touch-1-human/Touch-2-3-autonomous thinking already locked, though The Portal's version should be more granular (per touch number, not all-or-nothing per agent).
- **Schedule & Limits screen** (daily send cap, time-of-day window, day-of-week toggles, lead's timezone) — clean minimal reference for the rate-limit/throttle UI already planned.
- Headline dashboard metrics (Leads Found, Emails Sent, Replies, Opportunities with a dollar value) — a good minimal outcome-tracking display to mirror.

**Worth avoiding:**
- The actual generated pitch copy was weak and generic ("P.S. if this isn't useful, just ignore it" filler) — confirms Erich's read that Instantly optimizes for volume over quality. Not a UI lesson, a content-approach warning: don't let Pitch Machine's drafting default to generic template language the way Instantly's does.
- Auto-generated Business Details/Offers (built by scanning the company website) were off-target — generic "African psychedelic rock" framing, invented offers that don't reflect how OG actually pitches. Reinforces: Pitch Machine's context should be deliberately fed (Dropbox knowledge sync, house style file), never inferred from a website scan.

Small additional note: a Slack integration exists in Instantly's Settings — not urgent, but worth keeping in mind as a future notification-digest channel alongside email, consistent with the opt-in per-person digest cadence already locked in the main architecture spec.

**Parked for a future session:** Erich wants a further UI/visual-design discussion around sacred geometry and Egyptian architecture as design principles — screenshots to follow when he's ready. Not yet scoped, purely a placeholder so it isn't lost.
