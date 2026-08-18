"""
Claude API integration for Pitch Machine draft generation.

Loads pitch-type-specific knowledge from dropbox_sync, builds a system prompt
with prompt caching on the two large static blocks (house style rules + archive),
and generates a research brief + Touch 1 draft per target.

One DraftGenerator instance per batch and pitch type — the Anthropic client
reuses the HTTP session and cached blocks are billed at cache-read rates for
calls 2+ within the same batch.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Optional

from flask import current_app

from app.integrations.dropbox_sync import DropboxError, get_knowledge_for_pitch_type

_MODEL      = 'claude-sonnet-4-6'
_MAX_TOKENS = 4096

_SYSTEM_INTRO = """\
You are the booking agent AI for Orchestra Gold. Your job is to research targets \
and draft Touch 1 cold-outreach pitches that match Erich's exact voice and style.

Follow the house style rules exactly. Where relevant, reuse actual sentence blocks \
from the pitch archive rather than reconstructing them from scratch. Before drafting \
anything, produce a thorough research brief. Flag every gap explicitly with \
"⚠ Could not confirm:" rather than skipping bullets or guessing.

LINKS: The pitch archive contains hyperlinks as HTML <a href="URL">text</a> tags. \
When you include links in the Pitch Draft body (e.g. the ✱ ∞ ⊙ link-bullet section), \
reproduce them using the same <a href="URL">display text</a> format so the links \
remain clickable in the editor. Never strip the href — copy the actual URL from the \
archive example.

STYLE: Never use em dashes (—) anywhere in the pitch body or subject line. \
Use a hyphen (-), restructure the sentence, or use a comma instead. \
This is a hard house-style rule.

OUTPUT FORMAT: The Pitch Draft section must contain ONLY the email body — no flagging \
notes, caveats, or meta-commentary. If you need to flag something for Erich's attention \
(a factual uncertainty, a date to verify, etc.), put it in the Research Brief section, \
not in the Pitch Draft body.\
"""

# Per-pitch-type user prompt templates.
# Each one asks for a research brief then a pitch draft in the right format.

_FESTIVAL_TEMPLATE = """\
Draft a Touch 1 festival pitch for the following target.

Festival: {name}
Website: {website}
HubSpot description: {description}

Produce your response in exactly this format (no other headings):

## Research Brief
Cover each of the following; mark anything you cannot confirm with "⚠ Could not confirm:":
- Talent buyer: name, title, and how confirmed
- Festival vibe, history, and primary focus
- Attendance range and ticket pricing tier
- Notable sponsors or organizational values
- Any stated submission preferences or deadlines
- Comp-artist cross-references with genuine fit reasoning (not just genre-tagging)
- Lineup/booking patterns relevant to Orchestra Gold

## Pitch Draft
Subject: [subject line]
Body:
[full pitch body]\
"""

_WAA_TEMPLATE = """\
Draft a Touch 1 Western Arts Alliance pitch for the following presenter.

Presenter/Organization: {name}
Website: {website}
Notes: {description}

WAA context: Western Arts Alliance is a performing arts conference where presenters \
book artists for their venues/series. This is a showcase/conference pitch, not a \
festival submission. The tone should be presenter-to-presenter, relationship-first.

Produce your response in exactly this format (no other headings):

## Research Brief
Cover each of the following; mark anything you cannot confirm with "⚠ Could not confirm:":
- Presenter name and title
- Organization type (presenting series, venue, university presenter, etc.)
- Programming focus and typical artist tier
- Any known interest in world music, African music, or similar
- Connection to WAA or other presenting networks
- Fit reasoning specific to Orchestra Gold's profile

## Pitch Draft
Subject: [subject line]
Body:
[full pitch body]\
"""

