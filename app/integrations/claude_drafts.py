"""
Claude API integration for Pitch Machine draft generation.

Loads the knowledge base from dropbox_sync, builds a system prompt with
prompt caching on the two large static blocks (house style rules + pitch archive),
and generates a research brief + Touch 1 draft per festival.

One DraftGenerator instance per batch — the Anthropic client reuses the HTTP
session and the cached prompt blocks are billed at cache-read rates for calls 2+.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Optional

from flask import current_app

from app.integrations.dropbox_sync import (
    KNOWLEDGE_PATHS,
    DropboxError,
    get_knowledge_content,
)

_RULES_PATH   = KNOWLEDGE_PATHS[0]   # PITCH_MACHINE_RULES.md
_ARCHIVE_PATH = KNOWLEDGE_PATHS[1]   # .docx pitch archive

_MODEL      = 'claude-sonnet-4-6'
_MAX_TOKENS = 4096

_SYSTEM_INTRO = """\
You are the booking agent AI for Orchestra Gold. Your job is to research festival \
targets and draft Touch 1 cold-outreach pitches that match Erich's exact voice and style.

Follow the house style rules exactly. Where relevant, reuse actual sentence blocks from \
the pitch archive rather than reconstructing them from scratch. Before drafting anything, \
produce a thorough research brief. Flag every gap explicitly with "⚠ Could not confirm:" \
rather than skipping bullets or guessing.\
"""

_USER_TEMPLATE = """\
Draft a Touch 1 pitch for the following festival target.

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
    Holds the loaded knowledge and the Anthropic client for one batch session.
    Construct once, call generate() per festival.
    """

    def __init__(self):
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
            knowledge = get_knowledge_content()
        except DropboxError as e:
            raise DraftGenerationError(str(e))

        rules_content   = knowledge[_RULES_PATH]
        archive_content = knowledge[_ARCHIVE_PATH]

        self._client = self._anthropic.Anthropic(api_key=api_key)

        # System prompt: intro + rules cached together, then archive cached separately.
        # Each block with cache_control is a cache checkpoint that includes everything
        # before it — so the archive cache also covers the rules block.
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
        user_msg = _USER_TEMPLATE.format(
            name=name,
            website=website or 'Not set in HubSpot',
            description=description or 'Not set in HubSpot',
        )

        try:
            resp = self._client.messages.create(
                model=_MODEL,
                max_tokens=_MAX_TOKENS,
                system=self._system,
                messages=[{'role': 'user', 'content': user_msg}],
            )
        except Exception as e:
            raise DraftGenerationError(f'Claude API call failed: {e}')

        raw = resp.content[0].text if resp.content else ''
        research_notes, subject, body = _parse_response(raw)

        return GeneratedDraft(
            research_notes=research_notes,
            subject=subject,
            body=body,
            model=resp.model,
            input_tokens=resp.usage.input_tokens,
            output_tokens=resp.usage.output_tokens,
        )


def _parse_response(text: str) -> tuple[str, str, str]:
    """
    Split Claude's response into (research_notes, subject, body).
    Returns best-effort values even if the format drifts slightly.
    """
    # Split on ## Pitch Draft
    parts = re.split(r'##\s*Pitch Draft', text, flags=re.IGNORECASE, maxsplit=1)
    if len(parts) == 2:
        brief_part, draft_part = parts
    else:
        brief_part, draft_part = '', text

    research_notes = re.sub(
        r'^##\s*Research Brief\s*', '', brief_part, flags=re.IGNORECASE
    ).strip()

    subject_match = re.search(
        r'^Subject:\s*(.+)$', draft_part, re.IGNORECASE | re.MULTILINE
    )
    subject = subject_match.group(1).strip() if subject_match else ''

    body_match = re.search(
        r'^Body:\s*\n(.+)', draft_part, re.IGNORECASE | re.MULTILINE | re.DOTALL
    )
    if body_match:
        body = body_match.group(1).strip()
    elif subject_match:
        body = re.sub(
            r'^Body:\s*', '',
            draft_part[subject_match.end():].strip(),
            flags=re.IGNORECASE,
        ).strip()
    else:
        body = draft_part.strip()

    return research_notes, subject, body
