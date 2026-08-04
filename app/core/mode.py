from flask import current_app


def get_mode() -> str:
    """
    Returns 'test' or 'live'.
    The DB value (app_settings.mode) takes precedence over the APP_MODE env var,
    so the UI can flip the switch without a redeploy. Falls back to env config
    if the DB isn't available yet (e.g. before init-db has run).
    """
    try:
        from app.models.queue import AppSetting
        setting = AppSetting.query.get('mode')
        if setting and setting.value in ('test', 'live'):
            return setting.value
    except Exception:
        pass
    return current_app.config.get('APP_MODE', 'test')


def is_test_mode() -> bool:
    return get_mode() == 'test'


def is_live_mode() -> bool:
    return get_mode() == 'live'


def get_test_redirect_email() -> str:
    return current_app.config.get('TEST_REDIRECT_EMAIL', 'orchestragold@gmail.com')


def resolve_email_recipient(intended_email: str) -> tuple[str, bool]:
    """
    Returns (actual_recipient, was_redirected).

    In test mode: redirects to orchestragold@gmail.com; callers should annotate
    the subject line with the original intended recipient so the redirect is
    clearly identifiable in the inbox.

    In live mode: passes the intended_email through unchanged.
    """
    if is_test_mode():
        return get_test_redirect_email(), True
    return intended_email, False
