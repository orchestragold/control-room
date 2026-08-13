"""
Send-date algorithm for Pitch Machine — decisions #11-15, Session 14.

compute_send_date(festival_date) → ideal Touch 1 send date.

Rules applied in order per decision #15:
  1. Base: 1st of month, 8 months before festival month
  2. European blackout: July 15 – Aug 31 → push to September 1
  3. Buyer's own festival blackout: avoid month immediately before any of their festivals
  4. Day-of-week nudge: nearest Tuesday; Thursday if Tuesday is not workable

Assumptions (flag here so they're easy to find and fix):
  DEADLINE INTERPRETATION: 'deadline' in the queue sheet is treated as the festival's
  own date. send_date = 1st of the month 8 months before deadline month.
  If 'deadline' actually means the booking submission cutoff (not the festival date),
  the "8 months before" math is wrong and _base_send_date() should be adjusted.

  EUROPEAN DETECTION: inferred from website/domain TLD (.de, .fr, .uk, etc.).
  Works for the majority of European festivals. An explicit 'is_european' boolean
  in the queue sheet would be more reliable if TLD inference proves wrong for
  edge cases (e.g. .com domains for German festivals).

  BUYER FESTIVAL DATES: looked up by matching hubspot_owner_id across all synced
  HubSpot companies. This approximates "all festivals this buyer manages" but is
  not guaranteed complete — a buyer may run festivals not yet in HubSpot.
"""

from __future__ import annotations

from datetime import date, timedelta
from typing import Optional

_EUROPEAN_TLDS = frozenset({
    'de', 'fr', 'uk', 'it', 'es', 'nl', 'be', 'ch', 'at', 'pt',
    'pl', 'cz', 'dk', 'fi', 'no', 'se', 'hu', 'ro', 'gr', 'hr',
    'sk', 'si', 'lt', 'lv', 'ee', 'ie', 'lu', 'mt', 'cy', 'eu',
})


def compute_send_date(
    festival_date: date,
    is_european: bool = False,
    buyer_festival_dates: Optional[list[date]] = None,
) -> date:
    """
    Compute the ideal Touch 1 send date from a festival date.
    Returns the result of applying all four rules in the specified order.
    """
    d = _base_send_date(festival_date)
    d = _european_blackout(d, is_european)
    d = _buyer_blackout(d, list(buyer_festival_dates or []))
    d = _tuesday_nudge(d)
    return d


def infer_is_european(domain: Optional[str] = None, website: Optional[str] = None) -> bool:
    """
    Infer European status from domain or website TLD.
    See module docstring for limitations.
    """
    for source in filter(None, [domain, website]):
        host = source.lower().strip().rstrip('/')
        if '://' in host:
            host = host.split('://', 1)[1]
        host = host.split('/')[0].split('?')[0]
        parts = host.split('.')
        if parts and parts[-1] in _EUROPEAN_TLDS:
            return True
        if len(parts) >= 2 and f'{parts[-2]}.{parts[-1]}' in {'co.uk', 'org.uk', 'me.uk'}:
            return True
    return False


def get_buyer_festival_dates(company, all_companies) -> list[date]:
    """
    Return reach_out_1 dates for other companies sharing this company's HubSpot owner.
    Used as a proxy for "other festivals this buyer manages."
    See module docstring for limitations.
    """
    if not company.hubspot_owner_id:
        return []
    today = date.today()
    return [
        c.reach_out_1
        for c in all_companies
        if (
            c.hubspot_owner_id == company.hubspot_owner_id
            and c.hubspot_id != company.hubspot_id
            and c.reach_out_1 is not None
            and c.reach_out_1 >= today
        )
    ]


# ── Rule implementations ────────────────────────────────────────────────────────

def _base_send_date(festival_date: date) -> date:
    """Rule 1: 1st of the month, 8 months before the festival month."""
    month = festival_date.month - 8
    year  = festival_date.year
    while month <= 0:
        month += 12
        year  -= 1
    return date(year, month, 1)


def _european_blackout(d: date, is_european: bool) -> date:
    """Rule 2: For European contacts, push any date July 15 – Aug 31 to Sept 1."""
    if not is_european:
        return d
    if (d.month == 7 and d.day >= 15) or d.month == 8:
        return date(d.year, 9, 1)
    return d


def _buyer_blackout(send_date: date, buyer_dates: list[date]) -> date:
    """
    Rule 3: Avoid the month immediately before any of the buyer's own festival dates.
    If the send_date falls in a blackout month, push to the 1st of that festival's
    own month. Re-checks after each push in case of cascading conflicts.
    """
    for _ in range(20):  # cap iterations against pathological inputs
        pushed = False
        for fd in buyer_dates:
            blackout_month = fd.month - 1 or 12
            blackout_year  = fd.year if fd.month > 1 else fd.year - 1
            if send_date.year == blackout_year and send_date.month == blackout_month:
                send_date = date(fd.year, fd.month, 1)
                pushed = True
                break
        if not pushed:
            break
    return send_date


def _tuesday_nudge(d: date) -> date:
    """
    Rule 4: Shift to the next upcoming Tuesday.
    If already Tuesday, keep it. Falls back to Thursday if the caller detects
    a conflict (add 2 days to the returned date).
    """
    weekday = d.weekday()  # 0=Mon 1=Tue 2=Wed 3=Thu 4=Fri 5=Sat 6=Sun
    if weekday == 1:
        return d
    days_ahead = (1 - weekday) % 7
    if days_ahead == 0:
        days_ahead = 7
    return d + timedelta(days=days_ahead)
