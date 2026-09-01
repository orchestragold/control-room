#!/usr/bin/env python3
"""
Portal smoke test — runs against the live production site.

Tests the full HTTP stack end-to-end: session auth → CSRF → Referer → routing.
This is the test that would have caught the "405 while 11 tests pass" CSRF failure.

Usage:
    1. Log into portal.orchestragold.com in your browser.
    2. Copy the session cookie value (DevTools → Application → Cookies → session).
    3. Paste it into scripts/.smoke_session (one line, the raw cookie value).
    4. Run: python3 scripts/smoke_test.py

Safe to run any number of times — makes no writes, sends no email.
The approve step POSTs to a deliberately invalid pid and asserts 404,
proving routing, auth, CSRF, and the Referer header all work end-to-end.

Cookie file: scripts/.smoke_session (gitignored — never commit it).
"""

import re
import sys
from pathlib import Path
import urllib.request
import urllib.error
import urllib.parse

BASE_URL = 'https://portal.orchestragold.com'
REVIEW_PATH = '/projects/orchestra-gold/pitch-machine/review'
COOKIE_FILE = Path(__file__).parent / '.smoke_session'

# Routes checked on every deploy — any 500 here means a broken import or
# crash-on-load that the unit tests can't see.
ROUTE_CHECKS = [
    '/projects/orchestra-gold/pitch-machine',
    '/projects/orchestra-gold/pitch-machine/wheel',
    '/projects/orchestra-gold/pitch-machine/draft-queue',
    '/projects/orchestra-gold/pitch-machine/review',
]

BOARD_PATH = '/projects/orchestra-gold/pitch-machine'
SYNC_PATH  = '/projects/orchestra-gold/pitch-machine/sync'

BOLD  = '\033[1m'
GREEN = '\033[32m'
RED   = '\033[31m'
YELLOW = '\033[33m'
RESET = '\033[0m'


def ok(msg: str) -> None:
    print(f'{GREEN}✓{RESET} {msg}')


def fail(msg: str) -> None:
    print(f'{RED}✗ FAIL:{RESET} {msg}')
    sys.exit(1)


def warn(msg: str) -> None:
    print(f'{YELLOW}!{RESET} {msg}')


def load_cookie() -> str:
    if not COOKIE_FILE.exists():
        fail(
            f'{COOKIE_FILE} not found.\n'
            '  1. Log into portal.orchestragold.com\n'
            '  2. DevTools → Application → Cookies → "session"\n'
            '  3. Paste the value into scripts/.smoke_session'
        )
    value = COOKIE_FILE.read_text().strip()
    if not value:
        fail(f'{COOKIE_FILE} is empty.')
    return value


def get(path: str, cookie: str) -> tuple[int, str]:
    req = urllib.request.Request(
        f'{BASE_URL}{path}',
        headers={'Cookie': f'session={cookie}'},
    )
    try:
        with urllib.request.urlopen(req) as resp:
            return resp.status, resp.read().decode('utf-8', errors='replace')
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode('utf-8', errors='replace')


def post_json(path: str, cookie: str, csrf: str) -> tuple[int, str]:
    """POST with X-CSRFToken + Referer (Flask-WTF requires Referer for header-mode CSRF)."""
    req = urllib.request.Request(
        f'{BASE_URL}{path}',
        data=b'{}',
        headers={
            'Cookie': f'session={cookie}',
            'Content-Type': 'application/json',
            'X-CSRFToken': csrf,
            'Referer': BASE_URL + path,
        },
        method='POST',
    )
    try:
        with urllib.request.urlopen(req) as resp:
            return resp.status, resp.read().decode('utf-8', errors='replace')
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode('utf-8', errors='replace')


def post(path: str, cookie: str, data: dict) -> tuple[int, str]:
    encoded = urllib.parse.urlencode(data).encode('utf-8')
    req = urllib.request.Request(
        f'{BASE_URL}{path}',
        data=encoded,
        headers={
            'Cookie': f'session={cookie}',
            'Content-Type': 'application/x-www-form-urlencoded',
            # Flask-WTF requires Referer on HTTPS when WTF_CSRF_SSL_STRICT=True (default).
            # Without it the request gets a 400 "referrer header is missing" before
            # the route even runs — same failure shape as the original CSRF 403.
            'Referer': BASE_URL + path,
        },
        method='POST',
    )
    # Don't follow redirects — a 302 means success, a 4xx is the failure.
    class NoRedirect(urllib.request.HTTPRedirectHandler):
        def redirect_request(self, *args, **kwargs):
            return None

    opener = urllib.request.build_opener(NoRedirect())
    try:
        with opener.open(req) as resp:
            return resp.status, resp.read().decode('utf-8', errors='replace')
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode('utf-8', errors='replace')


