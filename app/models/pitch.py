from datetime import datetime
from app.extensions import db


class PitchApproval(db.Model):
    """
    Holds Touch 1 drafts pending Erich's explicit approval.
    Touch 2/3 auto-sends go through api_task_queue, not here.
    """
    __tablename__ = 'pitch_approvals'

    id                 = db.Column(db.Integer, primary_key=True)
    hubspot_contact_id = db.Column(db.String(100), nullable=False)
    touch_number       = db.Column(db.Integer, nullable=False, default=1)
    draft_subject      = db.Column(db.String(500))
    draft_body         = db.Column(db.Text, nullable=False)
    status             = db.Column(
        db.Enum('pending', 'approved', 'rejected', 'sent'),
        nullable=False,
        default='pending',
    )
    approved_by  = db.Column(db.Integer, db.ForeignKey('users.id', ondelete='SET NULL'))
    approved_at  = db.Column(db.DateTime)
    sent_at      = db.Column(db.DateTime)
    error_message = db.Column(db.Text)
    created_at   = db.Column(db.DateTime, nullable=False, default=datetime.utcnow)
    updated_at   = db.Column(
        db.DateTime,
        nullable=False,
        default=datetime.utcnow,
        onupdate=datetime.utcnow,
    )

    __table_args__ = (
        db.Index('idx_pa_status', 'status'),
        db.Index('idx_pa_contact', 'hubspot_contact_id'),
    )
