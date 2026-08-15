import re
from collections import defaultdict
from dataclasses import dataclass
from datetime import date, datetime
from typing import Optional

from flask import (
    Blueprint,
    abort,
    flash,
    jsonify,
    redirect,
    render_template,
    request,
    url_for,
)
from flask_login import current_user, login_required

from app.core.mode import is_test_mode, resolve_email_recipient
from app.extensions import csrf, db
from app.utils.sanitize import sanitize_body_html
from app.integrations.hubspot import (
    HubSpotClient,
    HubSpotError,
    get_cached_companies,
    sync_companies_to_cache,
)
from app.models.hubspot_cache import HubSpotCompany
from app.models.pitch import PitchApproval
from app.models.queue import APITaskQueue, ApprovalLog
from .stages import (
    ALLOWED_MOVE_TARGETS,
    STAGE_ORDER,
    PMStage,
    get_stage,
    stage_config,
)

pm_bp = Blueprint(
    'pitch_machine',
    __name__,
    url_prefix='/projects/orchestra-gold/pitch-machine',
)

_DRAFT_QUEUE_MAX_SHOWN = 50
_GENERATE_BATCH_LIMIT  = 5
_NEEDS_OUTREACH_CAP    = 15
_DEFAULT_CC            = 'booking@orchestragold.com'


def _require_access():
    if not current_user.has_project_access('pitch-machine'):
        abort(403)


# ── Unified queue entry (merges HubSpot + queue-sheet items) ────────────────────

@dataclass
class QueueEntry:
    """Normalised view of one item in the unified draft queue."""
    name:            str
    pitch_type:      str
    source:          str            # 'hubspot' | 'cowork' | 'spreadsheet'
    send_date:       Optional[date]  # computed from scheduling algorithm
    deadline:        Optional[date]  # festival date (queue sheet) or None (HubSpot items)
    hubspot_id:      str            # empty for queue-sheet-only items
    website:         Optional[str]
    description:     Optional[str]
    notes:           Optional[str]
    has_pending_draft: bool = False

    @property
    def sort_key(self):
        return self.send_date or date.max

    @property
    def entry_id(self):
        """Stable identifier passed as a form value."""
        if self.hubspot_id:
            return f'hs:{self.hubspot_id}'
        return f'qs:{self.name}'


def _build_queue(companies: list) -> list[QueueEntry]:
    """
    Build the unified queue from HubSpot QUEUED companies + Dropbox queue sheet.
    Sorted by computed/planned send date, nearest first.
    """
    from app.integrations.dropbox_sync import DropboxError, get_or_create_queue_csv
    from app.integrations.pitch_queue import parse_queue
    from app.pitch_machine.scheduling import (
        compute_send_date,
        get_buyer_festival_dates,
        infer_is_european,
    )

    today = date.today()
    entries: list[QueueEntry] = []

    # ── Source 1: HubSpot QUEUED companies with upcoming reach_out_1 ────────────
    # Decision #5 (Session 14): only companies with an actual upcoming send date.
    for c in companies:
        if (
            c.is_duplicate
            or c.reach_out_1 is None
            or c.reach_out_1 < today
            or get_stage(c) != PMStage.QUEUED
        ):
            continue
        entries.append(QueueEntry(
            name        = c.name,
            pitch_type  = 'Festival',
            source      = 'hubspot',
            send_date   = c.reach_out_1,
            deadline    = None,
            hubspot_id  = c.hubspot_id,
            website     = c.website,
            description = c.description,
            notes       = None,
        ))

    # ── Source 2: Dropbox queue sheet ────────────────────────────────────────────
    try:
        from app.integrations.dropbox_sync import DropboxError as _DropboxError
        csv_content = get_or_create_queue_csv()
        queue_items = parse_queue(csv_content)
    except _DropboxError as e:
        flash(f'Dropbox queue sheet unavailable: {e}', 'error')
        queue_items = []
    except Exception as e:
        flash(f'Could not load queue sheet: {e}', 'error')
        queue_items = []

    existing_hs_ids = {e.hubspot_id for e in entries if e.hubspot_id}

    # Build a set of company names that have already been sent or are approved/pending —
    # used to filter the queue sheet regardless of whether the CSV write-back succeeded.
    already_handled: set[str] = {
        a.company_name.lower()
        for a in PitchApproval.query.filter(
            PitchApproval.status.in_(['sent', 'approved', 'pending'])
        ).all()
        if a.company_name
    }

    for item in queue_items:
        if item.status != 'queued':
            continue
        if not item.deadline or item.deadline < today:
            continue
        # Skip if this item is already represented by a HubSpot entry
        if item.hubspot_id and item.hubspot_id in existing_hs_ids:
            continue
        # Skip if a pitch_approval already exists for this company (sent/approved/pending)
        if item.name.lower() in already_handled:
            continue

        # Compute send date via algorithm
        if item.deadline:
            try:
                if item.pitch_type == 'Festival':
                    # Festival: send date = 8 months before the festival date
                    send_date = compute_send_date(
                        item.deadline,
                        is_european=False,  # no domain available from queue sheet
                        buyer_festival_dates=[],
                    )
                else:
                    # WAA / Show Invite / PNW / Distribution:
                    # deadline IS the pitch-by date — use it directly
                    send_date = item.deadline
            except Exception:
                send_date = item.deadline
        else:
            send_date = None

        entries.append(QueueEntry(
            name        = item.name,
            pitch_type  = item.pitch_type,
            source      = item.source,
            send_date   = send_date,
            deadline    = item.deadline,
            hubspot_id  = item.hubspot_id or '',
            website     = None,
            description = None,
            notes       = item.notes or None,
        ))

    entries.sort(key=lambda e: e.sort_key)
    return entries[:_DRAFT_QUEUE_MAX_SHOWN]


