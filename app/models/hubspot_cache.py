from datetime import datetime
from app.extensions import db


class HubSpotCompany(db.Model):
    """
    Local cache of HubSpot Company objects — populated by sync_companies_to_cache().
    The kanban reads from here; never hits HubSpot directly on page load.

    reach_out_* fields are DATE values (not timestamps) — they represent planned
    send dates. Past date = outreach was due; future date = scheduled upcoming.
    """
    __tablename__ = 'hubspot_companies'

    id                  = db.Column(db.Integer, primary_key=True)
    hubspot_id          = db.Column(db.String(50), nullable=False, unique=True)
    name                = db.Column(db.String(500), nullable=False, default='')
    description         = db.Column(db.Text)
    website             = db.Column(db.String(500))
    domain              = db.Column(db.String(255))
    hubspot_owner_id    = db.Column(db.String(50))

    # Pitch Machine touch dates (all DATE, not DATETIME)
    reach_out_1         = db.Column(db.Date)   # Touch 1 planned send date
    reach_out_2_checkin = db.Column(db.Date)   # Touch 2 check-in (+14 days)
    reach_out_2         = db.Column(db.Date)   # Touch 3 close-out (HubSpot label: "Reach Out #3")

    hs_lead_status      = db.Column(db.String(100))
    lifecyclestage      = db.Column(db.String(100))
    notes_last_contacted = db.Column(db.DateTime)
    hs_lastmodifieddate  = db.Column(db.DateTime)
    last_synced_at      = db.Column(db.DateTime, nullable=False, default=datetime.utcnow)

    __table_args__ = (
        db.Index('idx_hsc_reach_out_1', 'reach_out_1'),
        db.Index('idx_hsc_lead_status', 'hs_lead_status'),
        db.Index('idx_hsc_synced', 'last_synced_at'),
    )

    @property
    def is_duplicate(self) -> bool:
        """Records prefixed '[DUPLICATE' are legacy entries — display-only, never act on them."""
        return self.name.startswith('[DUPLICATE')

    @property
    def touch1_is_past(self) -> bool:
        return self.reach_out_1 is not None and self.reach_out_1 <= datetime.utcnow().date()

    @property
    def touch2_is_past(self) -> bool:
        return self.reach_out_2_checkin is not None and self.reach_out_2_checkin <= datetime.utcnow().date()

    @property
    def touch3_is_past(self) -> bool:
        return self.reach_out_2 is not None and self.reach_out_2 <= datetime.utcnow().date()
