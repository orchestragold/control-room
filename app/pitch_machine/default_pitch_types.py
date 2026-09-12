"""
Default pitch type seed data.

These are the initial values written to pitch_type_configs on first boot.
After seeding the DB is the source of truth — add new types via the Portal admin UI,
not by editing this file.
"""

DEFAULT_PITCH_TYPES = [
    {
        # Festival - Cold: targets never yet contacted. Touch 1 is a no-ask introduction
        # that builds the relationship ahead of a future window. Longer interval because
        # an unhurried intro does not need a 14-day chase — Olivier Rey replied after 30 days.
        # touch1_to_touch2_days and touch2_to_touch3_days are seeded at 30 each (unreviewed
        # defaults — tune via Portal UI once real data exists).
        'name': 'Festival - Cold',
        'archive_dropbox_path': '/2026 pitches.docx',
        'badge_color': '#5aaa7a',
        'sort_order': 0,
        'touch1_to_touch2_days': 30,
        'touch2_to_touch3_days': 30,
        'touch2_prompt': (
            'Write a brief, warm Touch 2 follow-up for {name}. '
            'This is a no-ask cold introduction sequence — they have not replied to Touch 1. '
            'Keep it very short: one or two sentences maximum. Reference that you wrote recently; '
            'do not repeat the full introduction. End with 👍🏽'
        ),
        'touch3_prompt': (
            'Write a graceful close-out for {name}. This is the final touch in the cold sequence. '
            'One short paragraph. Wish them a great upcoming season. Leave the door open for next year. '
            'No ask, no urgency. End with 👍🏽'
        ),
        'prompt_template': (
            'Draft a Touch 1 NO-ASK INTRODUCTION for the following festival.\n\n'
            'Festival: {name}\n'
            'Website: {website}\n'
            'HubSpot description: {description}\n\n'
            'This is NOT a pitch with a booking ask. It is an introduction sent BEFORE the '
            'window opens, to build the relationship a year early. The model that works: '
            'two buyers replied warmly to letters that asked for nothing.\n\n'
            'Structure (in order):\n'
            '1. THE DISARM. Open by naming that this is an introduction rather than a submission. '
            'Be concrete about their situation, not generic. This must come first — it makes '
            'the rest of the email welcome rather than intrusive.\n'
            '2. THE SPECIFIC OBSERVATION. One genuine, particular thing about what they have '
            'built, drawn from the research context. Where their published language describes '
            'the festival, quote or paraphrase it and say why it lands.\n'
            '3. THE IDENTITY PARAGRAPH (use this shape exactly):\n'
            '   "Our band Orchestra GOLD is moving past the word \'world music.\' It\'s Mali and '
            '   Oakland colliding into one sound: Mariam Diakité\'s voice, rooted in Bamako and '
            '   the Baye Fall Sufi tradition, carried through cosmic guitars, horns, and groove '
            '   that comes out of my own path growing up an Arab American kid in Southern '
            '   California who loves heavy guitar rock music. Different places, different histories, '
            '   coming together to create something that other people can\'t fit into boxes."\n'
            '4. THE DAKAN PARAGRAPH:\n'
            '   "Our latest record is DAKAN, a Bambara word for destiny, the sense that some '
            '   paths aren\'t chosen so much as revealed. It follows that thread, what happens '
            '   when people from different backgrounds come together over shared purpose, '
            '   across distance and odds."\n'
            '5. LINKS: ✱ KEXP link  ∞ Live link  ⊙ Instagram\n'
            '6. THE NO-ASK LINE (required, close to verbatim):\n'
            '   "No ask right now, just wanted you to know we are breathing. One day, we\'d '
            '   love to be in the mix."\n'
            '7. "Hope this finds you well." then the closing.\n\n'
            'TONE: unhurried, warm, no urgency. NEVER mention fusion. NEVER put Erich\'s name '
            'in the sign-off. End with 👍🏽\n\n'
            'Produce your response in exactly this format:\n\n'
            '## Research Brief\n'
            'Cover each of the following; mark anything you cannot confirm with '
            '"⚠ Could not confirm:":\n'
            '- Talent buyer: name, title, and how confirmed\n'
            '- Festival vibe, history, and primary focus\n'
            '- Any notable programming notes (artists, values, stated booking preferences)\n'
            '- Artist callback opportunity (comp artist, year, source — omit if unconfirmed)\n\n'
            '## Pitch Draft\n'
            'Subject: [subject line]\n'
            'Body:\n'
            '[full letter body]'
        ),
    },
    {
        # Festival - Pitched Before: introduced in a prior outreach cycle, so a real ask
        # is earned. Shorter interval because a follow-up to a known contact can be tighter.
        # touch1_to_touch2_days and touch2_to_touch3_days seeded at 14 (unreviewed defaults).
        'name': 'Festival - Pitched Before',
        'archive_dropbox_path': '/2026 pitches.docx',
        'badge_color': '#3d8a57',
        'sort_order': 1,
        'touch1_to_touch2_days': 14,
        'touch2_to_touch3_days': 14,
        'touch2_prompt': (
            'Write a brief Touch 2 follow-up for {name}. '
            'They were introduced in a prior outreach cycle and received a Touch 1 pitch this cycle '
            'but have not replied. Keep it short: two sentences. Reference the earlier email; '
            'do not repeat the pitch. End with 👍🏽'
        ),
        'touch3_prompt': (
            'Write a graceful final close-out for {name}. '
            'This is Touch 3 — the last message in this cycle. One short paragraph. '
            'Warm, no pressure. Wish them a great season, express interest in connecting '
            'for the next cycle. End with 👍🏽'
        ),
        'prompt_template': (
            'Draft Touch 1 outreach for the following festival. '
            'This contact was introduced in a prior outreach cycle — they know the band name '
            'already. A real booking ask is earned; this is not a cold introduction.\n\n'
            'Festival: {name}\n'
            'Website: {website}\n'
            'HubSpot description: {description}\n\n'
            'Produce your response in exactly this format (no other headings):\n\n'
            '## Research Brief\n'
            'Cover each of the following; mark anything you cannot confirm with '
            '"⚠ Could not confirm:":\n'
            '- Talent buyer: name, title, and how confirmed\n'
            '- Festival vibe, history, and primary focus\n'
            '- Attendance range and ticket pricing tier\n'
            '- Notable sponsors or organizational values\n'
            '- Any stated submission preferences or deadlines\n'
            '- Comp-artist cross-references with genuine fit reasoning (not just genre-tagging)\n'
            '- Lineup/booking patterns relevant to Orchestra Gold\n\n'
            '## Pitch Draft\n'
            'Subject: [subject line]\n'
            'Body:\n'
            '[full pitch body]'
        ),
    },
    {
        'name': 'Festival',
        'archive_dropbox_path': '/2026 pitches.docx',
        'badge_color': '#5aaa7a',
        'sort_order': 0,
        'prompt_template': (
            'Draft a Touch 1 festival pitch for the following target.\n\n'
            'Festival: {name}\n'
            'Website: {website}\n'
            'HubSpot description: {description}\n\n'
            'Produce your response in exactly this format (no other headings):\n\n'
            '## Research Brief\n'
            'Cover each of the following; mark anything you cannot confirm with '
            '"⚠ Could not confirm:":\n'
            '- Talent buyer: name, title, and how confirmed\n'
            '- Festival vibe, history, and primary focus\n'
            '- Attendance range and ticket pricing tier\n'
            '- Notable sponsors or organizational values\n'
            '- Any stated submission preferences or deadlines\n'
            '- Comp-artist cross-references with genuine fit reasoning (not just genre-tagging)\n'
            '- Lineup/booking patterns relevant to Orchestra Gold\n\n'
            '## Pitch Draft\n'
            'Subject: [subject line]\n'
            'Body:\n'
            '[full pitch body]'
        ),
    },
    {
        'name': 'WAA',
        'archive_dropbox_path': '/WAA pitches.docx',
        'badge_color': '#5a7aaa',
        'sort_order': 1,
        'prompt_template': (
            'Draft a Touch 1 Western Arts Alliance pitch for the following presenter.\n\n'
            'Presenter/Organization: {name}\n'
            'Website: {website}\n'
            'Notes: {description}\n\n'
            'WAA context: Western Arts Alliance is a performing arts conference where presenters '
            'book artists for their venues/series. This is a showcase/conference pitch, not a '
            'festival submission. The tone should be presenter-to-presenter, relationship-first.\n\n'
            'Produce your response in exactly this format (no other headings):\n\n'
            '## Research Brief\n'
            'Cover each of the following; mark anything you cannot confirm with '
            '"⚠ Could not confirm:":\n'
            '- Presenter name and title\n'
            '- Organization type (presenting series, venue, university presenter, etc.)\n'
            '- Programming focus and typical artist tier\n'
            '- Any known interest in world music, African music, or similar\n'
            '- Connection to WAA or other presenting networks\n'
            '- Fit reasoning specific to Orchestra Gold\'s profile\n\n'
            '## Pitch Draft\n'
            'Subject: [subject line]\n'
            'Body:\n'
            '[full pitch body]'
        ),
    },
    {
        'name': 'PNW',
        'archive_dropbox_path': '/PNW pitches.docx',
        'badge_color': '#c8900a',
        'sort_order': 2,
        'prompt_template': (
            'Draft a Touch 1 Pacific Northwest tour pitch for the following venue/promoter/contact.\n\n'
            'Venue/Contact: {name}\n'
            'Website: {website}\n'
            'Notes: {description}\n\n'
            'PNW tour context: Orchestra Gold is routing through the Pacific Northwest. '
            'This is a show-invite pitch — asking if they\'d like to host us during our tour, '
            'not a festival submission. The framing is tour-routing and relationship-building, '
            'not a booking application.\n\n'
            'Produce your response in exactly this format (no other headings):\n\n'
            '## Research Brief\n'
            'Cover each of the following; mark anything you cannot confirm with '
            '"⚠ Could not confirm:":\n'
            '- Venue/promoter type and capacity\n'
            '- Programming focus and typical booking style\n'
            '- Any previous OG connection or relevant history\n'
            '- Best contact name and title\n'
            '- Fit reasoning for the PNW tour specifically\n\n'
            '## Pitch Draft\n'
            'Subject: [subject line]\n'
            'Body:\n'
            '[full pitch body]'
        ),
    },
    {
        'name': 'PNW Tour - Media',
        'archive_dropbox_path': '/PNW Tour - Media.docx',
        'badge_color': '#3ab8b8',
        'sort_order': 3,
        'prompt_template': (
            'Draft a Touch 1 press/media outreach pitch for the following journalist, DJ, or media contact '
            'regarding Orchestra Gold\'s upcoming September PNW tour.\n\n'
            'Contact/Outlet: {name}\n'
            'Website: {website}\n'
            'Notes: {description}\n\n'
            'PNW tour context: Orchestra Gold is playing four confirmed September shows — '
            'Arcata Sep 23 (Miniplex), Astoria Sep 24 (KALA), Portland Sep 25 (Turn! Turn! Turn!), '
            'Seattle Sep 28 (Clock Out Lounge). This is a media pitch — seeking coverage, airplay, '
            'a feature, or a calendar listing, depending on the outlet. Not a booking inquiry.\n\n'
            'Produce your response in exactly this format (no other headings):\n\n'
            '## Research Brief\n'
            'Cover each of the following; mark anything you cannot confirm with '
            '"⚠ Could not confirm:":\n'
            '- Contact name, title, and outlet\n'
            '- Beat/coverage focus (music genre, local scene, world music, etc.)\n'
            '- Relevant past coverage of similar artists or tour coverage\n'
            '- Best angle for Orchestra Gold (feature, preview, airplay, listing)\n'
            '- Fit reasoning for the September PNW tour specifically\n'
            '- Which tour stop(s) are most relevant to this contact\'s geography/beat\n\n'
            '## Pitch Draft\n'
            'Subject: [subject line]\n'
            'Body:\n'
            '[full pitch body]'
        ),
    },
    {
        'name': 'Show Invite',
        'archive_dropbox_path': '/2026 pitches.docx',
        'badge_color': '#aa5aaa',
        'sort_order': 4,
        'prompt_template': (
            'Draft a Touch 1 outreach pitch for the following target.\n\n'
            'Target: {name}\n'
            'Website: {website}\n'
            'Notes: {description}\n\n'
            'Produce your response in exactly this format (no other headings):\n\n'
            '## Research Brief\n'
            'Cover what you can; mark anything you cannot confirm with "⚠ Could not confirm:":\n'
            '- Contact name and role\n'
            '- Organization focus and fit for Orchestra Gold\n'
            '- Relevant context for this pitch type\n\n'
            '## Pitch Draft\n'
            'Subject: [subject line]\n'
            'Body:\n'
            '[full pitch body]'
        ),
    },
    {
        'name': 'Distribution',
        'archive_dropbox_path': '/2026 pitches.docx',
        'badge_color': '#888888',
        'sort_order': 5,
        'prompt_template': (
            'Draft a Touch 1 outreach pitch for the following target.\n\n'
            'Target: {name}\n'
            'Website: {website}\n'
            'Notes: {description}\n\n'
            'Produce your response in exactly this format (no other headings):\n\n'
            '## Research Brief\n'
            'Cover what you can; mark anything you cannot confirm with "⚠ Could not confirm:":\n'
            '- Contact name and role\n'
            '- Organization focus and fit for Orchestra Gold\n'
            '- Relevant context for this pitch type\n\n'
            '## Pitch Draft\n'
            'Subject: [subject line]\n'
            'Body:\n'
            '[full pitch body]'
        ),
    },
]
