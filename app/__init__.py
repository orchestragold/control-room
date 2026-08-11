from flask import Flask

from .config import Config
from .extensions import db, login_manager, oauth


def create_app(config_class=Config):
    app = Flask(__name__)
    app.config.from_object(config_class)

    db.init_app(app)
    login_manager.init_app(app)
    oauth.init_app(app)

    oauth.register(
        name='google',
        client_id=app.config['GOOGLE_CLIENT_ID'],
        client_secret=app.config['GOOGLE_CLIENT_SECRET'],
        server_metadata_url='https://accounts.google.com/.well-known/openid-configuration',
        client_kwargs={'scope': 'openid email profile'},
    )

    login_manager.login_view = 'auth.login'
    login_manager.login_message = ''

    @login_manager.user_loader
    def load_user(user_id):
        from .models.user import User
        return User.query.get(int(user_id))

    @app.context_processor
    def inject_globals():
        from app.core.mode import get_mode
        return {'mode': get_mode()}

    from .auth.routes import auth_bp
    from .main.routes import main_bp
    from .pitch_machine.routes import pm_bp
    app.register_blueprint(auth_bp)
    app.register_blueprint(main_bp)
    app.register_blueprint(pm_bp)

    with app.app_context():
        _auto_init_db(app)

    @app.cli.command('init-db')
    def init_db_command():
        """Create all tables and seed default settings. Safe to re-run."""
        _auto_init_db(app)
        print('Database initialised and default settings seeded.')

    @app.cli.command('sync-hubspot')
    def sync_hubspot_command():
        """Pull all HubSpot companies into the local cache. Safe to re-run."""
        from app.integrations.hubspot import sync_companies_to_cache, HubSpotError
        try:
            count = sync_companies_to_cache()
            print(f'Synced {count} companies from HubSpot.')
        except HubSpotError as e:
            print(f'Error: {e}')

    return app


def _auto_init_db(flask_app):
    """
    Create tables and seed defaults on first startup.
    db.create_all() is idempotent — safe to call on every boot.
    Needed because GoDaddy Deluxe shared hosting has no shell access,
    so there's no way to run 'flask init-db' manually.
    """
    import app.models  # noqa: F401 — registers all models before create_all
    db.create_all()

    from .models.queue import AppSetting
    if AppSetting.query.get('mode') is None:
        db.session.add(AppSetting(key_name='mode', value=flask_app.config.get('APP_MODE', 'test')))
    if AppSetting.query.get('version') is None:
        db.session.add(AppSetting(key_name='version', value='0.1.0'))
    db.session.commit()
