"""
Pitch Machine stage model.

Derives a kanban stage from a HubSpotCompany's hs_lead_status and reach_out dates.
Stage definitions are locked here; the kanban view and move API both import from here.

Erich's confirmed hs_lead_status → stage mapping (Session C-2 inspection):
  NEW                → Queued (researched and scheduled, pre-send)
  ATTEMPTED_TO_CONTACT → Sent / awaiting response
  CONNECTED          → In negotiation (real two-way engagement)
  BAD_TIMING         → Deprioritized (manual pull from active queue)
  UNQUALIFIED        → Declined
  OPEN_DEAL          → Confirmed
  OPEN / IN_PROGRESS → ⚠ Needs review (existing catch-all, 3 records; never set going forward)
  (none + reach_out_1 set) → Queued
  (none + no reach_out_1)  → Needs outreach
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Optional


class PMStage(str, Enum):
    NEEDS_OUTREACH = 'needs-outreach'
    QUEUED         = 'queued'
    SENT           = 'sent'
    IN_NEGOTIATION = 'in-negotiation'
    CONFIRMED      = 'confirmed'
    DEPRIORITIZED  = 'deprioritized'
    DECLINED       = 'declined'
    NEEDS_REVIEW   = 'needs-review'


@dataclass(frozen=True)
class StageConfig:
    label: str
    hs_status: Optional[str]  # value written to hs_lead_status on a card move
    col_class: str            # CSS modifier class on the column element


_CONFIGS: dict[PMStage, StageConfig] = {
    PMStage.NEEDS_OUTREACH: StageConfig('Needs outreach',   None,                   'col-neutral'),
    PMStage.QUEUED:         StageConfig('Queued',           'NEW',                  'col-neutral'),
    PMStage.SENT:           StageConfig('Sent',             'ATTEMPTED_TO_CONTACT', 'col-active'),
    PMStage.IN_NEGOTIATION: StageConfig('In negotiation',   'CONNECTED',            'col-warm'),
    PMStage.CONFIRMED:      StageConfig('Confirmed',        'OPEN_DEAL',            'col-confirmed'),
    PMStage.DEPRIORITIZED:  StageConfig('Deprioritized',    'BAD_TIMING',           'col-dim'),
    PMStage.DECLINED:       StageConfig('Declined',         'UNQUALIFIED',          'col-dim'),
    PMStage.NEEDS_REVIEW:   StageConfig('⚠ Needs review',   'OPEN',                 'col-review'),
}

# Left-to-right column order on the kanban board
STAGE_ORDER: list[PMStage] = [
    PMStage.NEEDS_OUTREACH,
    PMStage.QUEUED,
    PMStage.SENT,
    PMStage.IN_NEGOTIATION,
    PMStage.CONFIRMED,
    PMStage.DEPRIORITIZED,
    PMStage.DECLINED,
    PMStage.NEEDS_REVIEW,
]

# Valid drop targets for a card move.
# NEEDS_OUTREACH excluded: no single hs_lead_status maps back cleanly to "not yet started."
# NEEDS_REVIEW excluded: never set hs_lead_status = OPEN going forward per the
#   confident-only rule; existing OPEN records live there for manual reclassification.
ALLOWED_MOVE_TARGETS: frozenset[PMStage] = frozenset({
    PMStage.QUEUED,
    PMStage.SENT,
    PMStage.IN_NEGOTIATION,
    PMStage.CONFIRMED,
    PMStage.DEPRIORITIZED,
    PMStage.DECLINED,
})


def stage_config(stage: PMStage) -> StageConfig:
    return _CONFIGS[stage]


def get_stage(company) -> Optional[PMStage]:
    """
    Map a HubSpotCompany to its kanban stage.
    Returns None for [DUPLICATE] records — they're excluded from the board entirely.
    """
    if company.is_duplicate:
        return None

    s = company.hs_lead_status

    if s == 'ATTEMPTED_TO_CONTACT':
        return PMStage.SENT
    if s == 'CONNECTED':
        return PMStage.IN_NEGOTIATION
    if s == 'BAD_TIMING':
        return PMStage.DEPRIORITIZED
    if s == 'UNQUALIFIED':
        return PMStage.DECLINED
    if s == 'OPEN_DEAL':
        return PMStage.CONFIRMED
    if s in ('OPEN', 'IN_PROGRESS'):
        return PMStage.NEEDS_REVIEW
    # NEW or no status: QUEUED if reach_out_1 is set, otherwise NEEDS_OUTREACH
    if s == 'NEW' or company.reach_out_1 is not None:
        return PMStage.QUEUED
    return PMStage.NEEDS_OUTREACH
