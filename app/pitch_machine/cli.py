"""
Pitch Machine CLI business logic.

Each function here is a standalone unit the Flask CLI command delegates to.
Keeping logic out of app/__init__.py makes it importable and testable without
going through the CLI runner.

Registration: app/__init__.py calls register_pitch_machine_cli(app, db).
"""

from __future__ import annotations

import re as _re
from collections import defaultdict
from datetime import datetime, timedelta
from typing import Optional

from app.extensions import db

# OOO subjects that trigger log-but-don't-cancel behaviour in cancel_replied_touches().
_OOO_PATTERNS = _re.compile(
    r'out of office|auto.?reply|on vacation|automatic reply|away from|i am away',
    _re.IGNORECASE,
)

_EMAIL_RE = _re.compile(r'\b[a-zA-Z0-9._%+\-]+@[a-zA-Z0-9.\-]+\.[a-zA-Z]{2,}\b')
_CC = 'booking@orchestragold.com'


def _parse_date(value: Optional[str]):
    if not value:
        return None
    try:
        return datetime.strptime(value, '%Y-%m-%d').date()
    except ValueError:
        return None


def _first_email(*sources: Optional[str]) -> str:
    for text in sources:
        if not text:
            continue
        for m in _EMAIL_RE.finditer(text):
            addr = m.group(0).lower()
            if 'orchestragold.com' not in addr:
                return addr
    return ''


# ── process_queue ─────────────────────────────────────────────────────────────

def process_queue() -> None:
    """
    Process pending zoho_mail send tasks. Run by cron every few minutes.

    Touch 1 (task_type='send_pitch_touch1'):
      - Sends with no threading headers.
      - After send, fetches the RFC Message-ID from Zoho and stores it in
        approval.sent_message_id so Touch 2/3 can reply-in-thread.
      - Reclassifies the pitch_targets row from 'Festival - Cold' to
        'Festival - Pitched Before' so the next cycle uses the right type.

    Touch 2/3 (task_type='send_pitch_touch2' / 'send_pitch_touch3'):
      - Looks up the Touch 1 approval via payload['touch1_approval_id'].
      - Passes its sent_message_id as inReplyTo + references to send_email(),
        threading the message inside the original conversation.
      - Subject is 'Re: <original subject>' — standard threading convention.
    """
    from app.integrations.zoho_mail import ZohoError, send_email, get_message_rfc_id
    from app.models.pitch import PitchApproval
    from app.models.queue import APITaskQueue

    tasks = (
        APITaskQueue.query
        .filter_by(platform='zoho_mail', status='pending')
        .filter(APITaskQueue.retry_count < APITaskQueue.max_retries)
        .filter(
            (APITaskQueue.scheduled_at == None) |
            (APITaskQueue.scheduled_at <= datetime.utcnow())
        )
        .order_by(APITaskQueue.created_at)
        .limit(10)
        .all()
    )

    if not tasks:
        print('No pending tasks.')
        return

    sent   = 0
    failed = 0

    for task in tasks:
        task.status     = 'processing'
        task.started_at = datetime.utcnow()
        db.session.commit()

        payload    = task.payload or {}
        task_type  = task.task_type  # 'send_pitch_touch1' / 'send_pitch_touch2' / 'send_pitch_touch3'

        # Resolve threading headers for Touch 2/3.
        in_reply_to: Optional[str] = None
        references:  Optional[str] = None
        touch1_approval_id = payload.get('touch1_approval_id')
        if touch1_approval_id and task_type in ('send_pitch_touch2', 'send_pitch_touch3'):
            t1 = PitchApproval.query.get(touch1_approval_id)
            if t1 and t1.sent_message_id:
                in_reply_to = t1.sent_message_id
                references  = t1.sent_message_id

        # ── C2: narrow except to send_email() only ───────────────────────────
        # Exceptions in the success block must NOT trigger a retry; the email
        # was already delivered.
        try:
            zoho_resp = send_email(
                to_address  = payload['to_email_actual'],
                subject     = payload['subject'],
                body_html   = payload['body'],
                cc_address  = payload.get('cc_email') or None,
                in_reply_to = in_reply_to,
                references  = references,
            )
        except Exception as e:
            task.retry_count  += 1
            task.error_message = str(e)
            task.status = (
                'pending' if task.retry_count < task.max_retries else 'failed'
            )
            failed += 1
            print(f'  Failed: {e}')
            db.session.commit()
            continue

        # ── C1: commit task=completed before touching approval/HubSpot ───────
        task.status       = 'completed'
        task.completed_at = datetime.utcnow()
        db.session.commit()

        # ── Post-send: update approval + store Message-ID + HubSpot ──────────
        approval = PitchApproval.query.get(payload.get('pitch_approval_id'))
        if approval:
            approval.status  = 'sent'
            approval.sent_at = datetime.utcnow()

            # Store the RFC Message-ID so Touch 2/3 can reply-in-thread.
            # Only for Touch 1 (Touch 2/3 already have the thread established).
            if task_type == 'send_pitch_touch1':
                zoho_msg_id = (
                    zoho_resp.get('data', {}).get('messageId')
                    or zoho_resp.get('messageId')
                    or ''
                )
                if zoho_msg_id:
                    rfc_id = get_message_rfc_id(str(zoho_msg_id))
                    if rfc_id:
                        approval.sent_message_id = rfc_id
                        print(f'  Message-ID stored: {rfc_id}')

                # Reclassify pitch_targets: Cold → Pitched Before on Touch 1 send.
                _reclassify_to_pitched_before(approval)

            if approval.hubspot_contact_id and task_type == 'send_pitch_touch1':
                _write_hubspot_on_send(approval)

        sent += 1
        recipient     = payload.get('to_email_actual', '?')
        intended      = payload.get('to_email_intended', '')
        redirect_note = f' (redirected from {intended})' if payload.get('was_redirected') else ''
        touch_label   = task_type.replace('send_pitch_', '').replace('_', ' ').upper()
        print(f'  Sent [{touch_label}] → {recipient}{redirect_note}')

        db.session.commit()

    print(f'Done — {sent} sent, {failed} failed.')


