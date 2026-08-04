from datetime import datetime
from flask_login import UserMixin
from app.extensions import db


class User(db.Model, UserMixin):
    __tablename__ = 'users'

    id = db.Column(db.Integer, primary_key=True)
    email = db.Column(db.String(255), nullable=False, unique=True)
    name = db.Column(db.String(255))
    google_sub = db.Column(db.String(255), unique=True)
    role = db.Column(
        db.Enum('super_admin', 'editor', 'member'),
        nullable=False,
        default='member',
    )
    is_active = db.Column(db.Boolean, nullable=False, default=True)
    created_at = db.Column(db.DateTime, default=datetime.utcnow, nullable=False)
    last_login_at = db.Column(db.DateTime)

    project_permissions = db.relationship(
        'UserProjectPermission',
        backref='user',
        lazy=True,
        cascade='all, delete-orphan',
    )
    notification_pref = db.relationship(
        'NotificationPreference',
        backref='user',
        uselist=False,
        cascade='all, delete-orphan',
    )

    def get_id(self):
        return str(self.id)

    def has_project_access(self, project_slug: str) -> bool:
        if self.role == 'super_admin':
            return True
        return any(p.project_slug == project_slug for p in self.project_permissions)


class UserProjectPermission(db.Model):
    __tablename__ = 'user_project_permissions'

    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey('users.id'), nullable=False)
    project_slug = db.Column(db.String(100), nullable=False)
    created_at = db.Column(db.DateTime, default=datetime.utcnow, nullable=False)

    __table_args__ = (
        db.UniqueConstraint('user_id', 'project_slug', name='uq_user_project'),
    )


class NotificationPreference(db.Model):
    __tablename__ = 'notification_preferences'

    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey('users.id'), nullable=False, unique=True)
    cadence = db.Column(
        db.Enum('instant', 'daily', 'weekly', 'biweekly'),
        nullable=False,
        default='daily',
    )
    holds_enabled = db.Column(db.Boolean, nullable=False, default=True)
    approvals_enabled = db.Column(db.Boolean, nullable=False, default=True)
    pitch_updates_enabled = db.Column(db.Boolean, nullable=False, default=True)
    updated_at = db.Column(
        db.DateTime,
        default=datetime.utcnow,
        onupdate=datetime.utcnow,
        nullable=False,
    )