_PNW_TEMPLATE = """\
Draft a Touch 1 Pacific Northwest tour pitch for the following venue/promoter/contact.

Venue/Contact: {name}
Website: {website}
Notes: {description}

PNW tour context: Orchestra Gold is routing through the Pacific Northwest. \
This is a show-invite pitch — asking if they'd like to host us during our tour, \
not a festival submission. The framing is tour-routing and relationship-building, \
not a booking application.

Produce your response in exactly this format (no other headings):

## Research Brief
Cover each of the following; mark anything you cannot confirm with "⚠ Could not confirm:":
- Venue/promoter type and capacity
- Programming focus and typical booking style
- Any previous OG connection or relevant history
- Best contact name and title
- Fit reasoning for the PNW tour specifically

## Pitch Draft
Subject: [subject line]
Body:
[full pitch body]\
"""

_PNW_MEDIA_TEMPLATE = """\
Draft a Touch 1 press/media outreach pitch for the following journalist, DJ, or media contact \
regarding Orchestra Gold's upcoming September PNW tour.

Contact/Outlet: {name}
Website: {website}
Notes: {description}

PNW tour context: Orchestra Gold is playing four confirmed September shows — \
Arcata Sep 23 (Miniplex), Astoria Sep 24 (KALA), Portland Sep 25 (Turn! Turn! Turn!), \
Seattle Sep 28 (Clock Out Lounge). This is a media pitch — seeking coverage, airplay, \
a feature, or a calendar listing, depending on the outlet. Not a booking inquiry.

Produce your response in exactly this format (no other headings):

## Research Brief
Cover each of the following; mark anything you cannot confirm with "⚠ Could not confirm:":
- Contact name, title, and outlet
- Beat/coverage focus (music genre, local scene, world music, etc.)
- Relevant past coverage of similar artists or tour coverage
- Best angle for Orchestra Gold (feature, preview, airplay, listing)
- Fit reasoning for the September PNW tour specifically
- Which tour stop(s) are most relevant to this contact's geography/beat

## Pitch Draft
Subject: [subject line]
Body:
[full pitch body]\
"""

_DEFAULT_TEMPLATE = """\
Draft a Touch 1 outreach pitch for the following target.

Target: {name}
Website: {website}
Notes: {description}

Produce your response in exactly this format (no other headings):

## Research Brief
Cover what you can; mark anything you cannot confirm with "⚠ Could not confirm:":
- Contact name and role
- Organization focus and fit for Orchestra Gold
- Relevant context for this pitch type

## Pitch Draft
Subject: [subject line]
Body:
[full pitch body]\
"""

from app.pitch_machine.pitch_types import PITCH_TYPE_SET  # noqa: E402

_TEMPLATES = {
    'Festival':         _FESTIVAL_TEMPLATE,
    'WAA':              _WAA_TEMPLATE,
    'PNW':              _PNW_TEMPLATE,
    'PNW Tour - Media': _PNW_MEDIA_TEMPLATE,
    'Show Invite':      _DEFAULT_TEMPLATE,
    'Distribution':     _DEFAULT_TEMPLATE,
}


@dataclass
class GeneratedDraft:
    research_notes: str
    subject: str
    body: str
    model: str
    input_tokens: int
    output_tokens: int


class DraftGenerationError(Exception):
    pass