def _reclassify_to_pitched_before(approval: 'PitchApproval') -> None:
    """
    After Touch 1 sends for a Festival - Cold target, reclassify the matching
    pitch_targets row to 'Festival - Pitched Before' so the next outreach cycle
    uses the correct type. Best-effort — does not block the send.
    """
    try:
        from app.models.pitch_target import PitchTarget
        pt: Optional['PitchTarget'] = None
        if approval.hubspot_contact_id:
            pt = PitchTarget.query.filter_by(
                hubspot_id=approval.hubspot_contact_id
            ).first()
        if pt is None:
            pt = PitchTarget.query.filter(
                db.func.lower(PitchTarget.name) == (approval.company_name or '').lower()
            ).first()
        if pt and pt.pitch_type == 'Festival - Cold':
            pt.pitch_type = 'Festival - Pitched Before'
    except Exception as exc:
        print(f'  Warning: pitch_targets reclassify failed for {approval.company_name}: {exc}')


def _write_hubspot_on_send(approval: 'PitchApproval') -> None:
    """Write ATTEMPTED_TO_CONTACT + reach_out_1 to HubSpot after Touch 1 sends."""
    try:
        from app.integrations.hubspot import HubSpotClient, HubSpotError
        from app.models.hubspot_cache import HubSpotCompany
        updates: dict = {'hs_lead_status': 'ATTEMPTED_TO_CONTACT'}
        if approval.send_date:
            updates['reach_out_1'] = approval.send_date.isoformat()
        HubSpotClient().update_company(approval.hubspot_contact_id, updates)
        company = HubSpotCompany.query.filter_by(
            hubspot_id=approval.hubspot_contact_id
        ).first()
        if company:
            company.hs_lead_status = 'ATTEMPTED_TO_CONTACT'
            if approval.send_date and not company.reach_out_1:
                company.reach_out_1 = approval.send_date
    except Exception as hs_e:
        print(f'  HubSpot write failed for {approval.company_name}: {hs_e}')


