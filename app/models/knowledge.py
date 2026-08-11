from datetime import datetime
from app.extensions import db


class WarmContact(db.Model):
    """
    HubSpot property cap (10/10) means warm/cold can't be a new HubSpot property.
    Populated during the one-time historical gig spreadsheet import (Session F).
    Keyed to HubSpot contact ID.
    """
    __tablename__ = 'warm_contacts'

    id                 = db.Column(db.Integer, primary_key=True)
    hubspot_contact_id = db.Column(db.String(100), nullable=False, unique=True)
    tagged_at          = db.Column(db.DateTime, nullable=False, default=datetime.utcnow)
    tagged_by          = db.Column(db.Integer, db.ForeignKey('users.id', ondelete='SET NULL'))


class DropboxSync(db.Model):
    """
    Cache for Dropbox files pulled via the API (PITCH_MACHINE_RULES.md, etc.).
    Refreshed on a schedule by cron; re-fetched on demand before a research batch.
    """
    __tablename__ = 'dropbox_sync'

    id        = db.Column(db.Integer, primary_key=True)
    path      = db.Column(db.String(500), nullable=False, unique=True)
    content   = db.Column(db.Text(16_777_215))
    synced_at = db.Column(db.DateTime, nullable=False, default=datetime.utcnow)
