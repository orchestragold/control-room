"""
One-shot Dropbox re-authorization script.
Run this to get a new refresh token that includes files.content.write scope.

Usage:
    python3 scripts/reauth_dropbox.py
"""

import urllib.parse
import urllib.request
import json

APP_KEY    = '7zu9nsetnwt8hhc'
APP_SECRET = 'donw344swp3xp9x'

# Step 1 — build the authorization URL
params = urllib.parse.urlencode({
    'client_id':         APP_KEY,
    'token_access_type': 'offline',   # asks for a refresh token
    'response_type':     'code',
})
auth_url = f'https://www.dropbox.com/oauth2/authorize?{params}'

print('\n=== Dropbox Re-authorization ===\n')
print('1. Open this URL in your browser:\n')
print(f'   {auth_url}\n')
print('2. Click "Allow" (make sure you are logged in as the orchestragold Dropbox account).')
print('3. Dropbox will show you a short authorization code. Paste it below.\n')

auth_code = input('Authorization code: ').strip()

# Step 2 — exchange the code for tokens
data = urllib.parse.urlencode({
    'code':       auth_code,
    'grant_type': 'authorization_code',
}).encode('utf-8')

req = urllib.request.Request(
    'https://api.dropbox.com/oauth2/token',
    data=data,
    method='POST',
)
# Basic auth with app key + secret
import base64
credentials = base64.b64encode(f'{APP_KEY}:{APP_SECRET}'.encode()).decode()
req.add_header('Authorization', f'Basic {credentials}')
req.add_header('Content-Type', 'application/x-www-form-urlencoded')

try:
    with urllib.request.urlopen(req) as resp:
        tokens = json.loads(resp.read())
except urllib.error.HTTPError as e:
    body = e.read().decode()
    print(f'\nError from Dropbox: {e.code} — {body}')
    raise SystemExit(1)

refresh_token = tokens.get('refresh_token')
if not refresh_token:
    print(f'\nUnexpected response: {tokens}')
    raise SystemExit(1)

print('\n=== SUCCESS ===\n')
print(f'New refresh token:\n\n    {refresh_token}\n')
print('Next steps:')
print('  1. Go to cPanel → Python App → Environment Variables')
print('     Set DROPBOX_REFRESH_TOKEN to the value above.')
print('  2. Update your local .env file if you use one.')
print('  3. Restart the Portal (touch ~/control-room/tmp/restart.txt).')
print()