# ── generate_drafts ───────────────────────────────────────────────────────────

def generate_drafts() -> None:
    """
    Process pending pitch_machine/generate_draft tasks.
    Creates Touch 1/2/3 per target via _process_generate_draft_task.
    Cron fallback for browser-driven generation.
    """
    from app.integrations.claude_drafts import DraftGenerationError
    from app.models.queue import APITaskQueue

    stale_cutoff = datetime.utcnow() - timedelta(minutes=10)
    stale = (
        APITaskQueue.query
        .filter_by(platform='pitch_machine', task_type='generate_draft', status='processing')
        .filter(APITaskQueue.started_at < stale_cutoff)
        .all()
    )
    for t in stale:
        t.status      = 'pending'
        t.retry_count += 1
    if stale:
        db.session.commit()
        print(f'Reset {len(stale)} stale task(s).')

    tasks = (
        APITaskQueue.query
        .filter_by(platform='pitch_machine', task_type='generate_draft', status='pending')
        .filter(APITaskQueue.retry_count < APITaskQueue.max_retries)
        .order_by(APITaskQueue.created_at)
        .all()
    )

    if not tasks:
        print('No pending generate_draft tasks.')
        return

    print(f'{len(tasks)} pending task(s).')
    succeeded = 0
    failed    = 0

    for task in tasks:
        task.status     = 'processing'
        task.started_at = datetime.utcnow()
        db.session.commit()

        try:
            result = _process_generate_draft_task(task)
            succeeded += 1
            suffix = {1: '', 2: ' (+T2)', 3: ' (+T2/T3)'}.get(result['touch_count'], '')
            print(f"  Done: {result['name']}{suffix}")
        except DraftGenerationError as e:
            failed += 1
            name = (task.payload or {}).get('name') or (task.payload or {}).get('item_name', '?')
            print(f'  Failed: {name}: {e}')

    print(f'Finished: {succeeded} succeeded, {failed} failed.')


def _generate_followup_body(
    prompt_desc: str,
    touch_num: int,
    name: str,
    t1_subject: str,
    pitch_type: str,
    description: Optional[str],
) -> tuple[str, str]:
    """
    Generate a follow-up email body using the Claude API.
    Returns (body_html, subject). Subject is always 'Re: <t1_subject>'.
    Falls back to a minimal placeholder if generation fails.
    """
    subject = f'Re: {t1_subject}' if t1_subject else f'Following up — {name}'

    if not prompt_desc:
        prompt_desc = (
            f'Write a brief Touch {touch_num} follow-up email for {name}. '
            f'This is pitch type: {pitch_type}. Keep it short — one paragraph. '
            f'Reference the prior outreach; do not repeat the full pitch.'
        )

    full_prompt = (
        f'{prompt_desc}\n\n'
        f'Target: {name}\n'
        f'Notes: {description or "none"}\n'
        f'Original subject: {t1_subject}\n\n'
        f'Produce only the email body. No subject line, no headers. '
        f'End with 👍🏽'
    )

    try:
        import anthropic
        from flask import current_app
        client = anthropic.Anthropic(api_key=current_app.config['ANTHROPIC_API_KEY'])
        msg = client.messages.create(
            model='claude-sonnet-4-6',
            max_tokens=800,
            messages=[{'role': 'user', 'content': full_prompt}],
        )
        body = msg.content[0].text.strip()
        return body, subject
    except Exception as e:
        # Placeholder body — visible in review screen; Erich can edit before approving.
        placeholder = (
            f'<p>[Touch {touch_num} draft generation failed: {e}. '
            f'Edit this before approving.]</p>'
        )
        return placeholder, subject


# ── Shared generate-draft task processor ─────────────────────────────────────

