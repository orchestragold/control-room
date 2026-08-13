# The Portal — Architecture Spec (Draft v1)

*Working document from the August 4, 2026 planning session(s). Defines structure, not code. This is explicitly a continual build — pieces get added and refined over time, not a single big-bang launch — so this spec is meant to keep evolving alongside the build rather than being finalized once and handed off.*

## What this is

The Portal is a dashboard that unifies work currently split across Claude Code sessions, HubSpot, Asana, MailerLite, ContentStudio, and manual spreadsheets. It has two top-level hierarchies that sit side by side: **Projects** (the bands and creative entities the work is for) and **Tools** (shared utilities that cut across projects). Orchestra Gold is the first fully-built project. The Posting Tool is the first fully-scoped tool, and it already has a head start — the OG Publisher scoping doc from June 30, 2026 covers most of its Meta/YouTube/Thinkific architecture and is folded into this spec below.

Visual direction: simple, clean, black background, minimal chrome, similar type treatment to the podcast tool. Function over decoration.

### Layout

- **Left sidebar, collapsible.** Projects listed first as large headings, each expandable to show its subprojects nested underneath (e.g. Orchestra Gold → Pitch Machine, Distribution, Scheduling, Advance). Tools listed below Projects in the same sidebar, as their own peer section — matching the Projects/Tools hierarchy defined above, just made visible in the nav itself.
- **Landing view.** Opening the dashboard doesn't drop straight into a project — it shows a home/summary view: tasks due (across all projects, pulling from the Asana sync), and a feed of recent updates across projects (new pitch replies, holds added or changed, confirmed gigs, posting activity, etc.). This is the "what needs my attention today" surface, separate from drilling into any one subproject.

## Decisions locked (Session 2 — Aug 4)

Erich reviewed a Trello sales-pipeline template as a UI reference for the kanban shape of Pitch Machine — the board mechanic (cards, columns, drag-between-stages) is the right model, but Pitch Machine uses its own stage names as defined below, not the template's Warm/Hot/Cold labels. The following open items from the first pass are now resolved:

1. **Ownership handoff rule.** Not warm/cold by temperature — by pipeline position. Any contact newly added from the festival spreadsheet enters the pipeline under **Erich**. Once a contact reaches **Confirmed**, ownership transfers to **Maeve** for advance/logistics. Everything in between (drafting, sending, monitoring, follow-ups, negotiation) stays with Erich. Entry can be manual or bulk via spreadsheet upload.
2. **Follow-up cadence confirmed: 7, 11, and 22 days** after the previous touch (first follow-up at day 7, second at day 11, recycle decision at day 22 if still silent). Replaces the earlier placeholder 7–14 day estimate.
3. **Distribution contacts live in a spreadsheet today**, not HubSpot. Distribution gets its own pipeline built natively in The Portal's own database (mirroring Pitch Machine's stage-machine component) rather than assuming a HubSpot object — see Distribution section below.
4. **Scheduling: Google Calendar confirmed** as the date/time source of truth. The Portal owns the hold/note/notification layer on top of it.
5. **Posting Tool media hosting: Dropbox**, using existing account space, as the first choice over S3/Cloudinary.
6. **"Publish to the Vault" defined:** publish to Thinkific (current platform), and — once the new WordPress site is live — publish to its course section (built on MemberPress + WooCommerce) as a second destination. The Posting Tool should treat these as two distinct channel targets under one "course content" category, not a single Thinkific-only integration.
7. **GoDaddy plan type:** still pending — Erich is checking. Flagged below as a live open item; Claude will walk him through interpreting the plan once he has the details.
8. **MailerLite:** in scope. MailerLite has a documented public REST API (campaigns, subscribers, groups, automations), so it can be connected the same way as HubSpot/Asana. Recommendation: include it, scoped to read/report status back into The Portal first (e.g. surfacing the pending A/B test winner selection) before deciding whether The Portal ever triggers sends there.

## Decisions locked (Session 3)

9. **Ownership rule refined — it's not purely position-based.** Erich clarified: Maeve handles everything **warm**, meaning any contact who's done business with OG before — booked and paid for a gig previously — regardless of what stage that contact is currently in. Erich handles **cold**, meaning contacts with no prior business history. Combined with the Session 2 rule: a contact's starting owner is determined by warm/cold history at intake (repeat client → Maeve from the start; new/unknown → Erich), and any contact — warm or cold — transfers to Maeve once it reaches Confirmed if it wasn't hers already. This means HubSpot needs a reliable signal for "has this contact/organization booked and paid before" (a property or deal-history check) to drive the initial routing, since that's now doing real work rather than being a soft label.
10. **Distribution contacts will live in HubSpot, not a native The Portal database — reversing the Session 2 assumption.** Erich's original intent was always for record stores to become real HubSpot contacts, and he confirmed sticking with that. See "CRM: build vs. buy" below for the reasoning.
11. **Brand-level voice/style grouping, confirmed.** Accounts in the Posting Tool are grouped under a brand (Orchestra Gold, Calm Quiet Knowing, Mercury Red, etc.), and voice/style settings are set once per brand and apply to every account/channel under it — including the brand's website, not just social accounts. This answers the "does each brand need its own voice settings" question from Session 2: yes, at the brand level, one setting shared across that brand's channels.
12. **Notification digests: fully opt-in, per person.** Each person choosing to receive hold/update notifications picks their own cadence — instant, daily digest, weekly digest, or biweekly digest — rather than The Portal imposing one default for everyone.
13. **Timezone handling: Google Calendar's native per-event timezone support covers this**, reinforcing (not replacing) the Session 2 decision to use Google Calendar as the scheduling source of truth — no separate timezone-handling system needs to be built.

