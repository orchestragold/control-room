import os


class Config:
    SECRET_KEY = os.environ.get('SECRET_KEY', 'dev-key-change-before-deploy')
    SQLALCHEMY_DATABASE_URI = os.environ.get('DATABASE_URL', 'sqlite:///dev.db')
    SQLALCHEMY_TRACK_MODIFICATIONS = False
    SQLALCHEMY_ENGINE_OPTIONS = {
        'pool_pre_ping': True,   # drop and reconnect stale MySQL connections
        'pool_recycle': 280,     # recycle before MySQL's default 300s wait_timeout
    }

    # Secure cookies — disable only in local development (set FLASK_ENV=development)
    _is_dev = os.environ.get('FLASK_ENV') == 'development'
    SESSION_COOKIE_SECURE = not _is_dev
    SESSION_COOKIE_HTTPONLY = True
    SESSION_COOKIE_SAMESITE = 'Lax'

    # Google OAuth
    GOOGLE_CLIENT_ID = os.environ.get('GOOGLE_CLIENT_ID')
    GOOGLE_CLIENT_SECRET = os.environ.get('GOOGLE_CLIENT_SECRET')

    # Who can log in (checked at first sign-in, not on every request)
    SUPER_ADMIN_EMAILS = [
        e.strip().lower()
        for e in os.environ.get('SUPER_ADMIN_EMAILS', '').split(',')
        if e.strip()
    ]
    ALLOWED_EMAILS = [
        e.strip().lower()
        for e in os.environ.get('ALLOWED_EMAILS', '').split(',')
        if e.strip()
    ]

    # Test / live mode (DB value overrides this once the DB is seeded)
    APP_MODE = os.environ.get('APP_MODE', 'test')
    TEST_REDIRECT_EMAIL = os.environ.get('TEST_REDIRECT_EMAIL', 'orchestragold@gmail.com')

    # Editor-role grant at first login (comma-separated emails)
    EDITOR_EMAILS = [
        e.strip().lower()
        for e in os.environ.get('EDITOR_EMAILS', '').split(',')
        if e.strip()
    ]

    # Integration credentials (placeholders — values come from .env)
    HUBSPOT_API_KEY = os.environ.get('HUBSPOT_API_KEY')
    ASANA_ACCESS_TOKEN = os.environ.get('ASANA_ACCESS_TOKEN')
    GOOGLE_CALENDAR_ID = os.environ.get('GOOGLE_CALENDAR_ID')
    GOOGLE_SERVICE_ACCOUNT_JSON = os.environ.get('GOOGLE_SERVICE_ACCOUNT_JSON')
    MAILERLITE_API_KEY = os.environ.get('MAILERLITE_API_KEY')
    DROPBOX_APP_KEY = os.environ.get('DROPBOX_APP_KEY')
    DROPBOX_APP_SECRET = os.environ.get('DROPBOX_APP_SECRET')
    DROPBOX_REFRESH_TOKEN = os.environ.get('DROPBOX_REFRESH_TOKEN')

    # Anthropic (Claude API) — used for draft generation
    ANTHROPIC_API_KEY = os.environ.get('ANTHROPIC_API_KEY')

    # Zoho Mail OAuth — email provider for pitch sending (not Gmail).
    # Move these from the existing scheduled task's prompt into cPanel env vars.
    ZOHO_CLIENT_ID = os.environ.get('ZOHO_CLIENT_ID')
    ZOHO_CLIENT_SECRET = os.environ.get('ZOHO_CLIENT_SECRET')
    ZOHO_REFRESH_TOKEN = os.environ.get('ZOHO_REFRESH_TOKEN')
    ZOHO_FROM_EMAIL = os.environ.get('ZOHO_FROM_EMAIL')
    ZOHO_FROM_NAME = os.environ.get('ZOHO_FROM_NAME', 'Erich Huffaker')
    ZOHO_SIGNATURE_HTML = os.environ.get('ZOHO_SIGNATURE_HTML', '')