def _process_generate_draft_task(task) -> dict:
    """
    Process one generate_draft task. The task must already be in 'processing' state.

    Creates Touch 1 always; creates Touch 2/3 when the pitch type has sequence
    intervals configured. Commits on both success and failure.

    Returns {'name': str, 'touch_count': int}.
    Raises DraftGenerationError on generation failure (task marked 'failed' before raise).

    Called by both run_generate_next (browser, one task at a time) and
    generate_drafts (CLI, batch). Single implementation; two entry points.
    """
    from app.integrations.claude_drafts import DraftGenerationError, DraftGenerator
    from app.models.pitch import PitchApproval
    from app.models.pitch_config import PitchTypeConfig
    from app.utils.sanitize import sanitize_body_html

    payload    = task.payload or {}
    entry_type = payload.get('entry_type', 'hubspot')
    pitch_type = payload.get('pitch_type', 'Festival')

    if entry_type == 'hubspot':
        name          = payload.get('name', '')
        website       = payload.get('website') or None
        description   = payload.get('description') or None
        hubspot_id    = payload.get('hubspot_id', '')
        email_address = ''
    else:
        name          = payload.get('item_name', '')
        website       = None
        description   = payload.get('notes') or None
        hubspot_id    = payload.get('hubspot_id', '')
        email_address = payload.get('email_address', '')

    send_date = _parse_date(payload.get('send_date') or '')

    try:
        draft = DraftGenerator(pitch_type=pitch_type).generate(
            name=name, website=website, description=description,
        )
    except DraftGenerationError as exc:
        task.status        = 'failed'
        task.error_message = str(exc)
        task.completed_at  = datetime.utcnow()
        db.session.commit()
        raise

    to_email = _first_email(draft.research_notes) if entry_type == 'hubspot' else email_address

    db.session.add(PitchApproval(
        hubspot_contact_id = hubspot_id,
        company_name       = name,
        pitch_type         = pitch_type,
        touch_number       = 1,
        draft_subject      = draft.subject,
        draft_body         = sanitize_body_html(draft.body),
        research_notes     = draft.research_notes,
        to_email           = to_email,
        cc_email           = _CC,
        send_date          = send_date,
        status             = 'pending',
    ))

    touch_count = 1
    pt_config: Optional[PitchTypeConfig] = PitchTypeConfig.query.filter_by(name=pitch_type).first()
    if pt_config and pt_config.touch1_to_touch2_days is not None:
        t2_body, t2_subject = _generate_followup_body(
            prompt_desc = pt_config.touch2_prompt or '',
            touch_num   = 2,
            name        = name,
            t1_subject  = draft.subject,
            pitch_type  = pitch_type,
            description = description,
        )
        db.session.add(PitchApproval(
            hubspot_contact_id = hubspot_id,
            company_name       = name,
            pitch_type         = pitch_type,
            touch_number       = 2,
            draft_subject      = t2_subject,
            draft_body         = sanitize_body_html(t2_body),
            research_notes     = draft.research_notes,
            to_email           = to_email,
            cc_email           = _CC,
            status             = 'pending',
        ))
        touch_count = 2

        if pt_config.touch2_to_touch3_days is not None:
            t3_body, t3_subject = _generate_followup_body(
                prompt_desc = pt_config.touch3_prompt or '',
                touch_num   = 3,
                name        = name,
                t1_subject  = draft.subject,
                pitch_type  = pitch_type,
                description = description,
            )
            db.session.add(PitchApproval(
                hubspot_contact_id = hubspot_id,
                company_name       = name,
                pitch_type         = pitch_type,
                touch_number       = 3,
                draft_subject      = t3_subject,
                draft_body         = sanitize_body_html(t3_body),
                research_notes     = draft.research_notes,
                to_email           = to_email,
                cc_email           = _CC,
                status             = 'pending',
            ))
            touch_count = 3

    task.status       = 'completed'
    task.completed_at = datetime.utcnow()
    db.session.commit()
    return {'name': name, 'touch_count': touch_count}