### CRM: build vs. buy (HubSpot vs. a native The Portal CRM)

Erich raised whether The Portal should eventually replace HubSpot with its own lightweight CRM, mainly to escape the free-tier 1,000-record cap. Worth laying out plainly:

**Case for staying on HubSpot (recommended for now):** HubSpot already solves the unglamorous, easy-to-underestimate parts of a CRM — contact deduplication, activity history, search, list segmentation, a usable mobile app and native UI as a fallback if The Portal itself is ever down, and (for Distribution specifically) doesn't require any new build at all since it's the same object model Pitch Machine already uses. Staying on HubSpot also means one less system to secure, back up, and keep compliant with opt-out rules — that logic already needs building once, and HubSpot's contact record is a fine place to store the do-not-contact flag.

**Case for a native CRM (later, if needed):** no record cap, no per-seat or per-tier cost as volume grows, and every automation rule (stage transitions, ownership handoff, recycle timing) can be built exactly to spec instead of adapted to HubSpot's pipeline model. The real cost is that a CRM looks simple until you've built one — dedup logic, audit history, search, and safe concurrent access are all things HubSpot already got right, and getting them wrong risks exactly the kind of silent data loss this whole dashboard is trying to prevent.

**Recommendation, matching Erich's instinct:** stay on HubSpot for both Pitch Machine and Distribution now — it's the lower-risk, faster path, and the free tier's 1,000-record cap isn't yet hit. Track record count against that cap (per the rate-limits gap noted earlier) as a leading indicator, and treat "build a native CRM" as a deliberate future decision to make once there's a real number showing HubSpot's limits are actually binding, not a default plan to build toward now.

## Top-level shell

```
The Portal
├── Projects
│   ├── Orchestra Gold
│   │   ├── Pitch Machine
│   │   ├── Distribution
│   │   ├── Scheduling
│   │   └── Advance (gig/festival info)
│   ├── (future: other bands/entities)
│   └── ...
└── Tools
    ├── Posting Tool
    ├── (future: other cross-project utilities)
    └── ...
```

Projects and Tools are peers, not parent/child. A Tool can read from and write to any Project it's connected to; a Project has no direct dependency on any Tool. This is why the Posting Tool doesn't live inside Orchestra Gold — it also serves Calm Quiet Knowing and Mercury Red, and folding it into one project's tree would make that cross-project reach awkward later.

### Roles and permissions

Three role tiers, scoped per project:

- **Super admin (Erich):** full access to every project and tool, including settings, integrations, and role assignment.
- **Editor (Maeve, initially):** scoped access to specific subprojects within a project. Her first grant is Pitch Machine's warm-contact lane (see below) plus whatever Scheduling/Advance pieces she already owns in Asana.
- **Member (band members, eventually):** read-only access to their own gig and rehearsal info, plus the ability to respond to notes/questions tied to specific hold dates (see Scheduling).

Permissions are set per subproject, not just per project, since Maeve's access to Pitch Machine is narrower than full project access. The role model should be built generically from day one even though only two roles (Erich, Maeve) exist at launch, since band-member logins are a known near-term need.

## Orchestra Gold subprojects

### Pitch Machine

A CRM-pipeline view over HubSpot, not a separate database — HubSpot stays the system of record, The Portal is the working surface on top of it. Structured as pipeline stages, not a generic Trello board, so status changes carry the automation logic described below.

**Stages:**

1. **Needs outreach** — contact added by the existing scheduled festival-discovery tasks, or manually.
2. **Ready for review** — Claude has drafted a pitch; batched 5–7 at a time into a review queue for Erich (or Maeve, for her lane) each work session.
3. **Sent** — pitch approved (individually or in bulk) and scheduled into the inbox at the right send time; subject lines auto-generated at send.
4. **Awaiting response** — the monitoring window. Follow-up cadence is locked: first gentle follow-up at **day 7**, second at **day 11**, and if still silent, recycle decision at **day 22**.
5. **No response — recycled** — after the day-22 mark, the contact is requeued for next year's cycle at the timing the outreach spreadsheet calculates from the festival's typical dates.
6. **Declined** — explicit no. Ends the cycle for this year; may still be recycled next year depending on the decline reason. Contacts who explicitly opt out of future contact are excluded from recycling permanently (see compliance note below), not just for the current cycle.
7. **In negotiation** — active back-and-forth (the Green River case is the reference example).
8. **Confirmed** — pushes the booking into the Advance/Calendar system.

