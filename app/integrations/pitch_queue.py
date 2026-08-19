"""
Pitch queue — shared multi-source queue stored as CSV in Dropbox.

  App folder path (Dropbox API): /pitch_queue.csv
  Mac path (Cowork sessions):    ~/Dropbox/Apps/PortalKnowledgeSync/pitch_queue.csv

Cowork sessions read/write the file directly via the local filesystem.
The Portal reads via Dropbox API download and writes via Dropbox API upload.

Column contract (decision #8 — extends Festival Outreach spreadsheet columns):
  name          — festival or target name
  pitch_type    — Festival | WAA | Show Invite | PNW | Distribution
  source        — hubspot | cowork | spreadsheet
  deadline      — festival's own date (YYYY-MM-DD); anchor for the send-date algorithm.
                  ASSUMPTION: treated as the festival date from which Touch 1 send date
                  = 1st of month, 8 months prior. Correct _base_send_date() in
                  scheduling.py if this interpretation is wrong.
  status        — queued | pitched | removed
  notes         — free-form human context only; must NOT contain email addresses
  date_added    — ISO datetime string
  hubspot_id    — HubSpot Company ID if this item maps to an existing record
  email_address — primary recipient address; single address, no fallback parsing from notes

Deadline rules (decision #9):
  - Cowork-chat additions: deadline REQUIRED, human-provided. Refuse without one.
  - Spreadsheet auto-pulls: default to 7 days out if no deadline exists.
  These are distinct rules — do not conflate.
"""

from __future__ import annotations

import csv
import io
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from typing import Optional

QUEUE_PATH = '/pitch_queue.csv'

COLUMNS = [
    'name', 'pitch_type', 'source', 'deadline',
    'status', 'notes', 'date_added', 'hubspot_id', 'email_address',
]

PITCH_TYPES = ('Festival', 'WAA', 'Show Invite', 'PNW', 'Distribution')
STATUSES    = ('queued', 'pitched', 'removed')


@dataclass
class QueueItem:
    name:          str
    pitch_type:    str            = 'Festival'
    source:        str            = 'cowork'
    deadline:      Optional[date] = None
    status:        str            = 'queued'
    notes:         str            = ''
    date_added:    str            = ''
    hubspot_id:    str            = ''
    email_address: str            = ''

    @classmethod
    def from_row(cls, row: dict) -> 'QueueItem':
        return cls(
            name          = row.get('name', '').strip(),
            pitch_type    = row.get('pitch_type', 'Festival').strip() or 'Festival',
            source        = row.get('source', 'cowork').strip(),
            deadline      = _parse_date(row.get('deadline', '')),
            status        = row.get('status', 'queued').strip(),
            notes         = row.get('notes', '').strip(),
            date_added    = row.get('date_added', '').strip(),
            hubspot_id    = row.get('hubspot_id', '').strip(),
            email_address = row.get('email_address', '').strip(),
        )

    def to_row(self) -> dict:
        return {
            'name':          self.name,
            'pitch_type':    self.pitch_type,
            'source':        self.source,
            'deadline':      self.deadline.isoformat() if self.deadline else '',
            'status':        self.status,
            'notes':         self.notes,
            'date_added':    self.date_added or _now_iso(),
            'hubspot_id':    self.hubspot_id,
            'email_address': self.email_address,
        }


def parse_queue(csv_content: str) -> list[QueueItem]:
    """Parse queue CSV content into QueueItems. Tolerates extra columns."""
    reader = csv.DictReader(io.StringIO(csv_content))
    items = []
    for row in reader:
        item = QueueItem.from_row(row)
        if item.name:
            items.append(item)
    return items


def serialize_queue(items: list[QueueItem]) -> str:
    """Serialize queue items back to a CSV string."""
    out = io.StringIO()
    writer = csv.DictWriter(out, fieldnames=COLUMNS, lineterminator='\n', extrasaction='ignore')
    writer.writeheader()
    for item in items:
        writer.writerow(item.to_row())
    return out.getvalue()


def empty_queue_csv() -> str:
    """Return a CSV string with just the header row (for initial file creation)."""
    out = io.StringIO()
    csv.DictWriter(out, fieldnames=COLUMNS, lineterminator='\n').writeheader()
    return out.getvalue()


def spreadsheet_default_deadline() -> date:
    """Default deadline for auto-pulled spreadsheet items with no date (decision #9)."""
    return date.today() + timedelta(days=7)


def _parse_date(value: str) -> Optional[date]:
    if not value:
        return None
    for fmt in ('%Y-%m-%d', '%m/%d/%Y', '%Y/%m/%d', '%m/%d/%y', '%B %Y', '%b %Y'):
        try:
            return datetime.strptime(value.strip(), fmt).date()
        except ValueError:
            continue
    return None


def _now_iso() -> str:
    return datetime.utcnow().isoformat(timespec='seconds')