# ── cancel_replied_touches ────────────────────────────────────────────────────

def cancel_replied_touches(days_back: int = 90) -> None:
    """
    Scan the Zoho inbox for replies from pitched addresses. Auto-cancel
    pending Touch 2/3 rows for any address that replied.

    OOO guard: if the reply subject matches out-of-office patterns, log it
    but do NOT cancel the sequence. Cancelling a Touch 2 is reversible;
    sending one to someone who already replied is not — auto-cancel fails safe.

    Every cancellation is printed so sequences don't quietly vanish.
    Run by cron BEFORE process-queue.
    """
    from app.integrations.zoho_mail import ZohoError, list_inbox_messages
    from app.integrations.dropbox_sync import get_or_create_queue_csv
    from app.integrations.pitch_queue import parse_queue
    from app.models.pitch import PitchApproval

    # Build set of pitched addresses (same logic as scan-replies route).
    pitched_emails: dict[str, str] = {}  # addr → company_name
    try:
        items = parse_queue(get_or_create_queue_csv())
        for item in items:
            if item.status == 'pitched' and item.email_address:
                addr = item.email_address.lower().strip()
                if addr:
                    pitched_emails[addr] = item.name
    except Exception:
        pass

    for a in PitchApproval.query.filter_by(status='sent').all():
        if a.to_email:
            addr = a.to_email.lower().strip()
            if addr and addr not in pitched_emails:
                pitched_emails[addr] = a.company_name or addr

    if not pitched_emails:
        print('No pitched addresses found.')
        return

    try:
        inbox = list_inbox_messages(days_back=days_back)
    except ZohoError as e:
        print(f'Inbox scan failed: {e}')
        return

    cancelled = 0
    ooo_skipped = 0

    for msg in inbox:
        addr = msg['from_address']
        if addr not in pitched_emails:
            continue

        company_name = pitched_emails[addr]
        subject      = msg.get('subject', '') or ''

        if _OOO_PATTERNS.search(subject):
            print(f'  OOO detected (skipped): {addr}  subj={subject[:60]!r}')
            ooo_skipped += 1
            continue

        # Find pending Touch 2/3 rows for this address and cancel them.
        pending_followups = (
            PitchApproval.query
            .filter(
                db.func.lower(PitchApproval.to_email) == addr,
                PitchApproval.touch_number > 1,
                PitchApproval.status.in_(['pending', 'approved']),
            )
            .all()
        )
        for followup in pending_followups:
            followup.status = 'cancelled_reply'
            cancelled += 1
            print(
                f'  CANCELLED Touch {followup.touch_number} for '
                f'{company_name} ({addr}) — reply received: {subject[:60]!r}'
            )

        if pending_followups:
            db.session.commit()

    print(f'Done — {cancelled} touch(es) cancelled, {ooo_skipped} OOO reply(s) skipped.')


# ── scan_sent ─────────────────────────────────────────────────────────────────

