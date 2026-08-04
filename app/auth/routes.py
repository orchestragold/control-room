from datetime import datetime

from flask import Blueprint, flash, redirect, url_for
from flask_login import current_user, login_required, login_user, logout_user
from flask import current_app

from app.extensions import db, oauth
from app.models.user import NotificationPreference, User

auth_bp = Blueprint('auth', __name__, url_prefix='/auth')


def _google():
    return oauth.create_client('google')


@auth_bp.route('/login')
def login():
    if current_user.is_authenticated:
        return redirect(url_for('main.dashboard'))
    redirect_uri = url_for('auth.callback', _external=True)
    return _google().authorize_redirect(redirect_uri)


@auth_bp.route('/callback')
def callback():
    try:
        token = _google().authorize_access_token()
    except Exception:
        flash('Authentication failed. Please try again.')
        return redirect(url_for('main.index'))

    userinfo = token.get('userinfo') or {}
    email = userinfo.get('email', '').lower().strip()
    google_sub = userinfo.get('sub', '')

    if not email or not google_sub:
        flash('Could not retrieve account information from Google.')
        return redirect(url_for('main.index'))

    allowed = set(
        current_app.config.get('SUPER_ADMIN_EMAILS', [])
        + current_app.config.get('ALLOWED_EMAILS', [])
    )
    if email not in allowed:
        flash('Access denied. Your Google account is not authorized for Control Room.')
        return redirect(url_for('main.index'))

    # Find existing user by Google sub first, then fall back to email
    user = (
        User.query.filter_by(google_sub=google_sub).first()
        or User.query.filter_by(email=email).first()
    )

    if user is None:
        role = (
            'super_admin'
            if email in current_app.config.get('SUPER_ADMIN_EMAILS', [])
            else 'member'
        )
        user = User(
            email=email,
            name=userinfo.get('name'),
            google_sub=google_sub,
            role=role,
        )
        db.session.add(user)
        db.session.flush()
        db.session.add(NotificationPreference(user_id=user.id))
    else:
        user.google_sub = google_sub
        user.name = userinfo.get('name', user.name)
        user.last_login_at = datetime.utcnow()

    db.session.commit()
    login_user(user)
    return redirect(url_for('main.dashboard'))


@auth_bp.route('/logout')
@login_required
def logout():
    logout_user()
    return redirect(url_for('main.index'))
