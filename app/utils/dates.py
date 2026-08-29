"""
Date helpers that return Erich's local time (America/Los_Angeles), not server UTC.

GoDaddy runs in UTC. After midnight UTC (5 pm Pacific), date.today() on the server
is one day ahead of the user's local date. Any user-facing date — today markers,
delta_days, overdue flags — must use local_today(), not date.today().

Audit columns (approved_at, override_set_at, etc.) stay on datetime.utcnow();
they record *when* something happened and are compared to other UTC timestamps.
"""

from __future__ import annotations

from datetime import date, datetime
from zoneinfo import ZoneInfo

_TZ = ZoneInfo('America/Los_Angeles')


def local_today() -> date:
    """Return today's date in Pacific time."""
    return datetime.now(_TZ).date()


def local_now() -> datetime:
    """Return the current datetime in Pacific time (timezone-aware)."""
    return datetime.now(_TZ)