**Ownership split (locked, refined in Session 3):** determined by warm/cold history at intake, not just pipeline position. A contact is **warm** if OG has done business with them before — booked and paid for a gig previously — and warm contacts belong to **Maeve** from the moment they enter the pipeline. A contact is **cold** (no prior business) and belongs to **Erich**, who owns it through drafting, sending, monitoring, and negotiation. Regardless of starting owner, any contact transfers to **Maeve** once it reaches **Confirmed**, for advance/logistics. This requires HubSpot to carry a reliable warm/cold signal per contact or organization (a property, or a check against deal/booking history) so intake routing isn't guesswork. Entry into the pipeline can be manual, one at a time, or bulk via a spreadsheet upload.

**Inbox-driven stage transitions.** The system should read inbox replies and move contact cards automatically rather than requiring Erich to update HubSpot by hand. Practically, this means a reply-classification step runs on new inbox activity tied to a pitch thread and sorts each reply into one of a small number of buckets: clear positive/interest (→ In negotiation), clear decline (→ Declined), an out-of-office or clearly-not-relevant auto-reply (→ no stage change, resets nothing), or anything ambiguous — forwarded threads, a reply from a different person, unclear intent. Per Erich's direction, ambiguous cases are never auto-resolved: they're parked and pushed to Erich for a manual call, same as any other failure mode (see Gaps section). This keeps the automation confident-only — it moves cards it's sure about and surfaces everything else rather than guessing.

**Review/approval surface:** a lightweight editor for reviewing a batch of drafted pitches, editing text, and approving one-by-one or in bulk. This is new UI, not something HubSpot provides out of the box.

**Integration needs:** HubSpot (CRM read/write — an MCP connector for this is already active in this workspace), email send/schedule (currently Gmail via API per the existing scheduled-task setup — confirm) and inbox read access for reply classification, and the festival outreach spreadsheet (date math for recycle timing).

### Distribution

