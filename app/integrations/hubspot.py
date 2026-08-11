"""
HubSpot integration — Company read/write for Pitch Machine.

All outbound calls go through the shared throttle layer (app/core/throttle.py).
The UI never hits HubSpot directly — it reads from the local hubspot_companies cache.
The cache is populated by sync_companies_to_cache(), called by cron and manually via
`flask sync-hubspot`.

Property name notes (corrected from scoping doc):
  reach_out_1         — Touch 1 planned send date (DATE string, e.g. "2026-11-02")
  reach_out_2_checkin — Touch 2 check-in date (+14 days from Touch 1)
  reach_out_2         — Touch 3 close-out date (label: "Reach Out #3 (Close-out)")
  hs_lead_status      — Standard HubSpot enum tracking pipeline state
                        (see STAGE_MAP in pitch_machine/stages.py — built in Session C)

Legacy record protection: 107 Company records had reach_out_1/reach_out_2 pre-populated
from an old planning calendar and must never be overwritten. Enforcement lives in
the kanban view layer, not here — this module does what it's told.
"""

from __future__ import annotations

from datetime import date, datetime
from typing import Any, Optional

import requests
from flask import current_app

from app.core.throttle import APIThrottle
from app.extensions import db


# Properties fetched from HubSpot for every Company in the cache sync.
_SYNC_PROPERTIES = [
    'name',
    'description',
    'website',
    'domain',
    'hubspot_owner_id',
    'reach_out_1',
    'reach_out_2_checkin',
    'reach_out_2',
    'hs_lead_status',
    'lifecyclestage',
    'notes_last_contacted',
    'hs_lastmodifieddate',
]

_PAGE_SIZE = 100
_BASE_URL = 'https://api.hubapi.com'


class HubSpotError(Exception):
    """Raised for API errors or configuration problems."""


class HubSpotClient:
    """
    Thin wrapper around HubSpot's Companies v3 API.
    Instantiate inside a Flask app context.
    """

    def __init__(self):
        token = current_app.config.get('HUBSPOT_API_KEY')
        if not token:
            raise HubSpotError('HUBSPOT_API_KEY is not configured')
        self._token = token
        self._throttle = APIThrottle('hubspot')

    # ── Internal helpers ────────────────────────────────────────────────────────

    def _headers(self) -> dict:
        return {
            'Authorization': f'Bearer {self._token}',
            'Content-Type': 'application/json',
        }

    def _get(self, path: str, params: dict | None = None) -> dict:
        if not self._throttle.can_call():
            raise HubSpotError(
                'HubSpot rate limit reached — wait a moment and try again'
            )
        self._throttle.record_call()
        resp = requests.get(
            f'{_BASE_URL}{path}',
            headers=self._headers(),
            params=params,
            timeout=20,
        )
        resp.raise_for_status()
        return resp.json()

    def _patch(self, path: str, body: dict) -> dict:
        if not self._throttle.can_call():
            raise HubSpotError(
                'HubSpot rate limit reached — wait a moment and try again'
            )
        self._throttle.record_call()
        resp = requests.patch(
            f'{_BASE_URL}{path}',
            headers=self._headers(),
            json=body,
            timeout=20,
        )
        resp.raise_for_status()
        return resp.json()

    # ── Public API ──────────────────────────────────────────────────────────────

    def fetch_all_companies(self) -> list[dict[str, Any]]:
        """
        Fetch every non-archived Company with pitch-relevant properties.
        Handles cursor-based pagination automatically.
        One API call per page (~6 calls for 521 companies).
        """
        results: list[dict] = []
        after: Optional[str] = None

        while True:
            params: dict[str, Any] = {
                'limit': _PAGE_SIZE,
                'properties': ','.join(_SYNC_PROPERTIES),
                'archived': 'false',
            }
            if after:
                params['after'] = after

            data = self._get('/crm/v3/objects/companies', params)
            results.extend(data.get('results', []))

            after = data.get('paging', {}).get('next', {}).get('after')
            if not after:
                break

        return results

    def update_company(self, hubspot_id: str, properties: dict) -> dict:
        """
        Write properties back to a HubSpot Company record.
        Used by the kanban when a card is manually moved between stages.

        Only pass the properties you want to change — HubSpot merges them.
        Never pass reach_out_1 for a company that already has it set
        (legacy-record protection is enforced by the caller).
        """
        return self._patch(
            f'/crm/v3/objects/companies/{hubspot_id}',
            {'properties': properties},
        )


# ── Cache sync ──────────────────────────────────────────────────────────────────

def sync_companies_to_cache() -> int:
    """
    Pull all HubSpot companies into hubspot_companies (the local DB cache).
    Upserts on hubspot_id — safe to call repeatedly.
    Returns the number of records written.

    Called by:
      - `flask sync-hubspot` (manual)
      - The nightly cron job (automated, once background tasks are wired)
    """
    from app.models.hubspot_cache import HubSpotCompany

    client = HubSpotClient()
    raw_companies = client.fetch_all_companies()
    now = datetime.utcnow()
    count = 0

    for item in raw_companies:
        hs_id = str(item['id'])
        props = item.get('properties', {})

        record = HubSpotCompany.query.filter_by(hubspot_id=hs_id).first()
        if record is None:
            record = HubSpotCompany(hubspot_id=hs_id)
            db.session.add(record)

        record.name                = props.get('name') or ''
        record.description         = props.get('description')
        record.website             = props.get('website')
        record.domain              = props.get('domain')
        record.hubspot_owner_id    = props.get('hubspot_owner_id')
        record.reach_out_1         = _parse_date(props.get('reach_out_1'))
        record.reach_out_2_checkin = _parse_date(props.get('reach_out_2_checkin'))
        record.reach_out_2         = _parse_date(props.get('reach_out_2'))
        record.hs_lead_status      = props.get('hs_lead_status')
        record.lifecyclestage      = props.get('lifecyclestage')
        record.notes_last_contacted = _parse_dt(props.get('notes_last_contacted'))
        record.hs_lastmodifieddate  = _parse_dt(props.get('hs_lastmodifieddate'))
        record.last_synced_at      = now
        count += 1

    db.session.commit()
    return count


def get_cached_companies(stale_after_hours: int = 4) -> list:
    """
    Return companies from the local cache.
    Raises HubSpotError if the cache is empty (sync has never run) or stale.
    Stale = last_synced_at is older than stale_after_hours.
    The kanban view calls this; it never hits HubSpot directly.
    """
    from app.models.hubspot_cache import HubSpotCompany

    companies = HubSpotCompany.query.order_by(HubSpotCompany.name).all()
    if not companies:
        raise HubSpotError(
            'HubSpot cache is empty — run `flask sync-hubspot` to populate it'
        )

    most_recent = max(c.last_synced_at for c in companies)
    age_hours = (datetime.utcnow() - most_recent).total_seconds() / 3600
    if age_hours > stale_after_hours:
        raise HubSpotError(
            f'HubSpot cache is stale ({age_hours:.0f}h old) — '
            f'run `flask sync-hubspot` or wait for the next cron sync'
        )

    return companies


# ── Date parsing helpers ────────────────────────────────────────────────────────

def _parse_date(value: Any) -> Optional[date]:
    if not value:
        return None
    try:
        return datetime.strptime(str(value), '%Y-%m-%d').date()
    except (ValueError, TypeError):
        return None


def _parse_dt(value: Any) -> Optional[datetime]:
    if not value:
        return None
    try:
        return datetime.fromisoformat(str(value).replace('Z', '+00:00')).replace(tzinfo=None)
    except (ValueError, TypeError):
        return None
