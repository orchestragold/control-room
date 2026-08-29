#!/usr/bin/env python3
"""
Portal smoke test — runs against the live production site.

Tests the full HTTP stack end-to-end: session auth → CSRF → approve POST.
This is the test that would have caught the "405 while 11 tests pass" CSRF failure.

Usage:
    1. Log into portal.orchestragold.com in your browser.
    2. Copy the session cookie value (DevTools → Application → Cookies → session).
    3. Paste it into scripts/.smoke_session (one line, the raw cookie value).
    4. Run: python3 scripts/smoke_test.py

⚠  This approves the first pending draft and queues a real email send.
   Only run when you have a draft you're ready to send.

   If there are no pending drafts, the test exits OK after verifying
   auth + CSRF without making any writes.

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


def post(path: str, cookie: str, data: dict) -> tuple[int, str]:
    encoded = urllib.parse.urlencode(data).encode('utf-8')
    req = urllib.request.Request(
        f'{BASE_URL}{path}',
        data=encoded,
        headers={
            'Cookie': f'session={cookie}',
            'Content-Type': 'application/x-www-form-urlencoded',
        },
        method='POST',
    )
    # Don't follow redirects — a 302 means success, a 403/405/500 is the failure.
    opener = urllib.request.build_opener(urllib.request.HTTPRedirectHandler())

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


def extract_first_approve_pid(html: str) -> str:
    m = re.search(r'action="[^"]*/approve/(\d+)"', html)
    return m.group(1) if m else ''


def extract_form_field(html: str, field_name: str, pid: str) -> str:
    """Extract an input value from the approve form."""
    # Handles: value="..." on input[name="field_name"]
    pattern = rf'name="{re.escape(field_name)}"\s[^>]*value="([^"]*)"'
    m = re.search(pattern, html)
    if m:
        return m.group(1)
    # Also try: value="..." name="field_name"
    pattern2 = rf'value="([^"]*)"\s[^>]*name="{re.escape(field_name)}"'
    m2 = re.search(pattern2, html)
    return m2.group(1) if m2 else ''


def extract_body(html: str, pid: str) -> str:
    """Extract draft body from the server-rendered contenteditable div."""
    pattern = rf'data-for="body-{re.escape(pid)}">(.*?)</div>'
    m = re.search(pattern, html, re.DOTALL)
    return m.group(1).strip() if m else ''


def check_routes(cookie: str) -> None:
    """GET each main route and fail if any returns 500."""
    print('Step 0: GET each main route (checking for import errors and crashes)...')
    for path in ROUTE_CHECKS:
        status, body = get(path, cookie)
        if status == 500:
            fail(
                f'GET {path} returned 500 — broken import or crash-on-load.\n'
                f'  Body preview: {body[:400]!r}'
            )
        ok(f'GET {path} → {status}')
    print()


def main() -> None:
    print(f'\n{BOLD}Portal smoke test{RESET}')
    print(f'Target: {BASE_URL}\n')

    cookie = load_cookie()

    # Step 0: verify no route returns 500
    check_routes(cookie)

    # Step 1: fetch the review page
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

    # Step 3: find a pending draft
    pid = extract_first_approve_pid(html)
    if not pid:
        warn('No pending drafts found on the review page.')
        print('\nAuth and CSRF handshake verified. Nothing to approve.\n')
        sys.exit(0)
    ok(f'Found pending draft pid={pid}')

    # Step 4: pull form field values from the review page
    to_email  = extract_form_field(html, 'to_email', pid)
    cc_email  = extract_form_field(html, 'cc_email', pid)
    subject   = extract_form_field(html, 'subject', pid)
    send_date = extract_form_field(html, 'send_date', pid)
    body      = extract_body(html, pid)

    if not to_email:
        fail(f'Draft pid={pid} has no to_email — cannot approve. Fix the draft first.')
    if not body:
        warn(f'Could not extract body for pid={pid} — using placeholder.')
        body = '(smoke test — body extraction failed)'

    print(f'  To: {to_email}')
    print(f'  Subject: {subject[:60]}')

    # Step 5: POST the approval
    print(f'\nStep 2: POST approve pid={pid}...')
    approve_path = f'/projects/orchestra-gold/pitch-machine/approve/{pid}'
    post_status, post_body = post(approve_path, cookie, {
        'csrf_token': csrf,
        'to_email':   to_email,
        'cc_email':   cc_email,
        'subject':    subject,
        'body':       body,
        'send_date':  send_date,
    })

    if post_status in (301, 302, 303):
        ok(f'Approve returned HTTP {post_status} (redirect) — success.')
    elif post_status == 200 and 'error' not in post_body.lower()[:300]:
        ok(f'Approve returned HTTP {post_status} — check for flash errors in the browser.')
    else:
        fail(
            f'Approve returned HTTP {post_status}.\n'
            f'  Body preview: {post_body[:300]!r}'
        )

    print(f'\n{GREEN}{BOLD}Smoke test passed.{RESET}\n')


if __name__ == '__main__':
    main()
