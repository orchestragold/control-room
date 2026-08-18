"""
Canonical pitch type definitions for the Pitch Machine.

Any code that needs to enumerate, validate, or branch on pitch type
should import from here rather than duplicating the list inline.
"""

PITCH_TYPES: list[str] = [
    'Festival',
    'WAA',
    'PNW',
    'PNW Tour - Media',
    'Show Invite',
    'Distribution',
]

PITCH_TYPE_SET: frozenset[str] = frozenset(PITCH_TYPES)
