from datetime import datetime
from app.extensions import db


class AppSetting(db.Model):
    __tablename__ = 'app_settings'

    key_name = db.Column(db.String(100), primary_key=True)
    value = db.Column(db.Text)
    updated_at = db.Column(
        db.DateTime,
        default=datetime.utcnow,
        onupdate=datetime.utcnow,
        nullable=False,
    )
    updated_by = db.Column(db.Integer, db.ForeignKey('users.id', ondelete='SET NULL'))


class APIRateTracking(db.Model):
    __tablename__ = 'api_rate_tracking'

    id = db.Column(db.Integer, primary_key=True)
    platform = db.Column(db.String(100), nullable=False)
    window_start = db.Column(db.DateTime, nullable=False)
    call_count = db.Column(db.Integer, nullable=False, default=0)

    __table_args__ = (
        db.Index('idx_platform_window', 'platform', 'window_start'),
    )


class APITaskQueue(db.Model):
    __tablename__ = 'api_task_queue'

    id = db.Column(db.Integer, primary_key=True)
    platform = db.Column(db.String(100), nullable=False)
    task_type = db.Column(db.String(100), nullable=False)
    payload = db.Column(db.JSON, nullable=False)
    status = db.Column(
        db.Enum('pending', 'processing', 'completed', 'failed', 'cancelled'),
        nullable=False,
        default='pending',
    )
    priority = db.Column(db.Integer, nullable=False, default=5)
    retry_count = db.Column(db.Integer, nullable=False, default=0)
    max_retries = db.Column(db.Integer, nullable=False, default=3)
    scheduled_at = db.Column(db.DateTime)
    started_at = db.Column(db.DateTime)
    completed_at = db.Column(db.DateTime)
    error_message = db.Column(db.Text)
    created_by = db.Column(db.Integer, db.ForeignKey('users.id', ondelete='SET NULL'))
    created_at = db.Column(db.DateTime, default=datetime.utcnow, nullable=False)

    __table_args__ = (
        db.Index('idx_queue_status_scheduled', 'status', 'scheduled_at'),
        db.Index('idx_queue_platform', 'platform'),
    )


class ApprovalLog(db.Model):
    __tablename__ = 'approval_logs'

    id = db.Column(db.Integer, primary_key=True)
    approver_id = db.Column(db.Integer, db.ForeignKey('users.id', ondelete='SET NULL'))
    action = db.Column(db.String(100), nullable=False)
    entity_type = db.Column(db.String(100), nullable=False)
    entity_id = db.Column(db.String(255))
    details = db.Column(db.JSON)
    created_at = db.Column(db.DateTime, default=datetime.utcnow, nullable=False)

    __table_args__ = (
        db.Index('idx_approver', 'approver_id'),
        db.Index('idx_entity', 'entity_type', 'entity_id'),
        db.Index('idx_approval_created_at', 'created_at'),
    )