def extract_meta(html: str, name: str) -> str:
    m = re.search(rf'<meta\s+name="{re.escape(name)}"\s+content="([^"]*)"', html)
    return m.group(1) if m else ''



def check_routes(cookie: str, csrf: str) -> None:
    """GET each main route; POST to sync. Fail on any 500."""
    print('Step 0: GET each main route (checking for import errors and crashes)...')
    for path in ROUTE_CHECKS:
        status, body = get(path, cookie)
        if status == 500:
            fail(
                f'GET {path} returned 500 — broken import or crash-on-load.\n'
                f'  Body preview: {body[:400]!r}'
            )
        ok(f'GET {path} → {status}')

    # POST to sync — this path is never a GET, so the route checks above miss it.
    # A non-JSON response (HTML error page) means the route 500d.
    sync_status, sync_body = post_json(SYNC_PATH, cookie, csrf)
    if sync_status == 500:
        fail(
            f'POST {SYNC_PATH} returned 500.\n'
            f'  Body preview: {sync_body[:400]!r}'
        )
    try:
        import json as _json
        _json.loads(sync_body)
        ok(f'POST {SYNC_PATH} → {sync_status} (JSON)')
    except Exception:
        fail(
            f'POST {SYNC_PATH} returned {sync_status} but body is not JSON '
            f'— server likely returned an HTML error page.\n'
            f'  Body preview: {sync_body[:400]!r}'
        )
    print()


def main() -> None:
    print(f'\n{BOLD}Portal smoke test{RESET}')
    print(f'Target: {BASE_URL}\n')

    cookie = load_cookie()

    # Step 1: fetch the review page (also provides the CSRF token for all checks)
    print('Step 1: GET review page...')
    status, html = get(REVIEW_PATH, cookie)

    if status == 302 or 'login' in html.lower()[:500]:
        fail('Session cookie rejected — looks like a redirect to login. Refresh the cookie.')
    if status != 200:
        fail(f'Review page returned HTTP {status} (expected 200).')
    ok(f'Review page: HTTP {status}')

    # Step 2: extract CSRF token
    csrf = extract_meta(html, 'csrf-token')
    if not csrf:
        fail('Could not extract CSRF token from <meta name="csrf-token">.')
    ok(f'CSRF token: {csrf[:12]}…')

    # Step 2b: route checks — GET pages + POST sync (now that we have a CSRF token)
    check_routes(cookie, csrf)

    # Step 3: POST to a deliberately invalid pid.
    # A 404 proves routing, auth, CSRF token, AND the Referer header all worked —
    # the request got through every gate, just found no record to act on.
    # A 403 or 400 means CSRF/Referer is still broken.
    # A 405 means the route method wiring is wrong.
    # This is safe to run any number of times — no draft is approved, no mail is sent.
    INVALID_PID = 99999999
    approve_path = f'/projects/orchestra-gold/pitch-machine/approve/{INVALID_PID}'
    print(f'\nStep 2: POST approve pid={INVALID_PID} (invalid — expects 404)...')
    post_status, post_body = post(approve_path, cookie, {
        'csrf_token': csrf,
        'to_email':   'smoke@test.invalid',
        'cc_email':   '',
        'subject':    'Smoke test — should never send',
        'body':       '<p>Smoke test body</p>',
        'send_date':  '',
    })

    if post_status == 404:
        ok(f'Approve returned HTTP 404 — routing, auth, CSRF and Referer all verified.')
    elif post_status in (400, 403):
        fail(
            f'Approve returned HTTP {post_status} — CSRF or Referer check failed.\n'
            f'  Body preview: {post_body[:300]!r}'
        )
    elif post_status == 405:
        fail(
            f'Approve returned HTTP 405 — route method wiring is broken.\n'
            f'  Body preview: {post_body[:200]!r}'
        )
    else:
        fail(
            f'Approve returned unexpected HTTP {post_status} (expected 404).\n'
            f'  Body preview: {post_body[:300]!r}'
        )

    print(f'\n{GREEN}{BOLD}Smoke test passed.{RESET}\n')


if __name__ == '__main__':
    main()
