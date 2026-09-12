from datetime import datetime
from app.extensions import db


class PitchApproval(db.Model):
    """
    All three touches per target exist as PitchApproval rows (touch_number 1/2/3).
    Touch 1 is approved interactively; approving Touch 1 auto-schedules Touch 2/3.
    process-queue fires each touch when its scheduled_at arrives.

    hubspot_contact_id stores the HubSpot *company* ID — festivals are COMPANY
    objects in HubSpot. The column name is a pre-existing artifact; don't rename.
    """
    __tablename__ = 'pitch_approvals'

    id                 = db.Column(db.Integer, primary_key=True)
    hubspot_contact_id = db.Column(db.String(100), nullable=False)  # HubSpot company ID; empty for non-HubSpot queue items
    company_name       = db.Column(db.String(500))
    pitch_type         = db.Column(db.String(50), nullable=False, default='Festival')  # Festival | WAA | Show Invite | PNW | Distribution
    touch_number       = db.Column(db.Integer, nullable=False, default=1)
    draft_subject      = db.Column(db.String(500))
    draft_body         = db.Column(db.Text, nullable=False)
    research_notes     = db.Column(db.Text)   # research brief produced alongside the draft
    to_email           = db.Column(db.String(500))  # blank at generation; Erich fills in
    cc_email           = db.Column(db.String(500))  # pre-filled to booking@orchestragold.com
    status             = db.Column(
        db.Enum('pending', 'approved', 'rejected', 'sent', 'cancelled_reply'),
        nullable=False,
        default='pending',
    )
    send_date        = db.Column(db.Date, nullable=True)
    sent_message_id  = db.Column(db.String(500), nullable=True)  # RFC Message-ID from Zoho after Touch 1 sends; used to thread Touch 2/3 in-reply-to
    approved_by      = db.Column(db.Integer, db.ForeignKey('users.id', ondelete='SET NULL'))
    approved_at      = db.Column(db.DateTime)
    sent_at          = db.Column(db.DateTime)
    error_message    = db.Column(db.Text)
    created_at    = db.Column(db.DateTime, nullable=False, default=datetime.utcnow)
    updated_at    = db.Column(
        db.DateTime,
        nullable=False,
        default=datetime.utcnow,
        onupdate=datetime.utcnow,
    )

    __table_args__ = (
        db.Index('idx_pa_status', 'status'),
        db.Index('idx_pa_contact', 'hubspot_contact_id'),
    )