class DraftGenerator:
    """
    Holds loaded knowledge and the Anthropic client for one batch session.
    Construct once per batch (same pitch type), call generate() per target.
    Prompt caching reuses the two static knowledge blocks across calls.
    """

    def __init__(self, pitch_type: str = 'Festival'):
        try:
            import anthropic
            self._anthropic = anthropic
        except ImportError:
            raise DraftGenerationError(
                'anthropic package not installed — run: pip install anthropic'
            )

        api_key = current_app.config.get('ANTHROPIC_API_KEY')
        if not api_key:
            raise DraftGenerationError('ANTHROPIC_API_KEY is not configured')

        try:
            rules_content, archive_content = get_knowledge_for_pitch_type(pitch_type)
        except DropboxError as e:
            raise DraftGenerationError(str(e))

        if pitch_type not in PITCH_TYPE_SET:
            raise DraftGenerationError(f'Unknown pitch type: {pitch_type!r}')
        self._pitch_type = pitch_type
        self._template   = _TEMPLATES.get(pitch_type, _DEFAULT_TEMPLATE)
        self._client     = self._anthropic.Anthropic(api_key=api_key)

        # System prompt: house style rules cached first, then archive cached on top.
        # Each cache_control block checkpoints everything before it too.
        self._system = [
            {
                'type': 'text',
                'text': (
                    _SYSTEM_INTRO
                    + '\n\n<house_style>\n'
                    + rules_content
                    + '\n</house_style>'
                ),
                'cache_control': {'type': 'ephemeral'},
            },
            {
                'type': 'text',
                'text': '<pitch_archive>\n' + archive_content + '\n</pitch_archive>',
                'cache_control': {'type': 'ephemeral'},
            },
        ]

    def generate(
        self,
        name: str,
        website: Optional[str],
        description: Optional[str],
    ) -> GeneratedDraft:
        user_msg = self._template.format(
            name        = name,
            website     = website     or 'Not available',
            description = description or 'Not available',
        )

        try:
            resp = self._client.messages.create(
                model      = _MODEL,
                max_tokens = _MAX_TOKENS,
                system     = self._system,
                messages   = [{'role': 'user', 'content': user_msg}],
            )
        except Exception as e:
            raise DraftGenerationError(f'Claude API call failed: {e}')

        raw = resp.content[0].text if resp.content else ''
        research_notes, subject, body = _parse_response(raw)

        return GeneratedDraft(
            research_notes = research_notes,
            subject        = subject,
            body           = body,
            model          = resp.model,
            input_tokens   = resp.usage.input_tokens,
            output_tokens  = resp.usage.output_tokens,
        )


def _parse_response(text: str) -> tuple[str, str, str]:
    """Split Claude's response into (research_notes, subject, body)."""
    parts = re.split(r'##\s*Pitch Draft', text, flags=re.IGNORECASE, maxsplit=1)
    if len(parts) == 2:
        brief_part, draft_part = parts
    else:
        brief_part, draft_part = '', text

    research_notes = re.sub(
        r'^##\s*Research Brief\s*', '', brief_part, flags=re.IGNORECASE
    ).strip()

    # Allow optional markdown bold around label (Claude sometimes outputs **Subject:**)
    subject_match = re.search(
        r'^\*{0,2}Subject:\*{0,2}\s*(.+)$', draft_part, re.IGNORECASE | re.MULTILINE
    )
    subject = subject_match.group(1).strip() if subject_match else ''

    body_match = re.search(
        r'^\*{0,2}Body:\*{0,2}\s*\n(.+)', draft_part, re.IGNORECASE | re.MULTILINE | re.DOTALL
    )
    if body_match:
        body = body_match.group(1).strip()
    elif subject_match:
        body = re.sub(
            r'^\*{0,2}Body:\*{0,2}\s*', '',
            draft_part[subject_match.end():].strip(),
            flags=re.IGNORECASE,
        ).strip()
    else:
        body = draft_part.strip()

    # Strip anything Claude appends after a --- divider in the body (flags, caveats, meta-notes).
    # Claude uses many phrasings ("Drafting notes:", "Flagging one item...", etc.) — catch all of them
    # by splitting on any --- separator and moving the tail to the research panel.
    drafting_split = re.split(r'\n[-—]{3,}\n', body, maxsplit=1)
    if len(drafting_split) == 2:
        body = drafting_split[0].strip()
        drafting_notes = drafting_split[1].strip()
        if drafting_notes:
            sep = '\n\n---\n\n' if research_notes else ''
            research_notes = (research_notes + sep + '**Notes:**\n' + drafting_notes).strip()

    # Hard safety net: replace any em dashes that slipped through (en dashes are fine for ranges)
    body = body.replace('—', '-')

    return research_notes, subject, body
