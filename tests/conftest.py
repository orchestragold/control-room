"""
Shared pytest fixtures for The Portal test suite.

Uses SQLite in-memory with StaticPool so all connections (including
the Flask CLI runner) see the same database state within a test.
"""
import pytest
from sqlalchemy.pool import StaticPool


@pytest.fixture(scope='session')
def app():
    from app import create_app
    from app.config import Config

    class TestConfig(Config):
        TESTING = True
        WTF_CSRF_ENABLED = False
        SECRET_KEY = 'test-secret-do-not-use'
        APP_MODE = 'test'
        TEST_REDIRECT_EMAIL = 'test@test.com'
        # In-memory SQLite shared across all connections in this test session
        SQLALCHEMY_DATABASE_URI = 'sqlite:///:memory:'
        SQLALCHEMY_ENGINE_OPTIONS = {
            'connect_args': {'check_same_thread': False},
            'poolclass': StaticPool,
        }
        # Fake credentials — no real external calls should happen in tests
        GOOGLE_CLIENT_ID = 'fake-client-id'
        GOOGLE_CLIENT_SECRET = 'fake-client-secret'
        HUBSPOT_API_KEY = 'fake-hubspot-key'
        DROPBOX_APP_KEY = 'fake-dropbox-key'
        DROPBOX_APP_SECRET = 'fake-dropbox-secret'
        DROPBOX_REFRESH_TOKEN = 'fake-dropbox-token'
        ZOHO_CLIENT_ID = 'fake-zoho-id'
        ZOHO_CLIENT_SECRET = 'fake-zoho-secret'
        ZOHO_REFRESH_TOKEN = 'fake-zoho-token'
        ZOHO_FROM_EMAIL = 'test@orchestragold.com'
        ANTHROPIC_API_KEY = 'fake-anthropic-key'

    flask_app = create_app(TestConfig)

    with flask_app.app_context():
        from app.extensions import db
        db.create_all()

    yield flask_app


@pytest.fixture(scope='session')
def db(app):
    from app.extensions import db as _db
    return _db


@pytest.fixture(autouse=True)
def clean_tables(app, db):
    """Wipe all rows between tests; keep schema intact."""
    yield
    with app.app_context():
        db.session.rollback()
        for table in reversed(db.metadata.sorted_tables):
            db.session.execute(table.delete())
        db.session.commit()


@pytest.fixture
def test_user(app, db):
    """A super_admin user with full project access."""
    from app.models.user import User
    with app.app_context():
        user = User(
            email='erich@test.com',
            name='Test User',
            google_sub='test-google-sub',
            role='super_admin',
            is_active=True,
        )
        db.session.add(user)
        db.session.commit()
        db.session.refresh(user)
        return user.id  # return id, not object — avoids detached-instance issues


@pytest.fixture
def client(app, test_user):
    """Test client with the test user already logged in."""
    c = app.test_client()
    with c.session_transaction() as sess:
        sess['_user_id'] = str(test_user)
        sess['_fresh'] = True
    return c


@pytest.fixture
def runner(app):
    return app.test_cli_runner()
