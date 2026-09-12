from flask import Flask

from .config import Config
from .extensions import csrf, db, login_manager, oauth


def create_app(config_class=Config):
    app = Flask(__name__)
    app.config.from_object(config_class)

    db.init_app(app)
    login_manager.init_app(app)
    csrf.init_app(app)
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

    @app.cli.command('sync-pitch-targets')
    def sync_pitch_targets_command():
        """Rebuild pitch_targets from HubSpot cache, spreadsheet XLSX, and queue CSV."""
        from app.pitch_machine.pitch_target_sync import sync_pitch_targets
        try:
            result = sync_pitch_targets()
            print(str(result))
            for w in result.warnings:
                print(f'  WARNING: {w}')
        except Exception as e:
            print(f'Error: {e}')
            raise

    # ── Pitch Machine CLI commands — business logic lives in pitch_machine/cli.py ──
    from app.pitch_machine.cli import register_pitch_machine_cli
    register_pitch_machine_cli(app, db)

    @app.cli.command('debug-zoho-sent')
    def debug_zoho_sent_command():
        """
        Diagnostic: dump Zoho folders + raw Sent messages without any filtering.
        Helps identify why a specific message is missing from scan-sent output.
        """
        import re as _re
        import requests as _requests
        from app.integrations.zoho_mail import (
            ZohoError, _get_access_token, get_account_id,
        )

        access_token = _get_access_token()
        account_id   = get_account_id(access_token)
        headers      = {'Authorization': f'Zoho-oauthtoken {access_token}'}

        # 1. List all folders so we can confirm which one we're reading
        folders_resp = _requests.get(
            f'https://mail.zoho.com/api/accounts/{account_id}/folders',
            headers=headers, timeout=15,
        )
        folders_resp.raise_for_status()
        print('=== ZOHO FOLDERS ===')
        for f in folders_resp.json().get('data', []):
            print(f'  id={f["folderId"]}  name={f.get("folderName")!r}  count={f.get("messageCount")}')

        sent_folder_id = next(
            (f['folderId'] for f in folders_resp.json().get('data', [])
             if f.get('folderName', '').lower() == 'sent'),
            None,
        )
        if not sent_folder_id:
            print('ERROR: No folder named "sent" found.')
            return

        # 2. Fetch raw messages — no timestamp filter, no matching
        print(f'\n=== RAW SENT MESSAGES (folder {sent_folder_id}, first 200) ===')
        resp = _requests.get(
            f'https://mail.zoho.com/api/accounts/{account_id}/messages/view',
            headers=headers,
            params={'folderId': sent_folder_id, 'limit': 200, 'start': 0},
            timeout=30,
        )
        resp.raise_for_status()
        raw_msgs = resp.json().get('data', [])
        print(f'API returned {len(raw_msgs)} messages in this page.')

        email_pat = _re.compile(r'[\w._%+\-]+@[\w.\-]+\.[a-zA-Z]{2,}')
        print('\nAll messages (ts / to_address / subject):')
        for msg in raw_msgs:
            ts      = msg.get('sentDateInGMT') or msg.get('receivedTime') or '0'
            to_raw  = msg.get('toAddress', '')
            m       = email_pat.search(to_raw)
            to_addr = m.group(0).lower() if m else to_raw[:60]
            subj    = msg.get('subject', '')[:70]
            print(f'  ts={ts}  to={to_addr}  subj={subj!r}')

    @app.cli.command('seed-pitch-types')
    def seed_pitch_types_command():
        """Seed or reset pitch_type_configs to the built-in defaults. Skips existing rows."""
        from app.models.pitch_config import PitchTypeConfig
        from app.pitch_machine.default_pitch_types import DEFAULT_PITCH_TYPES
        added = 0
        for data in DEFAULT_PITCH_TYPES:
            if PitchTypeConfig.query.filter_by(name=data['name']).first() is None:
                db.session.add(PitchTypeConfig(**data))
                added += 1
                print(f'  Added: {data["name"]}')
            else:
                print(f'  Skipped (exists): {data["name"]}')
        db.session.commit()
        print(f'Done — {added} added.')

    @app.cli.command('sync-knowledge')
    def sync_knowledge_command():
        """Pull Dropbox knowledge files into the local cache. Safe to re-run."""
        from app.integrations.dropbox_sync import sync_knowledge_to_cache, DropboxError
        try:
            results = sync_knowledge_to_cache()
            for path, chars in results.items():
                name = path.split('/')[-1]
                print(f'  {name}: {chars:,} chars')
            print(f'Knowledge sync complete ({len(results)} files).')
        except DropboxError as e:
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

    # Seed pitch type configs on first boot (table empty = fresh install or reset)
    from .models.pitch_config import PitchTypeConfig
    if PitchTypeConfig.query.count() == 0:
        from .pitch_machine.default_pitch_types import DEFAULT_PITCH_TYPES
        for data in DEFAULT_PITCH_TYPES:
            db.session.add(PitchTypeConfig(**data))
        db.session.commit()
