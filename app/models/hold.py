from datetime import datetime
from app.extensions import db


class Hold(db.Model):
    __tablename__ = 'holds'

    id = db.Column(db.Integer, primary_key=True)
    gcal_event_id = db.Column(db.String(255))
    title = db.Column(db.String(500), nullable=False)
    hold_date = db.Column(db.Date, nullable=False)
    end_date = db.Column(db.Date)
    status = db.Column(
        db.Enum('tentative', 'confirmed', 'cancelled'),
        nullable=False,
        default='tentative',
    )
    project_slug = db.Column(db.String(100))
    created_by = db.Column(db.Integer, db.ForeignKey('users.id', ondelete='SET NULL'))
    created_at = db.Column(db.DateTime, default=datetime.utcnow, nullable=False)
    updated_at = db.Column(
        db.DateTime,
        default=datetime.utcnow,
        onupdate=datetime.utcnow,
        nullable=False,
    )

    notes = db.relationship(
        'HoldNote',
        backref='hold',
        lazy=True,
        cascade='all, delete-orphan',
        order_by='HoldNote.created_at',
    )
    participants = db.relationship(
        'HoldParticipant',
        backref='hold',
        lazy=True,
        cascade='all, delete-orphan',
    )

    __table_args__ = (
        db.Index('idx_hold_date', 'hold_date'),
        db.Index('idx_hold_status', 'status'),
        db.Index('idx_gcal_event_id', 'gcal_event_id'),
    )


class HoldNote(db.Model):
    __tablename__ = 'hold_notes'

    id = db.Column(db.Integer, primary_key=True)
    hold_id = db.Column(db.Integer, db.ForeignKey('holds.id'), nullable=False)
    author_id = db.Column(db.Integer, db.ForeignKey('users.id', ondelete='SET NULL'))
    parent_note_id = db.Column(
        db.Integer, db.ForeignKey('hold_notes.id', ondelete='SET NULL')
    )
    body = db.Column(db.Text, nullable=False)
    created_at = db.Column(db.DateTime, default=datetime.utcnow, nullable=False)

    __table_args__ = (db.Index('idx_hold_notes_hold_id', 'hold_id'),)


class HoldParticipant(db.Model):
    __tablename__ = 'hold_participants'

    id = db.Column(db.Integer, primary_key=True)
    hold_id = db.Column(db.Integer, db.ForeignKey('holds.id'), nullable=False)
    user_id = db.Column(db.Integer, db.ForeignKey('users.id'), nullable=False)

    __table_args__ = (
        db.UniqueConstraint('hold_id', 'user_id', name='uq_hold_user'),
    )