# ── Kanban board ─────────────────────────────────────────────────────────────────

@pm_bp.route('', strict_slashes=False)
@login_required
def board():
    _require_access()

    sync_error = None
    companies = []

    try:
        companies = get_cached_companies()
    except HubSpotError as e:
        sync_error = str(e)

    buckets: dict[PMStage, list] = defaultdict(list)
    company_by_hs_id: dict[str, object] = {}
    for c in companies:
        company_by_hs_id[c.hubspot_id] = c
        stage = get_stage(c)
        if stage is not None:
            buckets[stage].append(c)

    # Overlay SCHEDULED: pitch_approvals with status='approved' waiting in the send queue.
    # Move the matching HubSpot company out of its current bucket into SCHEDULED.
    # Non-HubSpot approvals get a lightweight placeholder card.
    from types import SimpleNamespace
    approved_approvals = PitchApproval.query.filter_by(status='approved').all()
    scheduled_hs_ids: set[str] = set()
    for appr in approved_approvals:
        if appr.hubspot_contact_id:
            scheduled_hs_ids.add(appr.hubspot_contact_id)
            company = company_by_hs_id.get(appr.hubspot_contact_id)
            if company:
                for stage_bucket in buckets.values():
                    if company in stage_bucket:
                        stage_bucket.remove(company)
                        break
                buckets[PMStage.SCHEDULED].append(company)
        else:
            buckets[PMStage.SCHEDULED].append(SimpleNamespace(
                hubspot_id='',
                name=appr.company_name or 'Unknown',
                reach_out_1=appr.send_date,
                pitch_type=appr.pitch_type,
            ))

    buckets[PMStage.NEEDS_OUTREACH].sort(key=lambda c: c.name.lower())
    buckets[PMStage.QUEUED].sort(key=lambda c: (c.reach_out_1 or date.max))
    buckets[PMStage.SCHEDULED].sort(key=lambda c: (c.reach_out_1 or date.max))
    buckets[PMStage.SENT].sort(key=lambda c: (c.reach_out_1 or date.min), reverse=True)
    for s in (PMStage.IN_NEGOTIATION, PMStage.CONFIRMED,
              PMStage.DEPRIORITIZED, PMStage.DECLINED, PMStage.NEEDS_REVIEW):
        buckets[s].sort(key=lambda c: c.name.lower())

    sync_age = None
    if companies:
        most_recent = max(c.last_synced_at for c in companies)
        minutes = int((datetime.utcnow() - most_recent).total_seconds() / 60)
        sync_age = f'{minutes}m ago' if minutes < 60 else f'{minutes // 60}h ago'

    pending_count = PitchApproval.query.filter_by(status='pending').count()

    return render_template(
        'pitch_machine/board.html',
        buckets=buckets,
        stage_order=STAGE_ORDER,
        stage_config=stage_config,
        PMStage=PMStage,
        allowed_targets=[s.value for s in ALLOWED_MOVE_TARGETS],
        cap=_NEEDS_OUTREACH_CAP,
        sync_age=sync_age,
        sync_error=sync_error,
        pending_count=pending_count,
    )