Same pipeline shape as Pitch Machine, applied to record stores carrying OG vinyl instead of festivals. Recommend building Pitch Machine's pipeline as a reusable stage-machine component so Distribution is a second instance of it with a different contact list and different stage labels, rather than a second bespoke build. The record-store contact list currently lives in a spreadsheet, but per Session 3, the intent has always been for these to become real HubSpot contacts — confirmed as the direction (see "CRM: build vs. buy" above for the reasoning). Distribution's build is therefore: a one-time spreadsheet-to-HubSpot import (likely a new pipeline or object type within HubSpot, distinct from Pitch Machine's festival pipeline), then the same reusable stage-machine logic — inbox-driven transitions, follow-up cadence, ownership handoff — running against HubSpot's API, the same way Pitch Machine does. This makes Distribution a much closer sibling of Pitch Machine than originally assumed, sharing not just the pipeline component but the backing store too.

### Scheduling

Least defined piece so far. Known requirements:

- Solve for rehearsal availability collection across band members — likely a lightweight poll/response mechanism rather than a full scheduling engine.
- Support "hold" dates — dates blocked before a festival/show is confirmed — as first-class objects, each with its own thread of notes/updates visible to whoever the hold is relevant to (band members, Maeve, etc.), so status changes don't require individual emails.
- People attached to a hold can ask questions inline and get pinged on updates, rather than Erich manually notifying each person.
- **Underlying calendar: Google Calendar, confirmed.** The Portal wraps Google Calendar as the date/time source of truth (avoids rebuilding calendar UI and syncing headaches) and owns the hold/note/notification layer on top of it.
- **Pitch sprint capacity planning.** Erich wants to front-load pitching and take November–December mostly off. This isn't just a personal calendar note — it implies Scheduling (or Pitch Machine) should support planning outreach volume against a target window, e.g. seeing how many spreadsheet leads remain and whether the current pace clears them before a self-imposed cutoff. Worth a simple capacity/pace view rather than leaving this as something Erich tracks in his head.

### Advance (gig/festival information)

The confirmed-show information hub: venue details, load-in, contacts, technical info — the standard "advance" packet, but living in the dashboard instead of scattered docs. Confirmed items from Pitch Machine land here automatically. This is also the natural home for the "list of confirmed gigs/rehearsals" broadcast Erich described — a view that can be shared with band members or sent out on a schedule without manual compilation.

### Tasks (cross-cutting, not a subproject)

Every person (Erich, Maeve, eventually band members) sees their own upcoming tasks inside The Portal. Rather than rebuilding task management, this syncs with Asana (an MCP connector for Asana is already active in this workspace) — tasks created in Asana appear in The Portal, and tasks created in The Portal get pushed to Asana, so both stay current without picking one as the only interface.

## Tools

### Posting Tool

Working name only — "The Portal" was floated as the overall dashboard name, and the posting tool itself doesn't have a settled name yet. The June 30 OG Publisher scoping doc already covers most of the technical architecture for the first version of this, built for Orchestra Gold's channels specifically. Bringing it into The Portal means the same engine gets exposed as the shared cross-project tool, with the project/channel mapping layer added on top. Key points from that scoping session, still valid:

- **Direct API architecture, not a re-rented aggregator.** Own Meta app (Instagram + Facebook Pages), direct YouTube, direct Thinkific — no more ContentStudio once this is live. Meta Business Verification is already complete; the developer app (MultiPublisher) exists; Instagram content-publishing permission review is the next unblocked step.
- **13 destination channels mapped for Orchestra Gold alone**, split into ready-to-automate, needs-one-setup-step, and permanently-manual (personal Facebook profiles — Meta blocks this for good). That channel inventory becomes the template for onboarding a second project (Calm Quiet Knowing, Mercury Red) into the same tool: same three-bucket triage, new account list.
- **One content package fans out to every channel it's tagged for** — this is the core mechanic and should generalize cleanly to a multi-project version: a post belongs to a project, and only shows channel options that project has connected.
- **Podcast folds in here rather than getting its own tool.** Scheduling a podcast episode and its companion script to the right WordPress site becomes another channel type in the same fan-out system, not a separate build.
- **Bandcamp has no API.** Two options discussed: treat it as a manual step (the tool prepares the content, Erich posts it natively), or use the Claude in Chrome extension to drive the native Bandcamp UI directly when a post includes a Bandcamp destination. Either way this is a semi-automated channel, not a true API integration, and should be scoped as such rather than promised as automatic.
- **Cross-project connections:** the tool needs a concept of which social/WordPress accounts belong to which project, since Calm Quiet Knowing and Mercury Red both need posting capability but aren't Orchestra Gold. This is the main net-new architecture beyond what the scoping doc already covers — a project ↔ account mapping layer.

**Resolved this session:** media hosting is **Dropbox**, using existing account space — Meta and the WordPress/Thinkific targets fetch media from a public Dropbox link rather than a new S3/Cloudinary setup. "Publish to the Vault" now covers two destinations: Thinkific (current) and, once live, the course section of the new WordPress site (MemberPress + WooCommerce) — treated as a "course content" category with two channel targets rather than one.

**Also resolved this session:** accounts are grouped by brand (Orchestra Gold, Calm Quiet Knowing, Mercury Red, BMWP, etc.), and voice/style settings are configured once per brand, applying to every channel and account under it — including that brand's website, not just its social accounts. This is now a first-class grouping concept the Posting Tool needs, not just a per-account setting.

Still open: input format for a content package, and whether ContentStudio keeps running during the transition.

## Integration map

| System | Role | Status |
|---|---|---|
| HubSpot | CRM system of record for both Pitch Machine and Distribution | MCP connector active in this workspace; free-tier 1,000 record cap to watch (see Gaps) |
| The Portal's own database | Holds/notes/approval logs and other The Portal-native data — no longer a CRM substitute | New — not an existing system |
| Asana | Task system of record, two-way sync with The Portal's task view | MCP connector active in this workspace |
| Google Calendar | Date/time source of truth for Scheduling — **confirmed** | Not yet built |
| Email (Gmail, per existing scheduled tasks) | Pitch sending, monitoring, follow-ups, and inbox reads for reply classification | Existing automation to confirm/reuse, not rebuild |
| Meta Graph API (own app) | Instagram + Facebook Pages posting | App created, verification complete, permission review pending |
| YouTube API | Direct channel posting | Not yet built |
| Thinkific API | Vault content/lesson publishing (destination 1 of 2 for "course content") | Classic builder confirmed compatible; needs the Vault confirmed on classic builder |
| WordPress (MemberPress + WooCommerce) | Vault content/lesson publishing (destination 2 of 2), once the new site is live | Site in development |
| Dropbox | Media hosting for the Posting Tool — **confirmed** as first choice | Existing account, ample space |
| ContentStudio | Current social scheduler, being replaced | MCP connector active; sunset once Posting Tool is live |
| Bandcamp | No API | Manual or Chrome-extension-assisted only |
| Outreach spreadsheet | Festival date math for recycle timing; also the entry point for new Pitch Machine contacts | Location/format to confirm |
| MailerLite | Fan-facing email — **in scope**, has a public REST API | Not yet connected; recommend starting read/report-only |

## Hosting — resolved (Session 5)

Confirmed by walking through the actual account (yellowwebdesign.com, cPanel-based GoDaddy shared hosting, currently running Orchestra Gold, Mercury Red, and White People Black Music on WordPress):

- **No Node.js app support** in cPanel's Software section — ruling out the stack most modern interactive dashboards default to.
- **Python App and Ruby App setup are both available** (via cPanel/Passenger), along with **SSH access** and **Git version control** under Security/Files. That's a genuinely usable, non-fragile combination — a real terminal plus git-based deploys plus a supported app runtime — which is better than typical shared hosting.
- **Resource ceiling is real, not theoretical.** This is one shared account already serving three live WordPress sites; disk (16.6/50GB) and databases (9/25) have headroom, but the account shows a hard cap of **10/10 addon domains already used (100%)** — no room for a new addon domain without a plan upgrade, though subdomains are wide open (10/50). A persistent app process running alongside three production WordPress sites' PHP processes on a shared-tier account also risks contention that wouldn't show up until it's under real load.

### Recommendation revised (Session 6) — cost reality check

The dedicated-platform recommendation above assumed "free tier" meant actually free. Checked that assumption since the whole point of this move is to cut costs, not add a new bill:

- **Railway:** free trial gives $5 in usage credits for 30 days, then drops to a $1/month tier with just 0.5GB RAM — not enough for a real app. The practical tier for anything real is **Hobby, $5/month minimum**, and a Node app plus a database typically lands $6–12/month once actually running.
- **Render:** free web services exist indefinitely but **spin down after 15 minutes of inactivity** (about a minute to wake back up) — a real problem for something meant to monitor an inbox or send timed notifications promptly. Free Postgres databases are capped at 1GB and **expire after 30 days**, after which it's a paid upgrade or the data is deleted.
- **Vercel:** generous free tier for a frontend and light serverless functions, but not built for the kind of always-on background monitoring Pitch Machine's inbox-classification and follow-up cadence need — free-tier function executions have short time limits, and persistent background workers aren't really its model.

None of these are free for what The Portal actually needs to do (persistent background monitoring, a real database, prompt responsiveness). They're free for prototypes and start charging once it's a real always-on app — realistically **$5–25/month** territory.

Against that, the GoDaddy plan is **already paid for**, and per the walkthrough above it genuinely supports what's needed: SSH access, Git-based deploys, a supported Python app runtime, and (standard on cPanel, not yet checked in the screenshots above but worth confirming) cron jobs for scheduled/background tasks like inbox polling. **Given the cost-reduction goal, building The Portal as a Python app on the existing GoDaddy account is now the better default**, not the fallback.

**What that costs in tradeoffs, honestly:**
- **Resource contention risk.** This is one shared account serving three live WordPress sites already. A background process running continuously (inbox polling, HubSpot syncs) shares CPU/memory with those sites' PHP processes. Current usage is very light (0% CPU, under 1% memory), so there's real headroom today, but it's worth monitoring once The Portal is live — a spike in one shouldn't be able to take down the band's own website.
- **Stack fit, not a hard blocker.** Python is a completely capable choice for the backend (Flask or FastAPI), but the ecosystem for building a slick, modern interactive UI leans more toward JavaScript/React. This isn't fatal — a static JS/React frontend can be built separately and served as static files from the same GoDaddy account (shared hosting serves static files natively, no Python needed for that part), with a Python API underneath handling the HubSpot/Asana/Meta/Calendar integrations. That's a very workable split: static frontend + Python backend, both on infrastructure that's already paid for.
- **No autoscaling or preview environments.** Deploys are manual (or scripted via the Git/SSH access) rather than the one-click, automatic-preview workflow modern platforms offer. Not a cost, but a workflow difference worth knowing going in.

**Bottom line:** default to GoDaddy (Python backend + static frontend, both self-hosted there) to keep this at effectively $0 incremental cost, and treat Railway/Render/Vercel as the fallback only if resource contention with the WordPress sites turns out to be a real problem in practice — not the starting assumption. If The Portal needs its own subdomain off an existing GoDaddy domain (e.g. `control.orchestragold.com`), that's available regardless of which path is chosen — the addon-domain cap doesn't apply to subdomains.

## Decisions locked (Session 4)

14. **Warm/cold signal, resolved.** Rather than deriving it live from deal data, Erich will hand over a spreadsheet of OG's historical gigs in a short dedicated session; that becomes the source for a one-time pass that tags each existing HubSpot contact/organization warm (appears in the gig history) or cold (doesn't). New contacts added afterward inherit the same check going forward. This is simpler than building live deal-history detection and reuses data Erich already has.
15. **ContentStudio sunset timing: no fixed date.** It stays running until the Posting Tool actually covers Orchestra Gold's current channel set — a functional cutover, not a calendar one.
16. **HubSpot baseline pulled and cap confirmed.** Live count as of this session: **539 contacts, 426 companies**. Confirmed the 1,000-record cap on HubSpot's free tier (for accounts created after September 2024) applies specifically to **contacts**, not companies — Erich's original instinct was right. That puts the account at 539/1,000 contacts, a bit over halfway, not the 965 combined figure flagged earlier. Companies aren't governed by that same ceiling, which matters for Distribution: if record-store contacts land as HubSpot **contacts**, they compete for the same 1,000-contact budget as festival contacts; if structured more as **companies** with fewer associated contacts, there's substantially more room. Erich noted HubSpot's default setup nudges toward a roughly one-company-to-one-contact pattern (a company record plus a corresponding contact) — worth confirming directly against how the existing 539/426 are actually structured before deciding Distribution's shape, rather than assuming either a strict 1:1 or a many-contacts-per-company model.
17. **Hosting resolved** — see the Hosting section below (revised further in Session 6, see below).
18. **Posting Tool content-package format resolved — pulled live from ContentStudio rather than relying on old screenshots.** ContentStudio's actual data shape for a post (confirmed via a live API pull against the Orchestra GOLD workspace) is: one base/common content block, a per-platform `overrides` object letting each destination (Facebook, Instagram, YouTube, TikTok, etc.) have its own text and media while defaulting to the common content if not overridden, a list of target accounts, a scheduling block (publish type + execute time), and an optional first-comment block applied across accounts. This maps directly onto the "one content package fans out to every channel it's tagged for, with per-channel override" mechanic already described under Posting Tool above — The Portal's content-package format should mirror this shape (common content + per-channel overrides + target account list + schedule), which also makes a ContentStudio-to-Posting-Tool migration path straightforward later if wanted.

