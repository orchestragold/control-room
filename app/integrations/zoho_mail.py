"""
Zoho Mail integration for Pitch Machine email sending.

Auth: OAuth 2.0 refresh-token flow.
  ZOHO_CLIENT_ID, ZOHO_CLIENT_SECRET, ZOHO_REFRESH_TOKEN, ZOHO_FROM_EMAIL in env/config.

Sends HTML email via the Zoho Mail API v1.
Account ID is fetched once per app context and cached — avoids an extra API call
on every send within the same process lifetime.
"""

from __future__ import annotations

import re
from functools import lru_cache
from typing import Optional

import requests
from flask import current_app

_TOKEN_URL    = 'https://accounts.zoho.com/oauth/v2/token'
_ACCOUNTS_URL = 'https://mail.zoho.com/api/accounts'
_SEND_URL     = 'https://mail.zoho.com/api/accounts/{account_id}/messages'


class ZohoError(Exception):
    pass


def _get_access_token() -> str:
    """Exchange refresh token for a short-lived access token."""
    client_id     = current_app.config.get('ZOHO_CLIENT_ID')
    client_secret = current_app.config.get('ZOHO_CLIENT_SECRET')
    refresh_token = current_app.config.get('ZOHO_REFRESH_TOKEN')

    if not all([client_id, client_secret, refresh_token]):
        raise ZohoError(
            'ZOHO_CLIENT_ID, ZOHO_CLIENT_SECRET, and ZOHO_REFRESH_TOKEN must be set'
        )

    resp = requests.post(
        _TOKEN_URL,
        params={
            'grant_type':    'refresh_token',
            'client_id':     client_id,
            'client_secret': client_secret,
            'refresh_token': refresh_token,
        },
        timeout=15,
    )
    resp.raise_for_status()
    data = resp.json()
    token = data.get('access_token')
    if not token:
        raise ZohoError(f'No access token in Zoho response: {data}')
    return token


def get_account_id(access_token: str) -> str:
    """
    Fetch the Zoho account ID for ZOHO_FROM_EMAIL.
    Called once and the result is used for the lifetime of the queue processor run.
    """
    from_email = current_app.config.get('ZOHO_FROM_EMAIL', '').lower()
    resp = requests.get(
        _ACCOUNTS_URL,
        headers={'Authorization': f'Zoho-oauthtoken {access_token}'},
        timeout=15,
    )
    resp.raise_for_status()
    data = resp.json()

    for account in data.get('data', []):
        for addr in account.get('emailAddress', []):
            if addr.get('mailId', '').lower() == from_email:
                return str(account['accountId'])

    # Fallback: return first account
    accounts = data.get('data', [])
    if accounts:
        return str(accounts[0]['accountId'])

    raise ZohoError(f'Could not find Zoho account for {from_email!r}')


def send_email(
    to_address: str,
    subject: str,
    body_html: str,
    cc_address: Optional[str] = None,
    from_address: Optional[str] = None,
) -> dict:
    """
    Send an HTML email via Zoho Mail.
    Returns the Zoho API response dict.
    In test mode, the to_address should already be the redirected address
    (resolve_email_recipient() handles that in the approve route).
    """
    access_token = _get_access_token()
    account_id   = get_account_id(access_token)
    from_email   = from_address or current_app.config.get('ZOHO_FROM_EMAIL', '')
    from_name    = current_app.config.get('ZOHO_FROM_NAME', '')
    from_addr    = f'"{from_name}" <{from_email}>' if from_name else from_email

    content = _build_html(body_html)

    payload: dict = {
        'fromAddress': from_addr,
        'toAddress':   to_address,
        'subject':     subject,
        'content':     content,
        'mailFormat':  'html',
    }
    if cc_address:
        payload['ccAddress'] = cc_address

    resp = requests.post(
        _SEND_URL.format(account_id=account_id),
        headers={
            'Authorization': f'Zoho-oauthtoken {access_token}',
            'Content-Type':  'application/json',
        },
        json=payload,
        timeout=30,
    )
    resp.raise_for_status()
    return resp.json()


def _build_html(body: str) -> str:
    """
    Wrap the pitch body in Tahoma 12pt, convert newlines to <br>,
    and append the configured signature.
    """
    has_tags = bool(re.search(r'<[a-zA-Z]', body))

    if not has_tags:
        escaped = (body
                   .replace('&', '&amp;')
                   .replace('<', '&lt;')
                   .replace('>', '&gt;'))
        body_html = escaped.replace('\n', '<br>\n')
    else:
        body_html = '<br>\n'.join(body.split('\n'))

    signature_html = current_app.config.get('ZOHO_SIGNATURE_HTML', '')
    sig_block = (
        f'<br><br><hr style="border:none;border-top:1px solid #ddd;margin:16px 0">'
        f'{signature_html}'
        if signature_html else ''
    )

    return (
        '<div style="font-family:Tahoma,Geneva,sans-serif;font-size:12pt;line-height:1.6;">'
        + body_html
        + sig_block
        + '</div>'
    )
