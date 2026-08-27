from datetime import datetime
from app.extensions import db


class PitchTargetOverride(db.Model):
    """
    User-set outreach date overrides for the Wheel.

    Keyed by hubspot_id (stable across pitch_targets DELETE+INSERT cycles).
    The sync engine applies these AFTER the atomic replace, so overrides
    survive every re-sync without touching HubSpot.

    Only HubSpot-linked targets can have overrides — name-only and CSV-only
    targets don't have a stable enough identity to key on.
    """
    __tablename__ = 'pitch_target_overrides'

    hubspot_id            = db.Column(db.String(50),  primary_key=True)
    outreach_date_override = db.Column(db.Date,        nullable=False)
    override_set_by       = db.Column(db.String(200),  nullable=False)
    override_set_at       = db.Column(db.DateTime,     nullable=False, default=datetime.utcnow)
