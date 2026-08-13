"""
Dropbox integration for the knowledge sync.

Pulls PITCH_MACHINE_RULES.md and the pitch archive .docx from Dropbox
into the dropbox_sync table. Called by `flask sync-knowledge` and on
demand before a draft generation batch.

Auth: Dropbox OAuth refresh-token flow.
  DROPBOX_APP_KEY, DROPBOX_APP_SECRET, DROPBOX_REFRESH_TOKEN in env/config.
"""

from __future__ import annotations

import io
import json
from datetime import datetime
from typing import Optional

import requests
from flask import current_app

from app.extensions import db

# Canonical Dropbox paths for the Pitch Machine knowledge base.
# The .docx is the curated real-pitch archive ("when Sabbath went to Mali…").
KNOWLEDGE_PATHS = [
    '/CoWork/Festival Outreach/PITCH_MACHINE_RULES.md',
    '/CoWork/Festival Outreach/2026-07 psych rock pitches - when sabbath went to Mali for four minutes.docx',
]

_TOKEN_URL   = 'https://api.dropbox.com/oauth2/token'
_DOWNLOAD_URL = 'https://content.dropboxapi.com/2/files/download'


class DropboxError(Exception):
    pass


def _get_access_token() -> str:
    """Exchange the stored refresh token for a short-lived access token."""
    app_key      = current_app.config.get('DROPBOX_APP_KEY')
    app_secret   = current_app.config.get('DROPBOX_APP_SECRET')
    refresh_token = current_app.config.get('DROPBOX_REFRESH_TOKEN')

    if not all([app_key, app_secret, refresh_token]):
        raise DropboxError(
            'DROPBOX_APP_KEY, DROPBOX_APP_SECRET, and DROPBOX_REFRESH_TOKEN must be set'
        )

    resp = requests.post(
        _TOKEN_URL,
        data={'grant_type': 'refresh_token', 'refresh_token': refresh_token},
        auth=(app_key, app_secret),
        timeout=15,
    )
    resp.raise_for_status()
    return resp.json()['access_token']


def _download_file(path: str, access_token: str) -> bytes:
    """Download a Dropbox file by path. Returns raw bytes."""
    resp = requests.post(
        _DOWNLOAD_URL,
        headers={
            'Authorization': f'Bearer {access_token}',
            'Dropbox-API-Arg': json.dumps({'path': path}),
        },
        timeout=60,
    )
    resp.raise_for_status()
    return resp.content


def _extract_text(path: str, raw: bytes) -> str:
    """Convert raw file bytes to plain text, with .docx support."""
    if path.endswith('.docx'):
        try:
            from docx import Document
            doc = Document(io.BytesIO(raw))
            return '\n'.join(p.text for p in doc.paragraphs if p.text.strip())
        except Exception as e:
            raise DropboxError(f'Could not parse .docx at {path!r}: {e}')
    return raw.decode('utf-8', errors='replace')


def sync_knowledge_to_cache(paths: Optional[list[str]] = None) -> dict[str, int]:
    """
    Pull each path in KNOWLEDGE_PATHS from Dropbox into the dropbox_sync table.
    Returns {path: character_count}. Safe to call repeatedly.
    """
    from app.models.knowledge import DropboxSync

    if paths is None:
        paths = KNOWLEDGE_PATHS

    access_token = _get_access_token()
    results: dict[str, int] = {}

    for path in paths:
        raw  = _download_file(path, access_token)
        text = _extract_text(path, raw)

        record = DropboxSync.query.filter_by(path=path).first()
        if record is None:
            record = DropboxSync(path=path)
            db.session.add(record)
        record.content   = text
        record.synced_at = datetime.utcnow()
        results[path]    = len(text)

    db.session.commit()
    return results


def get_knowledge_content() -> dict[str, str]:
    """
    Return cached content for all KNOWLEDGE_PATHS.
    Raises DropboxError if any path hasn't been synced yet.
    """
    from app.models.knowledge import DropboxSync

    records = {r.path: r.content for r in DropboxSync.query.all() if r.content}
    missing = [p for p in KNOWLEDGE_PATHS if p not in records]
    if missing:
        raise DropboxError(
            'Knowledge base not synced — run `flask sync-knowledge`. '
            f'Missing: {missing}'
        )
    return {p: records[p] for p in KNOWLEDGE_PATHS}
