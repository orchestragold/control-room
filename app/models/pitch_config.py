from datetime import datetime
from app.extensions import db


class PitchTypeConfig(db.Model):
    __tablename__ = 'pitch_type_configs'

    id                   = db.Column(db.Integer, primary_key=True)
    name                 = db.Column(db.String(100), nullable=False, unique=True)
    archive_dropbox_path = db.Column(db.String(255), nullable=False)
    prompt_template      = db.Column(db.Text, nullable=False)
    badge_color          = db.Column(db.String(7), nullable=False, default='#888888')
    active               = db.Column(db.Boolean, nullable=False, default=True)
    is_cyclical          = db.Column(db.Boolean, nullable=False, default=True)
    sort_order           = db.Column(db.Integer, nullable=False, default=0)

    # Multi-touch sequence config. Intervals are in days; null means sequence not
    # yet configured and Touch 2/3 will not be scheduled. Prompts describe HOW to
    # write each follow-up (not the body itself). Mark as unreviewed in UI until
    # Erich sets real values.
    touch1_to_touch2_days = db.Column(db.Integer, nullable=True)
    touch2_to_touch3_days = db.Column(db.Integer, nullable=True)
    touch2_prompt         = db.Column(db.Text, nullable=True)
    touch3_prompt         = db.Column(db.Text, nullable=True)

    created_at           = db.Column(db.DateTime, default=datetime.utcnow, nullable=False)
    updated_at           = db.Column(
        db.DateTime,
        default=datetime.utcnow,
        onupdate=datetime.utcnow,
        nullable=False,
    )

    __table_args__ = (
        db.Index('idx_ptc_active_sort', 'active', 'sort_order'),
    )

    @staticmethod
    def validate_template(template: str) -> str:
        """Return error message if template is invalid, empty string if valid."""
        try:
            template.format(name='', website='', description='')
            return ''
        except KeyError as e:
            return f'Unknown placeholder {e} — only {{name}}, {{website}}, {{description}} are allowed'
        except Exception as e:
            return f'Template error: {e}'
