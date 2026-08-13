from datetime import datetime
from app.extensions import db


class PitchApproval(db.Model):
    """
    Touch 1 pitch drafts awaiting Erich's explicit review and approval.
    Touch 2/3 auto-sends go through api_task_queue, not here.

    hubspot_contact_id stores the HubSpot *company* ID — festivals are COMPANY
    objects in HubSpot. The column name is a pre-existing artifact; don't rename.
    """
    __tablename__ = 'pitch_approvals'

    id                 = db.Column(db.Integer, primary_key=True)
    hubspot_contact_id = db.Column(db.String(100), nullable=False)  # HubSpot company ID
    company_name       = db.Column(db.String(500))
    touch_number       = db.Column(db.Integer, nullable=False, default=1)
    draft_subject      = db.Column(db.String(500))
    draft_body         = db.Column(db.Text, nullable=False)
    research_notes     = db.Column(db.Text)   # research brief produced alongside the draft
    to_email           = db.Column(db.String(500))  # blank at generation; Erich fills in
    cc_email           = db.Column(db.String(500))  # pre-filled to booking@orchestragold.com
    status             = db.Column(
        db.Enum('pending', 'approved', 'rejected', 'sent'),
        nullable=False,
        default='pending',
    )
    approved_by   = db.Column(db.Integer, db.ForeignKey('users.id', ondelete='SET NULL'))
    approved_at   = db.Column(db.DateTime)
    sent_at       = db.Column(db.DateTime)
    error_message = db.Column(db.Text)
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