def scan_sent() -> None:
    """
    Scan Zoho Sent folder and reconcile against pitch_approvals.
    Marks matched approvals as sent, writes ATTEMPTED_TO_CONTACT to HubSpot.
    Run manually after sending pitches outside the portal, or to backfill.
    """
    from app.integrations.zoho_mail import ZohoError, list_sent_messages
    from app.integrations.hubspot import HubSpotClient, HubSpotError
    from app.models.pitch import PitchApproval
    from app.models.hubspot_cache import HubSpotCompany

    try:
        messages = list_sent_messages(days_back=90)
    except ZohoError as e:
        print(f'Error fetching sent messages: {e}')
        return

    print(f'Found {len(messages)} sent messages in last 90 days.')

    matched   = 0
    unmatched = []

    for msg in messages:
        to_email = msg['to_address']
        if not to_email:
            continue

        approval = (
            PitchApproval.query
            .filter(
                db.func.lower(PitchApproval.to_email) == to_email,
                PitchApproval.status.in_(['pending', 'approved']),
            )
            .first()
        )

        if not approval:
            unmatched.append(msg)
            continue

        approval.status  = 'sent'
        approval.sent_at = msg['sent_at'] or datetime.utcnow()

        if approval.hubspot_contact_id:
            try:
                updates: dict = {'hs_lead_status': 'ATTEMPTED_TO_CONTACT'}
                if approval.send_date:
                    updates['reach_out_1'] = approval.send_date.isoformat()
                HubSpotClient().update_company(approval.hubspot_contact_id, updates)
                company = HubSpotCompany.query.filter_by(
                    hubspot_id=approval.hubspot_contact_id
                ).first()
                if company:
                    company.hs_lead_status = 'ATTEMPTED_TO_CONTACT'
                    if approval.send_date and not company.reach_out_1:
                        company.reach_out_1 = approval.send_date
            except HubSpotError as e:
                print(f'  HubSpot write failed for {approval.company_name}: {e}')

        db.session.commit()
        matched += 1
        print(f'  Matched: {approval.company_name} → {to_email}')

    print(f'\n{matched} matched and updated.')

    # Phase 2: subject-line proposed matches
    proposed      = []
    still_unmatched = []

    if unmatched:
        _SUBJECT_PATTERNS = [
            _re.compile(r'Orchestra\s+GOLD\s+[✱✱*]\s+(.+?)\s+\d{4}', _re.IGNORECASE),
            _re.compile(r'Orchestra\s+GOLD\s+[-–—]\s+(.+?)\s+\d{4}', _re.IGNORECASE),
            _re.compile(r'Orchestra\s+GOLD\s+[|]\s+(.+)', _re.IGNORECASE),
        ]
        from app.models.hubspot_cache import HubSpotCompany
        all_companies = HubSpotCompany.query.filter_by(is_duplicate=False).all()

        def _extract_name(subject):
            for pat in _SUBJECT_PATTERNS:
                m = pat.search(subject)
                if m:
                    return m.group(1).strip()
            return None

        def _find_company(extracted):
            name_lower = extracted.lower()
            exact = [c for c in all_companies if c.name.lower() == name_lower]
            if exact:
                return exact[0]
            subs = [c for c in all_companies
                    if name_lower in c.name.lower() or c.name.lower() in name_lower]
            return subs[0] if len(subs) == 1 else None

        for msg in unmatched:
            subject   = msg['subject']
            extracted = _extract_name(subject)
            company   = _find_company(extracted) if extracted else None

            if company is None:
                still_unmatched.append(msg)
                continue
            if company.hs_lead_status == 'ATTEMPTED_TO_CONTACT':
                still_unmatched.append(msg)
                continue
            proposed.append((company, msg))

    if proposed:
        print(f'\nPhase 2 — {len(proposed)} proposed match(es) (NOT applied):')
        for company, msg in proposed:
            print(f'  ? {company.name}  ←  "{msg["subject"][:65]}"')

    if still_unmatched:
        import os as _os
        seen_pairs: set = set()
        deduped = []
        for msg in still_unmatched:
            key = (msg['to_address'], msg['subject'])
            if key not in seen_pairs:
                seen_pairs.add(key)
                deduped.append(msg)

        report_path = _os.path.expanduser('~/scan_sent_unmatched.txt')
        with open(report_path, 'w') as _f:
            _f.write(f'scan-sent unmatched — {len(deduped)} messages '
                     f'({len(still_unmatched) - len(deduped)} duplicates collapsed)\n\n')
            for msg in deduped:
                _f.write(f'{msg["to_address"]}\t{msg["subject"]}\t{msg.get("sent_at","")}\n')
        print(f'\n{len(deduped)} unmatched — written to: {report_path}')
        for msg in deduped[:50]:
            print(f'  → {msg["to_address"]}: {msg["subject"][:65]}')

    # Phase 3: fix portal pitches where send succeeded but HubSpot write failed.
    from app.integrations.hubspot import HubSpotClient, HubSpotError
    from app.models.hubspot_cache import HubSpotCompany
    from app.models.pitch import PitchApproval

    hs_fixed   = 0
    hs_skipped = 0
    sent_approvals = PitchApproval.query.filter_by(status='sent').all()
    for appr in sent_approvals:
        if not appr.hubspot_contact_id:
            hs_skipped += 1
            continue
        company = HubSpotCompany.query.filter_by(
            hubspot_id=appr.hubspot_contact_id
        ).first()
        if not company or company.hs_lead_status == 'ATTEMPTED_TO_CONTACT':
            continue
        try:
            updates: dict = {'hs_lead_status': 'ATTEMPTED_TO_CONTACT'}
            if appr.send_date and not company.reach_out_1:
                updates['reach_out_1'] = appr.send_date.isoformat()
            HubSpotClient().update_company(appr.hubspot_contact_id, updates)
            company.hs_lead_status = 'ATTEMPTED_TO_CONTACT'
            if appr.send_date and not company.reach_out_1:
                company.reach_out_1 = appr.send_date
            db.session.commit()
            hs_fixed += 1
            print(f'  Phase 3 fixed: {appr.company_name} (approval #{appr.id})')
        except HubSpotError as e:
            print(f'  Phase 3 HubSpot write failed for {appr.company_name}: {e}')

    total  = len(sent_approvals)
    linked = total - hs_skipped
    print(f'\nPhase 3: {total} sent portal approvals total.')
    print(f'  {hs_skipped} skipped — no hubspot_contact_id.')
    if linked:
        print(f'  {linked} HubSpot-linked: {hs_fixed} fixed, {linked - hs_fixed} already correct.')