### Rate limits: shared throttle layer vs. per-integration — explained

This was flagged as a gap but not explained clearly enough to decide on. In plain terms: every external API The Portal talks to (HubSpot, the email provider, Meta, YouTube, MailerLite) enforces a limit on how many requests it'll accept in a given window — e.g. Instagram allows roughly 200 API calls per hour per account. If The Portal ever sends more requests than that in the window, the platform starts rejecting calls, which could mean a pitch fails to send or a post fails to publish with no warning unless something catches it.

**Option A — a shared throttle/queue layer.** One piece of shared infrastructure that every outbound API call passes through, each platform's limit configured once, and calls get spaced out or queued automatically so nothing ever exceeds the limit. Built once, applies everywhere automatically as new integrations get added.

**Option B — handle it per-integration.** Each integration (HubSpot module, Meta module, etc.) manages its own pacing individually, written specifically for that platform's limits.

**Recommendation:** Option A, the shared layer, and here's the concrete reasoning: The Portal already has several integrations planned that will each need this (HubSpot, Meta, YouTube, MailerLite), so building it once now is genuinely less total work than writing similar throttling logic four separate times, and it means every future integration (Thinkific, WordPress, the next project onboarded to the Posting Tool) gets rate-limit safety for free instead of needing it re-implemented. The only reason to choose Option B would be if the integrations' limits were wildly different in kind (they're not — they're all "N requests per time window," which is exactly what a shared queue is built to handle). This is a small, well-scoped piece of infrastructure, not a big upfront investment — worth building early rather than retrofitting once four integrations already have their own bespoke pacing code.

