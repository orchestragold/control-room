from datetime import datetime

from flask import Blueprint, abort, redirect, render_template, request, url_for
from flask_login import current_user, login_required

from app.extensions import db

main_bp = Blueprint('main', __name__)


def _require_project(slug):
    if not current_user.has_project_access(slug):
        abort(403)


@main_bp.route('/')
def index():
    if current_user.is_authenticated:
        return redirect(url_for('main.dashboard'))
    return render_template('auth/login.html')


@main_bp.route('/dashboard')
@login_required
def dashboard():
    hour = datetime.now().hour
    if hour < 12:
        greeting = 'Good morning'
    elif hour < 17:
        greeting = 'Good afternoon'
    else:
        greeting = 'Good evening'
    name = current_user.name or current_user.email
    first_name = name.split()[0]
    return render_template('main/dashboard.html', greeting=greeting, first_name=first_name)


@main_bp.route('/projects/orchestra-gold/distribution')
@login_required
def distribution():
    _require_project('distribution')
    return render_template('main/placeholder.html',
        title='Distribution',
        description='Record store outreach pipeline — built after Pitch Machine.',
    )


@main_bp.route('/projects/orchestra-gold/scheduling')
@login_required
def scheduling():
    _require_project('scheduling')
    return render_template('main/placeholder.html',
        title='Scheduling',
        description='Hold dates, rehearsal availability, Google Calendar sync.',
    )


@main_bp.route('/projects/orchestra-gold/advance')
@login_required
def advance():
    _require_project('advance')
    return render_template('main/placeholder.html',
        title='Advance',
        description='Confirmed-show info hub — auto-populated from Pitch Machine.',
    )


@main_bp.route('/tools/posting-tool')
@login_required
def posting_tool():
    if current_user.role != 'super_admin':
        abort(403)
    return render_template('main/placeholder.html',
        title='Posting Tool',
        description='Cross-project content publishing — coming in Session K.',
    )


@main_bp.route('/set-mode', methods=['POST'])
@login_required
def set_mode():
    if current_user.role != 'super_admin':
        abort(403)
    from app.models.queue import AppSetting
    new_mode = 'live' if request.form.get('mode') == 'live' else 'test'
    setting = AppSetting.query.get('mode')
    if setting:
        setting.value = new_mode
    else:
        db.session.add(AppSetting(key_name='mode', value=new_mode))
    db.session.commit()
    return redirect(request.referrer or url_for('main.dashboard'))