# ── pitch_untracked_sends ─────────────────────────────────────────────────────

def pitch_untracked_sends() -> None:
    """D11 triage: classify unmatched reach_out_1 dates into three tiers. Read-only."""
    from app.models.hubspot_cache import HubSpotCompany
    from app.models.pitch import PitchApproval
    from app.utils.dates import local_today

    companies = (
        HubSpotCompany.query
        .filter(HubSpotCompany.reach_out_1 != None)
        .order_by(HubSpotCompany.reach_out_1)
        .all()
    )
    if not companies:
        print('No HubSpot companies have reach_out_1 set.')
        return

    today = local_today()
    portal_hs_ids = {
        row.hubspot_contact_id
        for row in PitchApproval.query
        .filter(PitchApproval.hubspot_contact_id != '')
        .filter(PitchApproval.hubspot_contact_id != None)
        .all()
    }
    hs_confirmed_statuses = {'ATTEMPTED_TO_CONTACT', 'CONNECTED'}

    portal_matched, tier1, tier2, tier3 = [], [], [], []
    for c in companies:
        is_matched = (
            c.hubspot_id in portal_hs_ids
            or c.hs_lead_status in hs_confirmed_statuses
        )
        if is_matched:
            portal_matched.append(c)
        elif c.reach_out_1 > today:
            tier1.append(c)
        elif c.reach_out_1.day == 1:
            tier2.append(c)
        else:
            tier3.append(c)

    print(f'\nTotal with reach_out_1 set: {len(companies)}')
    print(f'  Confirmed sent: {len(portal_matched)}')
    print(f'  Tier 1 — future date:          {len(tier1)}')
    print(f'  Tier 2 — past, 1st of month:   {len(tier2)}')
    print(f'  Tier 3 — past, non-1st:        {len(tier3)}')

    if tier3:
        print('\n' + '─' * 62)
        print('⚠  TIER 3 — POSSIBLE REAL SENDS (migration blocked)')
        print('─' * 62)
        for c in tier3:
            print(f'  {c.reach_out_1}  {c.name}  [{c.hubspot_id}]')
    else:
        print('\n  No Tier 3 records — migration safe.')
    if tier2:
        print('\n--- Tier 2 ---')
        for c in tier2:
            print(f'  {c.reach_out_1}  {c.name}  [{c.hubspot_id}]')