## Open questions for next session

- Distribution: confirm how the existing 539 contacts / 426 companies are actually structured in HubSpot (roughly 1:1, or many-to-one) before deciding how record-store data should be shaped.

## Decisions locked (Session 9)

24. **Login mechanism: Google Sign-In (OAuth), confirmed.** Erich expects no more than 10–12 people logging in over the next year or two — comfortably within what Google Sign-In handles with no added infrastructure, no password reset flows, and no magic-link email system to build.
25. **Cron Jobs confirmed available on the GoDaddy plan** — standard cPanel tool, no jobs currently configured (clean slate). This closes the last open infrastructure question from the hosting walkthrough: background automation (inbox polling, the 7/11/22-day follow-up cadence, digest notifications) runs via cPanel cron jobs calling into the Python app on a schedule.

## Gaps not yet addressed

A few things worth deciding on purpose rather than discovering by accident once real contacts and real festivals are running through this.

**Failure modes for automated outreach — resolved, direction set.** Per Erich: don't try to auto-resolve edge cases. Any failure (bounced send, API error, stale contact info) or ambiguous reply gets parked and pushed to Erich for a manual call, rather than the system guessing. This is now the standing rule baked into the inbox-driven stage transitions described under Pitch Machine above — the automation only moves cards it's confident about.

**Compliance on outreach email — direction set, mechanism still needed.** Erich confirmed: anyone who asks off cold outreach must be taken off, permanently. Mechanism proposed for the build: a "do not contact" flag on the HubSpot contact record, set the moment an opt-out is detected (either the reply-classification step flags explicit opt-out language, or Erich sets it manually during review), checked before any automated send and before any recycle-into-next-year action. Once set, it's never cleared by the system — only a manual, deliberate action from Erich or Maeve could undo it. This needs to be a hard gate in the code, not a convention people have to remember to follow.

**Rate limits — two distinct problems, both real.** Erich correctly separated these: HubSpot's free-tier plan caps total stored records at 1,000, which is a *capacity* ceiling — separate from *rate* limits (calls per hour) on HubSpot, the email provider, Meta, and YouTube. For the rate-limit side, recommend a shared throttle/queue layer in The Portal that all outbound API calls pass through, each with its platform's known limit configured, so nothing ever gets sent fast enough to trip a limit — this can be a fairly small piece of shared infrastructure rather than something built per-integration. For the 1,000-record capacity ceiling: worth tracking current HubSpot record count against that cap now, before it's hit unexpectedly mid-cycle. Two ways out when it gets close — upgrade the HubSpot plan, or (since Distribution is already being built as a native The Portal pipeline rather than in HubSpot) let Pitch Machine follow the same pattern for overflow rather than committing to a paid HubSpot tier by default. Not urgent yet, but worth a running counter so it doesn't become a surprise.

**Maeve's availability — not currently a blocker.** Confirmed: outreach isn't day-sensitive enough for this to matter today. The more concrete need that came out of this is capacity planning around Erich's own November–December pitch sprint goal, now captured under Scheduling above.

**Approval audit trail.** Bulk-approving 5-7 pitches at once is efficient but means there's no record of what was actually sent unless the tool keeps one. Worth logging what was approved, by whom, and the exact text sent — useful both for consistency checking later and if a festival ever disputes what was said.

**Notification fatigue — resolved.** Fully opt-in, per person: each recipient chooses their own cadence (instant, daily digest, weekly digest, or biweekly digest) rather than The Portal imposing a default.

