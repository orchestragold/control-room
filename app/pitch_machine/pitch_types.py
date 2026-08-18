"""
Pitch type registry — DB-driven after item 2.

get_pitch_types() / get_pitch_type_set() are the live sources; call these in
request context. The module-level constants (PITCH_TYPES, PITCH_TYPE_SET) are
kept as a fallback for import-time usage and test fixtures — they do NOT reflect
DB-added types.
"""


def get_pitch_types() -> list[str]:
    """Return active pitch type names ordered by sort_order. Falls back to hardcoded if DB unavailable."""
    try:
        from app.models.pitch_config import PitchTypeConfig
        types = [
            c.name for c in
            PitchTypeConfig.query
            .filter_by(active=True)
            .order_by(PitchTypeConfig.sort_order)
            .all()
        ]
        if types:
            return types
    except Exception:
        pass
    return ['Festival', 'WAA', 'PNW', 'PNW Tour - Media', 'Show Invite', 'Distribution']


def get_pitch_type_set() -> frozenset:
    return frozenset(get_pitch_types())


# Fallback constants — kept for test fixtures and import-time use.
# Do NOT use these in request handlers; call get_pitch_types() instead.
PITCH_TYPES: list[str] = ['Festival', 'WAA', 'PNW', 'PNW Tour - Media', 'Show Invite', 'Distribution']
PITCH_TYPE_SET: frozenset = frozenset(PITCH_TYPES)