# ── hubspot_detect_duplicates ─────────────────────────────────────────────────

def hubspot_detect_duplicates() -> None:
    """D8: Find duplicate HubSpot Company records. Read-only."""
    from app.models.hubspot_cache import HubSpotCompany

    def _normalise(name: str) -> str:
        name = _re.sub(r'^\[DUPLICATE[^\]]*\]\s*', '', name, flags=_re.IGNORECASE)
        name = _re.sub(r'[^\w]', ' ', name.lower())
        name = _re.sub(r'\s+', ' ', name).strip()
        for suffix in ('music festival', 'jazz festival', 'music fest', 'festival', 'fest'):
            if name.endswith(' ' + suffix):
                name = name[: -(len(suffix) + 1)].rstrip()
                break
        return name

    def _score(c) -> int:
        s = 0
        if c.hs_lead_status == 'ATTEMPTED_TO_CONTACT': s += 4
        elif c.hs_lead_status and c.hs_lead_status not in ('', 'NEW'): s += 2
        if c.notes_last_contacted: s += 3
        if c.reach_out_1:          s += 2
        if c.description and len(c.description) > 100: s += 2
        elif c.description:        s += 1
        if c.website or c.domain:  s += 1
        return s

    all_companies = HubSpotCompany.query.order_by(HubSpotCompany.hubspot_id).all()
    groups: dict = defaultdict(list)
    for c in all_companies:
        key = _normalise(c.name)
        if key:
            groups[key].append(c)

    pairs = {k: v for k, v in groups.items() if len(v) > 1}
    if not pairs:
        print('\nNo duplicate Company records detected.')
        return

    print(f'\n{len(pairs)} duplicate group(s) found\n')
    print('Merging is irreversible — approve each row before acting.\n')

    total_merge_in = 0
    for key in sorted(pairs):
        group   = sorted(pairs[key], key=_score, reverse=True)
        keep    = group[0]
        merge_ins = group[1:]
        total_merge_in += len(merge_ins)
        print(f'── {key} ({len(group)} records) ──')

        def _row(c, role):
            status = (c.hs_lead_status or '—')[:20]
            ro1    = str(c.reach_out_1) if c.reach_out_1 else '—'
            nolc   = '✓ contacted' if c.notes_last_contacted else '—'
            score  = _score(c)
            name   = c.name[:50]
            print(f'  {role:9s}  {c.hubspot_id:15s}  score={score:2d}  {status:20s}  ro1={ro1:12s}  {nolc}  "{name}"')

        _row(keep, 'KEEP')
        for m in merge_ins:
            _row(m, 'MERGE-IN')
        print()

    print(f'Total to merge in: {total_merge_in} record(s)')


# ── Registration ──────────────────────────────────────────────────────────────

def register_pitch_machine_cli(app, db_instance) -> None:
    """Register all Pitch Machine CLI commands on the Flask app."""

    @app.cli.command('process-queue')
    def process_queue_command():
        """Process pending zoho_mail send tasks. Run by cron every few minutes."""
        process_queue()

    @app.cli.command('generate-drafts')
    def generate_drafts_command():
        """Process pending generate_draft tasks. Creates Touch 1/2/3 rows per target."""
        generate_drafts()

    @app.cli.command('cancel-replied-touches')
    def cancel_replied_touches_command():
        """Scan inbox for replies and cancel pending Touch 2/3. Run before process-queue."""
        cancel_replied_touches()

    @app.cli.command('scan-sent')
    def scan_sent_command():
        """Scan Zoho Sent folder and reconcile against pitch_approvals."""
        scan_sent()

    @app.cli.command('pitch-untracked-sends')
    def pitch_untracked_sends_command():
        """D11 triage: classify unmatched reach_out_1 dates. Read-only."""
        pitch_untracked_sends()

    @app.cli.command('hubspot-detect-duplicates')
    def hubspot_detect_duplicates_command():
        """D8: Find duplicate HubSpot Company records. Read-only."""
        hubspot_detect_duplicates()