@pm_bp.route('/sync', methods=['POST'])
@login_required
@csrf.exempt
def sync():
    _require_access()
    try:
        count = sync_companies_to_cache()
        return jsonify({'ok': True, 'count': count})
    except HubSpotError as e:
        return jsonify({'ok': False, 'error': str(e)}), 500


@pm_bp.route('/move', methods=['POST'])
@login_required
@csrf.exempt
def move():
    _require_access()

    if not request.is_json:
        return jsonify({'error': 'JSON required'}), 400

    data = request.get_json(silent=True) or {}
    hubspot_id     = str(data.get('hubspot_id', '')).strip()
    to_stage_value = str(data.get('to_stage', '')).strip()

    if not hubspot_id or not to_stage_value:
        return jsonify({'error': 'hubspot_id and to_stage are required'}), 400

    try:
        to_stage = PMStage(to_stage_value)
    except ValueError:
        return jsonify({'error': f'Unknown stage: {to_stage_value!r}'}), 400

    if to_stage not in ALLOWED_MOVE_TARGETS:
        return jsonify({
            'error': f'Cards cannot be moved to {to_stage.value!r}. '
                     f'Valid targets: {[s.value for s in ALLOWED_MOVE_TARGETS]}'
        }), 400

    company = HubSpotCompany.query.filter_by(hubspot_id=hubspot_id).first()
    if company is None:
        return jsonify({'error': 'Company not found in cache'}), 404
    if company.is_duplicate:
        return jsonify({'error': 'Duplicate records cannot be moved'}), 400

    cfg        = stage_config(to_stage)
    new_status = cfg.hs_status

    try:
        client = HubSpotClient()
        client.update_company(hubspot_id, {'hs_lead_status': new_status})
        company.hs_lead_status = new_status
        db.session.commit()
        return jsonify({'ok': True, 'new_status': new_status})
    except HubSpotError as e:
        db.session.rollback()
        return jsonify({'error': str(e)}), 500


# ── Unified draft queue ──────────────────────────────────────────────────────────

@pm_bp.route('/draft-queue')
@login_required
def draft_queue():
    _require_access()

    try:
        companies = get_cached_companies()
    except HubSpotError as e:
        flash(str(e), 'error')
        return redirect(url_for('pitch_machine.board'))

    entries = _build_queue(companies)

    existing_pending = {
        a.hubspot_contact_id
        for a in PitchApproval.query.filter_by(status='pending').all()
    } | {
        a.company_name
        for a in PitchApproval.query.filter_by(status='pending').all()
        if not a.hubspot_contact_id
    }

    from app.integrations.dropbox_sync import DropboxError, get_knowledge_content
    knowledge_ready = True
    try:
        get_knowledge_content()
    except DropboxError:
        knowledge_ready = False

    from app.pitch_machine.pitch_types import PITCH_TYPES
    return render_template(
        'pitch_machine/draft_queue.html',
        entries=entries,
        existing_pending=existing_pending,
        knowledge_ready=knowledge_ready,
        batch_limit=_GENERATE_BATCH_LIMIT,
        pitch_types=PITCH_TYPES,
    )


# ── Generate drafts ──────────────────────────────────────────────────────────────

