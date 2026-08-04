from flask import Blueprint, redirect, render_template, url_for
from flask_login import current_user, login_required

from app.core.mode import get_mode

main_bp = Blueprint('main', __name__)


@main_bp.route('/')
def index():
    if current_user.is_authenticated:
        return redirect(url_for('main.dashboard'))
    return render_template('auth/login.html')


@main_bp.route('/dashboard')
@login_required
def dashboard():
    return render_template('index.html', mode=get_mode())