**Timezone handling — resolved by the Google Calendar decision.** Google Calendar handles timezones natively per event, which covers hold dates and send times without any separate system. Rehearsal-poll timezone display still needs normal UI care (show each person's response in their own local time), but there's no new infrastructure to build here beyond wrapping Google Calendar as already planned.

**Data ownership and backup.** HubSpot, Asana, and Google Calendar (if chosen) stay systems of record, which is the right call, but it means The Portal's own database is mostly a view/cache layer plus the genuinely new data (holds, notes, approval logs). Worth being explicit about what data only exists in The Portal's own database, since that's the part that needs its own backup plan — everything else is recoverable from the source system.

**Testing against real contacts.** The Meta build already smartly uses "own accounts only" to sidestep review during development. Pitch Machine doesn't have an equivalent safe zone — there's no dry-run mode mentioned for testing the approval-to-send pipeline without risking a real send to a real festival contact. Worth building one before this goes live with actual outreach.

**Access if Erich is unreachable.** As super admin, if The Portal is the only place certain workflows live (approvals, holds), what happens if Erich is on tour with spotty connectivity during a time-sensitive approval window? Not necessarily a blocker, but worth a conscious answer (e.g., Maeve gets emergency elevated access, or nothing is ever truly time-critical enough to need it).

**Growth beyond two projects.** The architecture is designed to generalize (Projects/Tools as peers, reusable pipeline component, project-scoped roles), which is good, but nothing in the current scope tests that generalization — everything concrete so far is Orchestra Gold. Worth sanity-checking the data model against a second hypothetical project (even just on paper) before the first version gets built, so "customizable per project" doesn't turn out to secretly assume OG's shape everywhere.

## One more pass — anything left out (Session 7)

Final gap check before moving toward a first build prompt. Two things worth flagging that hadn't come up yet:

**The GoDaddy account's SSL certificate is expired right now.** This showed up directly in the account walkthrough screenshot — "SSL Certificate: Expired," flagged by GoDaddy itself as "your domain is at risk." This is independent of The Portal entirely and worth fixing regardless of any decision above, but it's especially relevant now that this account is the leading candidate to host The Portal's backend and database, which will hold HubSpot/Meta/MailerLite credentials and business data. Worth renewing before building anything new on this account, not after.

**Login/authentication for The Portal itself.** The roles-and-permissions model (super admin, editor, member) is defined, but nothing yet specifies how someone actually logs in — email/password, Google sign-in, magic link, something else. Matters especially for band members eventually, who'll want something low-friction.

**Secrets management.** The Portal will hold live credentials for HubSpot, Meta, Google Calendar, MailerLite, and Dropbox. Needs a real answer for where those live (environment variables, a secrets manager) rather than sitting in a config file — more pointed now that the leading hosting option is shared hosting rather than a platform with built-in secrets handling.

**Staging vs. production, made more important by the continual-build model.** Since this is explicitly not a one-and-done build, there needs to be a safe way to test a new feature or change without risking a live automation — an in-progress Pitch Machine change shouldn't be able to accidentally email a real festival contact, and an in-progress Posting Tool change shouldn't be able to accidentally post to a real Instagram account. Worth a lightweight dev/staging setup (a test HubSpot list, test social accounts, or a "dry run" mode per integration) before Pitch Machine and the Posting Tool are handling real outreach and real posts. This connects back to the "testing against real contacts" gap flagged earlier — it's the same problem, now sharper given the build will keep changing indefinitely rather than shipping once.

**Observability — who notices if something silently breaks.** With the "park it and flag Erich" failure-mode rule already in place, there also needs to be a way those flags actually surface (a notification, a dashboard badge) rather than requiring Erich to remember to check. Otherwise a stalled pipeline could sit quietly for a while before anyone notices — worth folding into the same landing-page summary view already planned for tasks/updates.

**Mobile/on-the-go use.** Approving pitches and checking hold-date updates plausibly happens on tour, off a phone, with unreliable connectivity — worth keeping the UI genuinely usable on mobile from the start rather than as an afterthought, given the touring cadence already described in Orchestra Gold's calendar.

## Decisions locked (Session 8)

19. **Login domain: `control.orchestragold.com`, confirmed.** A subdomain off the existing GoDaddy domain, pointed at wherever the app actually runs. No addon-domain slot needed for this.
20. **Notification/landing view, made concrete.** Logging into The Portal surfaces what needs attention *that day* plus what's upcoming — not a passive feed to go find, but the first thing seen on login. This absorbs the earlier "observability" gap directly: stalled/flagged items (a failed send, an ambiguous reply parked for review, a hold update someone's waiting on) show up here automatically rather than requiring Erich to go looking.
21. **Mobile usability, confirmed as a real requirement, not a nice-to-have.** Erich wants to genuinely use this on the road — approving pitches, checking holds — so the UI needs to hold up on a phone from the start, not get a responsive pass later.
22. **Staging/dry-run mechanism, defined.** In test mode, every outgoing email redirects to `orchestragold@gmail.com` instead of the real recipient (subject line or a header noting who it *would* have gone to), so the full send pipeline can be exercised without any risk to real festival contacts. The live/production mode sends to real addresses. Worth an explicit intermediate step too: once redirect-mode testing looks solid, add a small whitelist of trusted real test contacts (or send genuinely low-stakes test pitches to a couple of known, friendly contacts) before flipping fully live — a middle rung between "everything redirected" and "everything real," rather than jumping straight from one to the other. The same redirect pattern applies to the Posting Tool once it's being tested — a test mode that posts to a private/test account instead of the real Instagram/Facebook Page.
23. **Pitch Machine rollout, phased.** The 5–7-at-a-time bulk approval described earlier is the target state, not the starting state. Erich wants to start by approving and sending pitches **one at a time** to confirm the pipeline is behaving correctly end to end, then graduate to small batches, then to full bulk approval once trust is established. Worth building the review/approval surface to support both from day one (approve-one and approve-many) rather than bulk-only, so this rollout doesn't require a follow-up feature.