@pm_bp.route('/generate', methods=['POST'])
@login_required
def generate():
    _require_access()

    entry_ids = request.form.getlist('entry_id')
    if not entry_ids:
        flash('Select at least one item.', 'error')
        return redirect(url_for('pitch_machine.draft_queue'))

    if len(entry_ids) > _GENERATE_BATCH_LIMIT:
        flash(
            f'Select up to {_GENERATE_BATCH_LIMIT} items per batch '
            f'({len(entry_ids)} selected).',
            'error',
        )
        return redirect(url_for('pitch_machine.draft_queue'))

    from app.integrations.claude_drafts import DraftGenerationError, DraftGenerator
    from app.integrations.dropbox_sync import get_or_create_queue_csv, DropboxError
    from app.integrations.pitch_queue import parse_queue

    # Generators are cached per pitch type — one instance per type in the batch
    _generators: dict[str, DraftGenerator] = {}

    def _get_generator(pitch_type: str) -> DraftGenerator:
        if pitch_type not in _generators:
            _generators[pitch_type] = DraftGenerator(pitch_type=pitch_type)
        return _generators[pitch_type]

    # Load queue sheet items for lookup
    try:
        queue_items = {item.name: item for item in parse_queue(get_or_create_queue_csv())}
    except Exception:
        queue_items = {}

    try:
        companies = get_cached_companies()
    except HubSpotError as e:
        flash(str(e), 'error')
        return redirect(url_for('pitch_machine.draft_queue'))

    company_map = {c.hubspot_id: c for c in companies}

    errors: list[str] = []
    created = 0

    from app.pitch_machine.pitch_types import PITCH_TYPE_SET

    for entry_id in entry_ids:
        if entry_id.startswith('hs:'):
            # ── HubSpot company ──────────────────────────────────────────────────
            hs_id   = entry_id[3:]
            company = company_map.get(hs_id)
            if company is None or company.is_duplicate:
                continue

            existing = PitchApproval.query.filter_by(
                hubspot_contact_id=hs_id, status='pending'
            ).first()
            if existing:
                errors.append(f'{company.name}: pending draft already exists — skipped')
                continue

            # C4: read pitch type from form field; default to Festival
            pitch_type = request.form.get(f'pt_hs_{hs_id}', 'Festival')
            if pitch_type not in PITCH_TYPE_SET:
                pitch_type = 'Festival'

            try:
                draft = _get_generator(pitch_type).generate(
                    name=company.name,
                    website=company.website,
                    description=company.description,
                )
            except DraftGenerationError as e:
                errors.append(f'{company.name}: {e}')
                continue

            db.session.add(PitchApproval(
                hubspot_contact_id = hs_id,
                company_name       = company.name,
                pitch_type         = pitch_type,
                touch_number       = 1,
                draft_subject      = draft.subject,
                draft_body         = sanitize_body_html(draft.body),  # O2
                research_notes     = draft.research_notes,
                to_email           = _extract_email(draft.research_notes),
                cc_email           = _DEFAULT_CC,
                send_date          = company.reach_out_1,
                status             = 'pending',
            ))
            created += 1

        elif entry_id.startswith('qs:'):
            # ── Queue-sheet item ─────────────────────────────────────────────────
            item_name = entry_id[3:]
            item      = queue_items.get(item_name)
            if item is None:
                errors.append(f'{item_name!r}: not found in queue sheet — skipped')
                continue

            existing = PitchApproval.query.filter_by(
                company_name=item_name, status='pending'
            ).first()
            if existing:
                errors.append(f'{item_name}: pending draft already exists — skipped')
                continue

            try:
                draft = _get_generator(item.pitch_type).generate(
                    name=item_name,
                    website=None,
                    description=item.notes or None,
                )
            except DraftGenerationError as e:
                errors.append(f'{item_name}: {e}')
                continue

            db.session.add(PitchApproval(
                hubspot_contact_id = item.hubspot_id or '',
                company_name       = item_name,
                pitch_type         = item.pitch_type,
                touch_number       = 1,
                draft_subject      = draft.subject,
                draft_body         = sanitize_body_html(draft.body),  # O2
                research_notes     = draft.research_notes,
                to_email           = _extract_email(item.notes, draft.research_notes),
                cc_email           = _DEFAULT_CC,
                send_date          = item.send_date,
                status             = 'pending',
            ))
            created += 1

    db.session.commit()

    for err in errors:
        flash(err, 'error')
    if created:
        flash(
            f'{created} draft{"s" if created != 1 else ""} ready for review.',
            'success',
        )

    return redirect(url_for('pitch_machine.review'))


