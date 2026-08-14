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

# House style rules — applies to every pitch type.
RULES_PATH = '/PITCH_MACHINE_RULES.md'

# Per-pitch-type archive files. Each maps a pitch_type value to its Dropbox path.
# The archive for that type is loaded alongside RULES_PATH at draft-generation time.
ARCHIVE_PATHS: dict[str, str] = {
    'Festival':    '/2026 pitches.docx',
    'WAA':         '/WAA pitches.docx',
    'PNW':         '/PNW pitches.docx',
    'Show Invite': '/2026 pitches.docx',   # reuse festival archive until dedicated one exists
    'Distribution':'/2026 pitches.docx',   # same
}

# All paths that need to be in the DB cache (rules + every archive).
KNOWLEDGE_PATHS = [RULES_PATH] + sorted(set(ARCHIVE_PATHS.values()))

_TOKEN_URL   = 'https://api.dropbox.com/oauth2/token'
_DOWNLOAD_URL = 'https://content.dropboxapi.com/2/files/download'
_UPLOAD_URL   = 'https://content.dropboxapi.com/2/files/upload'


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
    """
    Convert raw file bytes to text suitable for Claude's knowledge context.
    For .docx files: uses mammoth to convert to HTML, then strips all tags
    except <a href> so hyperlinks survive into the prompt. This lets Claude
    reproduce actual link URLs (KEXP, Live, IG, etc.) in generated drafts
    rather than outputting bare link text with no href.
    """
    if path.endswith('.docx'):
        try:
            import mammoth
            import re
            result = mammoth.convert_to_html(io.BytesIO(raw))
            html = result.value
            # Strip every tag EXCEPT <a href="...">...</a>
            # Keep the link structure so Claude can reproduce real URLs.
            text = re.sub(r'<(?!/?a\b)[^>]+>', '', html)
            # Decode common HTML entities
            text = (text
                    .replace('&amp;', '&')
                    .replace('&lt;', '<')
                    .replace('&gt;', '>')
                    .replace('&nbsp;', ' ')
                    .replace('&#x27;', "'")
                    .replace('&quot;', '"'))
            # Collapse excessive blank lines
            text = re.sub(r'\n{3,}', '\n\n', text).strip()
            return text
        except Exception as e:
            raise DropboxError(f'Could not parse .docx at {path!r}: {e}')
    return raw.decode('utf-8', errors='replace')


def upload_file(path: str, content: str) -> None:
    """
    Create or overwrite a text file in the Dropbox App folder.
    Used for queue CSV write-back.
    """
    access_token = _get_access_token()
    resp = requests.post(
        _UPLOAD_URL,
        headers={
            'Authorization': f'Bearer {access_token}',
            'Dropbox-API-Arg': json.dumps({
                'path': path,
                'mode': 'overwrite',
                'autorename': False,
                'mute': True,
            }),
            'Content-Type': 'application/octet-stream',
        },
        data=content.encode('utf-8'),
        timeout=30,
    )
    resp.raise_for_status()


def download_file_direct(path: str) -> Optional[str]:
    """
    Download a file from Dropbox and return its text content without DB caching.
    Returns None if the file doesn't exist (409 path/not_found).
    Use for the queue CSV which changes frequently and shouldn't be stalely cached.
    """
    try:
        access_token = _get_access_token()
        raw = _download_file(path, access_token)
        return _extract_text(path, raw)
    except Exception as e:
        if '409' in str(e) or 'not_found' in str(e).lower():
            return None
        raise DropboxError(f'Failed to download {path!r}: {e}')


def get_or_create_queue_csv() -> str:
    """
    Download the pitch queue CSV from Dropbox, creating an empty one if it doesn't exist.
    Returns the CSV content as a string.
    """
    from app.integrations.pitch_queue import QUEUE_PATH, empty_queue_csv
    content = download_file_direct(QUEUE_PATH)
    if content is None:
        content = empty_queue_csv()
        upload_file(QUEUE_PATH, content)
    return content


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
    Raises DropboxError if the rules file or any archive hasn't been synced yet.
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


def get_knowledge_for_pitch_type(pitch_type: str) -> tuple[str, str]:
    """
    Return (rules_content, archive_content) for a given pitch type.
    Falls back to the Festival archive for unknown types.
    Raises DropboxError if the required files haven't been synced.
    """
    from app.models.knowledge import DropboxSync

    archive_path = ARCHIVE_PATHS.get(pitch_type, ARCHIVE_PATHS['Festival'])
    needed = [RULES_PATH, archive_path]

    records = {r.path: r.content for r in DropboxSync.query.filter(
        DropboxSync.path.in_(needed)
    ).all() if r.content}

    missing = [p for p in needed if p not in records]
    if missing:
        raise DropboxError(
            f'Knowledge not synced for pitch type {pitch_type!r} — '
            f'run `flask sync-knowledge`. Missing: {missing}'
        )
    return records[RULES_PATH], records[archive_path]