### API secrets — options and tradeoffs

Erich asked for the real options here, not just a placeholder answer. Three practical approaches, in order of how well they fit a GoDaddy-shared-hosting-plus-Python build:

**1. Environment variables on the server (recommended starting point).** Credentials for HubSpot, Meta, Google Calendar, MailerLite, and Dropbox live in a `.env` file on the GoDaddy account, outside the web-servable directory and outside Git (never committed to the repo), read into the Python app at startup. *Pros:* simple, zero additional cost, works naturally with the SSH/cPanel setup already confirmed, and is the standard approach for an app this size. *Cons:* manual to rotate a credential (SSH in, edit the file, restart the app), and if the server itself is ever compromised, the file is readable — though this risk applies to any option on shared hosting.

**2. A dedicated secrets manager (1Password's developer vault, Doppler, or similar).** Credentials are stored in a separate managed service and pulled in at runtime via an API call, rather than sitting in a file on the server. *Pros:* audit trail of who accessed what and when, easy rotation from one place, and secrets are never sitting in a plain file on disk. *Cons:* another account and (usually) another monthly cost, another moving piece for a shared-hosting deployment to depend on, and mild overkill for a two-to-three-person team at this stage.

**3. Database-encrypted storage.** Secrets stored in The Portal's own database, encrypted at rest, decrypted by the app when needed. *Pros:* keeps everything in one place (the app's own database) rather than a separate file or service. *Cons:* The Portal's own database backup then also contains encrypted credentials, meaning backup security matters more, and it requires building/maintaining encryption logic that Option 1 gets for free from the filesystem.

**Recommendation:** start with Option 1 (environment variables, outside Git). It's the standard choice for a project this size, costs nothing, and fits the GoDaddy/Python/SSH setup already decided on. Revisit Option 2 only if the number of people needing credential access grows enough that a shared `.env` file becomes a real coordination or audit problem — not a starting assumption.

## What this feeds

Given the continual-build framing, this spec isn't meant to produce one giant build prompt — it's meant to produce a first, small, working slice, then keep growing. A reasonable first slice: the shell (Projects/Tools navigation, roles, the landing summary view) plus Pitch Machine as the first working subproject, since it's the most fully specified and already has live HubSpot/Asana connectors to build against. Everything else in this document — Distribution, Scheduling, Advance, the Posting Tool, the additional subprojects — gets built as its own follow-up slice once the shell and Pitch Machine prove the pattern out, with this document updated as each slice surfaces new decisions (the way this session's HubSpot check and GoDaddy walkthrough already did).

## Decisions locked (Session D — Pitch Machine MVP, Aug 2026)

26. **Knowledge sync built (static half).** `PITCH_MACHINE_RULES.md` and the pitch archive `.docx` pull from Dropbox into the `dropbox_sync` table via `flask sync-knowledge`. Auth: Dropbox OAuth refresh-token flow (`DROPBOX_APP_KEY`, `DROPBOX_APP_SECRET`, `DROPBOX_REFRESH_TOKEN` env vars). The `.docx` is parsed to plain text with `python-docx`.

27. **Draft generation wired — Festival pitches only.** `DraftGenerator` (in `app/integrations/claude_drafts.py`) assembles the house-style rules and pitch archive as two cached system-prompt blocks, then generates a research brief + Touch 1 draft per festival using Claude claude-sonnet-4-6. Prompt caching confirmed as the cost-control mechanism; the two large static blocks are reused across all calls in a batch session. Model and batch limit (5 per synchronous run) are constants in the integration file — easy to change when async processing is added.

28. **Touch 1 review/approve UI built (Session D scope).** Three new routes: `/draft-queue` (festival selection checklist), `/review` (per-draft edit/approve/reject), approve and reject POST endpoints. Approve: saves edits, sets status='approved', writes `ApprovalLog`, enqueues a `zoho_mail/send_pitch_touch1` task in `api_task_queue`. The Zoho send processor is not yet built — the queue entry sits until Session E wires it. Reject: sets status='rejected' and logs.

29. **Test-mode gate wired into approval flow.** `resolve_email_recipient()` (Session A) runs at approve time: in test mode, the actual send target is `orchestragold@gmail.com` with the intended address annotated in the subject line. The mode badge and redirect note are visible in the review UI so the state is never ambiguous.

30. **`pitch_approvals` schema extended.** New columns: `company_name`, `research_notes`, `to_email`, `cc_email`. Migration ALTER TABLE statements are at the bottom of `migrations/schema.sql` for existing installs. `to_email` is blank at generation time — Erich fills it in during review. `cc_email` pre-fills to `booking@orchestragold.com`.

31. **App renamed `control.orchestragold.com` → `portal.orchestragold.com`.** All text-level renaming (docs, templates, flash messages) done. Subdomain provisioned, AutoSSL issued, Python App entry repointed, Google OAuth URI updated. MySQL DB name stays `controlroom` (internal identifier, not worth migration risk).