# ── Touch 1 review / approve ──────────────────────────────────────────────────────

@pm_bp.route('/review')
@login_required
def review():
    _require_access()

    pending = (
        PitchApproval.query
        .filter_by(status='pending')
        .order_by(PitchApproval.created_at)
        .all()
    )

    return render_template(
        'pitch_machine/review.html',
        drafts=pending,
        test_mode=is_test_mode(),
        redirect_email=_get_redirect_email_display(),
    )


@pm_bp.route('/approve/<int:pid>', methods=['POST'])
@login_required
def approve(pid: int):
    _require_access()

    approval = PitchApproval.query.get_or_404(pid)
    if approval.status != 'pending':
        flash('This draft is no longer pending.', 'error')
        return redirect(url_for('pitch_machine.review'))

    subject  = request.form.get('subject', '').strip()
    body     = sanitize_body_html(request.form.get('body', '').strip())
    to_email = request.form.get('to_email', '').strip()
    cc_email = request.form.get('cc_email', '').strip()
    send_date_str = request.form.get('send_date', '').strip()

    if not to_email:
        flash('Enter a recipient email before approving.', 'error')
        return redirect(url_for('pitch_machine.review'))
    if not body:
        flash('Draft body cannot be empty.', 'error')
        return redirect(url_for('pitch_machine.review'))

    # Parse the scheduled send date (if provided by the form)
    send_date = _parse_form_date(send_date_str)

    actual_recipient, was_redirected = resolve_email_recipient(to_email)
    send_subject = f'[TEST → {to_email}] {subject}' if was_redirected else subject

    approval.draft_subject = subject
    approval.draft_body    = body
    approval.to_email      = to_email
    approval.cc_email      = cc_email
    approval.send_date     = send_date
    approval.status        = 'approved'
    approval.approved_by   = current_user.id
    approval.approved_at   = datetime.utcnow()

    db.session.add(ApprovalLog(
        approver_id = current_user.id,
        action      = 'approved',
        entity_type = 'pitch_approval',
        entity_id   = str(pid),
        details     = {
            'company_name':     approval.company_name,
            'pitch_type':       approval.pitch_type,
            'to_email':         to_email,
            'actual_recipient': actual_recipient,
            'was_redirected':   was_redirected,
            'subject':          subject,
            'send_date':        send_date.isoformat() if send_date else None,
        },
    ))

    db.session.add(APITaskQueue(
        platform  = 'zoho_mail',
        task_type = 'send_pitch_touch1',
        payload   = {
            'pitch_approval_id': pid,
            'to_email_intended': to_email,
            'to_email_actual':   actual_recipient,
            'cc_email':          cc_email,
            'subject':           send_subject,
            'body':              body,
            'was_redirected':    was_redirected,
            'send_date':         send_date.isoformat() if send_date else None,
        },
        created_by = current_user.id,
    ))

    # ── C3: commit approval + task row BEFORE external side effects ───────────────
    # External writes (Dropbox, HubSpot) happen after the local state is durable.
    # If they fail, the task row already exists and process-queue will still send.
    db.session.commit()

    _remove_from_queue_sheet(approval.company_name)

    hs_write_error = None
    if approval.hubspot_contact_id:
        hs_write_error = _write_hubspot_reach_out_1(
            approval.hubspot_contact_id,
            send_date,
        )

    if hs_write_error:
        flash(f'Approved — but note: {hs_write_error}', 'error')
    elif was_redirected:
        flash(
            f'Approved — queued to send to {actual_recipient} '
            f'(test mode; intended: {to_email}).',
            'success',
        )
    else:
        flash(f'Approved — queued to send to {to_email}.', 'success')

    return redirect(url_for('pitch_machine.review'))


