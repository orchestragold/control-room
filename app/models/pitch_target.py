"""
pitch_targets — materialized sync table rebuilt from all three sources.

Spec: /Apps/PortalKnowledgeSync/Session G-1 Spec - pitch_targets.md
Sync engine: app/pitch_machine/pitch_target_sync.py
CLI: flask sync-pitch-targets
"""

from __future__ import annotations

from datetime import datetime

from app.extensions import db


class PitchTarget(db.Model):
    __tablename__ = 'pitch_targets'

    id                  = db.Column(db.Integer,     primary_key=True)
    hubspot_id          = db.Column(db.String(50),  unique=True, nullable=True)
    name                = db.Column(db.String(500), nullable=False)

    # Which sources contributed to this record
    source_hubspot      = db.Column(db.Boolean, nullable=False, default=False)
    source_spreadsheet  = db.Column(db.Boolean, nullable=False, default=False)
    source_queue_csv    = db.Column(db.Boolean, nullable=False, default=False)

    # Canonical stage (HubSpot wins when present; see sync engine for logic)
    stage               = db.Column(db.String(50),  nullable=False, default='needs-outreach')
    stage_conflict      = db.Column(db.Boolean,     nullable=False, default=False)
    conflict_note       = db.Column(db.String(500), nullable=True)

    # Pitch data — best-available across sources
    pitch_type          = db.Column(db.String(100), nullable=True)
    website             = db.Column(db.String(500), nullable=True)
    description         = db.Column(db.Text,        nullable=True)
    reach_out_1         = db.Column(db.Date,        nullable=True)
    submission_deadline = db.Column(db.Date,        nullable=True)

    # Raw source data — preserved for display and stage recompute without JOINs
    spreadsheet_status  = db.Column(db.String(500), nullable=True)
    spreadsheet_row     = db.Column(db.Integer,     nullable=True)
    hs_lead_status      = db.Column(db.String(100), nullable=True)
    queue_csv_status    = db.Column(db.String(50),  nullable=True)

    # Queue CSV fields
    email_address       = db.Column(db.String(500), nullable=True)
    not_a_fit           = db.Column(db.Boolean,     nullable=False, default=False)
    not_a_fit_reason    = db.Column(db.String(500), nullable=True)

    last_synced_at      = db.Column(db.DateTime, nullable=False, default=datetime.utcnow)

    __table_args__ = (
        db.Index('idx_pt_stage',     'stage'),
        db.Index('idx_pt_name',      'name'),
        db.Index('idx_pt_not_a_fit', 'not_a_fit'),
        # hubspot_id: no separate index needed — the unique=True above already
        # creates one. A redundant KEY here wastes space without benefit.
    )
