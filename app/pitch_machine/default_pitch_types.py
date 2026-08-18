"""
Default pitch type seed data.

These are the initial values written to pitch_type_configs on first boot.
After seeding the DB is the source of truth — add new types via the Portal admin UI,
not by editing this file.
"""

DEFAULT_PITCH_TYPES = [
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