@pm_bp.route('/reject-all', methods=['POST'])
@login_required
def reject_all():
    _require_access()
    pending = PitchApproval.query.filter_by(status='pending').all()
    count = len(pending)
    for approval in pending:
        approval.status = 'rejected'
        db.session.add(ApprovalLog(
            approver_id = current_user.id,
            action      = 'rejected',
            entity_type = 'pitch_approval',
            entity_id   = str(approval.id),
            details     = {'company_name': approval.company_name, 'bulk': True},
        ))
    db.session.commit()
    flash(f'Cleared {count} pending draft{"s" if count != 1 else ""}. Items remain in the queue for re-drafting.', 'success')
    return redirect(url_for('pitch_machine.review'))


@pm_bp.route('/reject/<int:pid>', methods=['POST'])
@login_required
def reject(pid: int):
    _require_access()

    approval = PitchApproval.query.get_or_404(pid)
    if approval.status != 'pending':
        flash('This draft is no longer pending.', 'error')
        return redirect(url_for('pitch_machine.review'))

    approval.status = 'rejected'
    db.session.add(ApprovalLog(
        approver_id = current_user.id,
        action      = 'rejected',
        entity_type = 'pitch_approval',
        entity_id   = str(pid),
        details     = {
            'company_name': approval.company_name,
            'pitch_type':   approval.pitch_type,
        },
    ))
    db.session.commit()

    flash(f'Draft for {approval.company_name} rejected.', 'success')
    return redirect(url_for('pitch_machine.review'))


# ── Helpers ──────────────────────────────────────────────────────────────────────

def _extract_email(*sources: Optional[str]) -> str:
    """
    Search each text source in order and return the first email address found.
    Used to pre-fill the 'To' field at draft-generation time.
    Skips @orchestragold.com addresses (those are CC, not the recipient).
    """
    pattern = re.compile(r'\b[a-zA-Z0-9._%+\-]+@[a-zA-Z0-9.\-]+\.[a-zA-Z]{2,}\b')
    for text in sources:
        if not text:
            continue
        for match in pattern.finditer(text):
            addr = match.group(0).lower()
            if 'orchestragold.com' not in addr:
                return addr
    return ''


def _get_redirect_email_display() -> str:
    from app.core.mode import get_test_redirect_email
    return get_test_redirect_email()


def _parse_form_date(value: str) -> Optional[date]:
    if not value:
        return None
    try:
        return datetime.strptime(value, '%Y-%m-%d').date()
    except ValueError:
        return None


def _remove_from_queue_sheet(company_name: str) -> None:
    """Mark a queue item as 'pitched' and rewrite the CSV to Dropbox."""
    from app.integrations.dropbox_sync import (
        DropboxError,
        get_or_create_queue_csv,
        upload_file,
    )
    from app.integrations.pitch_queue import QUEUE_PATH, parse_queue, serialize_queue
    try:
        items = parse_queue(get_or_create_queue_csv())
        changed = False
        for item in items:
            if item.name == company_name and item.status == 'queued':
                item.status = 'pitched'
                changed = True
        if changed:
            upload_file(QUEUE_PATH, serialize_queue(items))
    except Exception as e:
        import sys
        print(f'[queue sheet] remove failed for {company_name!r}: {e}', file=sys.stderr)


def _write_hubspot_reach_out_1(hubspot_id: str, send_date: Optional[date]) -> Optional[str]:
    """
    Write the computed send date into reach_out_1 on the HubSpot company record.
    Returns an error string if the write fails, or None on success.
    """
    if not send_date:
        return None
    try:
        client = HubSpotClient()
        client.update_company(hubspot_id, {'reach_out_1': send_date.isoformat()})
        company = HubSpotCompany.query.filter_by(hubspot_id=hubspot_id).first()
        if company:
            company.reach_out_1 = send_date
            db.session.flush()
        return None
    except HubSpotError as e:
        return f'HubSpot reach_out_1 write failed: {e}'
