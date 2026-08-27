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

    # Names permanently excluded — even if a research task later adds a fresh queued row
    # with the same name, the not_a_fit record blocks re-emergence in the Portal.
    not_a_fit_names: set[str] = {
        item.name.lower()
        for item in queue_items
        if item.status == 'not_a_fit'
    }

    for item in queue_items:
        if item.status != 'queued':
            continue
        if item.name.lower() in not_a_fit_names:
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

    from app.pitch_machine.pitch_types import get_pitch_types
    return render_template(
        'pitch_machine/draft_queue.html',
        entries=entries,
        existing_pending=existing_pending,
        knowledge_ready=knowledge_ready,
        batch_limit=_GENERATE_BATCH_LIMIT,
        pitch_types=get_pitch_types(),
        type_colors=_get_type_colors(),
        pending_gen=_pending_gen_count(),
    )


# ── Sync knowledge base ──────────────────────────────────────────────────────────

@pm_bp.route('/sync-knowledge', methods=['POST'])
@login_required
def sync_knowledge():
    _require_access()
    from app.integrations.dropbox_sync import DropboxError, sync_knowledge_to_cache
    try:
        results = sync_knowledge_to_cache()
        lines = [f'{path}: {count:,} chars' for path, count in sorted(results.items())]
        flash('Knowledge synced — ' + ' · '.join(lines), 'ok')
    except DropboxError as e:
        flash(f'Sync failed: {e}', 'error')
    return redirect(url_for('pitch_machine.draft_queue'))


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

    from app.integrations.dropbox_sync import get_or_create_queue_csv
    from app.integrations.pitch_queue import parse_queue
    from app.pitch_machine.pitch_types import get_pitch_type_set
    PITCH_TYPE_SET = get_pitch_type_set()

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
    queued = 0

    for entry_id in entry_ids:
        if entry_id.startswith('hs:'):
            hs_id   = entry_id[3:]
            company = company_map.get(hs_id)
            if company is None or company.is_duplicate:
                continue

            existing = PitchApproval.query.filter(
                PitchApproval.hubspot_contact_id == hs_id,
                PitchApproval.status.in_(['pending', 'approved', 'sent']),
            ).first()
            if existing:
                flash(f'{company.name}: already drafted or sent — skipped', 'error')
                continue

            pitch_type = request.form.get(f'pt_hs_{hs_id}', 'Festival')
            if pitch_type not in PITCH_TYPE_SET:
                pitch_type = 'Festival'

            db.session.add(APITaskQueue(
                platform  = 'pitch_machine',
                task_type = 'generate_draft',
                payload   = {
                    'entry_type':  'hubspot',
                    'hubspot_id':  hs_id,
                    'pitch_type':  pitch_type,
                    'name':        company.name,
                    'website':     company.website or '',
                    'description': company.description or '',
                    'send_date':   company.reach_out_1.isoformat() if company.reach_out_1 else None,
                },
                created_by = current_user.id,
            ))
            queued += 1

        elif entry_id.startswith('qs:'):
            item_name = entry_id[3:]
            item      = queue_items.get(item_name)
            if item is None:
                flash(f'{item_name!r}: not found in queue sheet — skipped', 'error')
                continue

            existing = PitchApproval.query.filter(
                PitchApproval.company_name == item_name,
                PitchApproval.status.in_(['pending', 'approved', 'sent']),
            ).first()
            if existing:
                flash(f'{item_name}: already drafted or sent — skipped', 'error')
                continue

            db.session.add(APITaskQueue(
                platform  = 'pitch_machine',
                task_type = 'generate_draft',
                payload   = {
                    'entry_type':    'queue_sheet',
                    'item_name':     item_name,
                    'pitch_type':    item.pitch_type,
                    'notes':         item.notes or '',
                    'send_date':     item.deadline.isoformat() if item.deadline else None,
                    'hubspot_id':    item.hubspot_id or '',
                    'email_address': item.email_address or '',
                },
                created_by = current_user.id,
            ))
            queued += 1

    db.session.commit()

    if queued:
        flash(
            f'{queued} draft{"s" if queued != 1 else ""} queued — click "Process drafts" to generate.',
            'success',
        )
    return redirect(url_for('pitch_machine.draft_queue'))


# ── Process one queued draft (called sequentially by frontend JS) ─────────────

@pm_bp.route('/run-generate-next', methods=['POST'])
@login_required
@csrf.exempt
def run_generate_next():
    _require_access()
    from datetime import datetime, timedelta
    from app.integrations.claude_drafts import DraftGenerationError, DraftGenerator

    # Reset tasks stuck in 'processing' for > 5 min (gateway timeout recovery)
    stale_cutoff = datetime.utcnow() - timedelta(minutes=5)
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

    task = (
        APITaskQueue.query
        .filter_by(platform='pitch_machine', task_type='generate_draft', status='pending')
        .filter(APITaskQueue.retry_count < APITaskQueue.max_retries)
        .order_by(APITaskQueue.created_at)
        .first()
    )

    if task is None:
        return jsonify({'done': True, 'remaining': 0})

    task.status     = 'processing'
    task.started_at = datetime.utcnow()
    db.session.commit()

    payload    = task.payload or {}
    entry_type = payload.get('entry_type', 'hubspot')
    pitch_type = payload.get('pitch_type', 'Festival')

    if entry_type == 'hubspot':
        name        = payload.get('name', '')
        website     = payload.get('website') or None
        description = payload.get('description') or None
        hubspot_id  = payload.get('hubspot_id', '')
        send_date   = _parse_form_date(payload.get('send_date') or '')
    else:
        name        = payload.get('item_name', '')
        website     = None
        description = payload.get('notes') or None
        hubspot_id  = payload.get('hubspot_id', '')
        send_date   = _parse_form_date(payload.get('send_date') or '')

    try:
        draft = DraftGenerator(pitch_type=pitch_type).generate(
            name=name, website=website, description=description,
        )
    except DraftGenerationError as e:
        task.status        = 'failed'
        task.error_message = str(e)
        task.completed_at  = datetime.utcnow()
        db.session.commit()
        remaining = _pending_gen_count()
        return jsonify({'done': remaining == 0, 'remaining': remaining, 'error': f'{name}: {e}'})

    if entry_type == 'hubspot':
        to_email = _extract_email(draft.research_notes)
    else:
        to_email = payload.get('email_address', '')

    db.session.add(PitchApproval(
        hubspot_contact_id = hubspot_id,
        company_name       = name,
        pitch_type         = pitch_type,
        touch_number       = 1,
        draft_subject      = draft.subject,
        draft_body         = sanitize_body_html(draft.body),
        research_notes     = draft.research_notes,
        to_email           = to_email,
        cc_email           = _DEFAULT_CC,
        send_date          = send_date,
        status             = 'pending',
    ))
    task.status       = 'completed'
    task.completed_at = datetime.utcnow()
    db.session.commit()

    remaining = _pending_gen_count()
    return jsonify({'done': remaining == 0, 'remaining': remaining, 'name': name})


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
        type_colors=_get_type_colors(),
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
    approval.approved_at   = datetime.utcnow()  # Phase 3: derive Touch 2/3 dates from date.today(), not approved_at.date() — UTC vs local drift after 5pm Pacific

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


@pm_bp.route('/not-a-fit/<int:pid>', methods=['POST'])
@login_required
def not_a_fit(pid: int):
    _require_access()

    approval = PitchApproval.query.get_or_404(pid)
    if approval.status != 'pending':
        flash('This draft is no longer pending.', 'error')
        return redirect(url_for('pitch_machine.review'))

    reason = request.form.get('reason', '').strip()

    approval.status = 'rejected'
    db.session.add(ApprovalLog(
        approver_id = current_user.id,
        action      = 'not_a_fit',
        entity_type = 'pitch_approval',
        entity_id   = str(pid),
        details     = {
            'company_name': approval.company_name,
            'pitch_type':   approval.pitch_type,
            'reason':       reason,
        },
    ))
    db.session.commit()

    hubspot_id = approval.hubspot_contact_id or ''
    _mark_queue_sheet_not_a_fit(approval.company_name, reason, hubspot_id)
    _mirror_not_a_fit_to_hubspot(hubspot_id, approval.company_name, reason)

    msg = f'{approval.company_name} marked not a fit and removed from queue.'
    if reason:
        msg += f' Reason: {reason}'
    flash(msg, 'success')
    return redirect(url_for('pitch_machine.review'))


# ── The Wheel ────────────────────────────────────────────────────────────────────

@pm_bp.route('/wheel')
@login_required
def wheel():
    _require_access()

    import json
    from app.models.pitch_config import PitchTypeConfig
    from app.models.pitch_target import PitchTarget

    configs = PitchTypeConfig.query.filter_by(active=True).order_by(
        PitchTypeConfig.sort_order, PitchTypeConfig.id
    ).all()

    selected_name = request.args.get('type')
    selected_config = next((c for c in configs if c.name == selected_name), None)
    if selected_config is None and configs:
        selected_config = configs[0]
    if selected_config is None:
        return render_template('pitch_machine/wheel.html', configs=[], selected_config=None,
                               targets_json='{"active":[],"dormant":[]}', today=date.today().isoformat())

    targets = PitchTarget.query.filter(
        PitchTarget.not_a_fit == False,
        PitchTarget.pitch_type == selected_config.name,
    ).all()

    today = date.today()
    active, dormant = [], []
    for t in targets:
        if t.reach_out_1:
            active.append({
                'id':                  t.id,
                'hubspot_id':          t.hubspot_id or '',
                'name':                t.name,
                'reach_out_1':         t.reach_out_1.isoformat(),
                'submission_deadline': t.submission_deadline.isoformat() if t.submission_deadline else None,
                'stage':               t.stage,
                'delta_days':          (t.reach_out_1 - today).days,
            })
        else:
            dormant.append({'name': t.name, 'stage': t.stage})

    return render_template('pitch_machine/wheel.html',
        configs=configs,
        selected_config=selected_config,
        targets_json=json.dumps({'active': active, 'dormant': dormant}),
        today=today.isoformat(),
    )


@pm_bp.route('/wheel/set-override', methods=['POST'])
@login_required
def wheel_set_override():
    """Upsert a drag-to-reschedule override for one HubSpot-linked pitch target.

    Writes to pitch_target_overrides (keyed on hubspot_id) and updates
    pitch_targets.reach_out_1 immediately so the Wheel reflects the change
    without waiting for the next sync. The sync engine re-applies overrides
    after every DELETE+INSERT, so the override survives re-syncs.

    Never writes to HubSpot — Portal-only override.
    """
    _require_access()

    from datetime import date as date_type
    import json
    from app.models.pitch_target import PitchTarget
    from app.models.pitch_target_override import PitchTargetOverride

    data = request.get_json(silent=True) or {}
    hubspot_id    = (data.get('hubspot_id') or '').strip()
    outreach_date = (data.get('outreach_date') or '').strip()

    if not hubspot_id:
        return jsonify({'error': 'hubspot_id required'}), 400
    try:
        new_date = date_type.fromisoformat(outreach_date)
    except ValueError:
        return jsonify({'error': f'invalid date: {outreach_date!r}'}), 400

    # Upsert the override record
    override = PitchTargetOverride.query.get(hubspot_id)
    if override is None:
        override = PitchTargetOverride(hubspot_id=hubspot_id)
        db.session.add(override)
    override.outreach_date_override = new_date
    override.override_set_by        = current_user.email
    override.override_set_at        = datetime.utcnow()

    # Apply immediately to pitch_targets so the Wheel reflects it without a sync
    pt = PitchTarget.query.filter_by(hubspot_id=hubspot_id).first()
    if pt:
        pt.reach_out_1 = new_date

    db.session.commit()
    return jsonify({'ok': True, 'new_date': new_date.isoformat()})


# ── Pitch type config admin (super_admin only) ───────────────────────────────────

def _require_super_admin():
    if current_user.role != 'super_admin':
        abort(403)


@pm_bp.route('/config')
@login_required
def config_list():
    _require_super_admin()
    from app.models.pitch_config import PitchTypeConfig
    types = PitchTypeConfig.query.order_by(PitchTypeConfig.sort_order, PitchTypeConfig.id).all()
    return render_template('pitch_machine/config.html', types=types)


@pm_bp.route('/config/new', methods=['GET', 'POST'])
@login_required
def config_new():
    _require_super_admin()
    from app.models.pitch_config import PitchTypeConfig

    if request.method == 'POST':
        name                 = request.form.get('name', '').strip()
        archive_dropbox_path = request.form.get('archive_dropbox_path', '').strip()
        prompt_template      = request.form.get('prompt_template', '').strip()
        badge_color          = request.form.get('badge_color', '#888888').strip()
        sort_order           = int(request.form.get('sort_order', 0) or 0)
        is_cyclical          = request.form.get('is_cyclical') == '1'

        error = _validate_pitch_type_form(name, archive_dropbox_path, prompt_template, badge_color)
        if error:
            flash(error, 'error')
            return render_template('pitch_machine/config_form.html',
                                   mode='new', form=request.form)

        if PitchTypeConfig.query.filter_by(name=name).first():
            flash(f'A pitch type named {name!r} already exists.', 'error')
            return render_template('pitch_machine/config_form.html',
                                   mode='new', form=request.form)

        db.session.add(PitchTypeConfig(
            name=name,
            archive_dropbox_path=archive_dropbox_path,
            prompt_template=prompt_template,
            badge_color=badge_color,
            sort_order=sort_order,
            is_cyclical=is_cyclical,
            active=True,
        ))
        db.session.commit()
        flash(f'Pitch type {name!r} created.', 'success')
        return redirect(url_for('pitch_machine.config_list'))

    return render_template('pitch_machine/config_form.html', mode='new', form={})


@pm_bp.route('/config/<int:tid>/edit', methods=['GET', 'POST'])
@login_required
def config_edit(tid: int):
    _require_super_admin()
    from app.models.pitch_config import PitchTypeConfig

    pt = PitchTypeConfig.query.get_or_404(tid)

    if request.method == 'POST':
        name                 = request.form.get('name', '').strip()
        archive_dropbox_path = request.form.get('archive_dropbox_path', '').strip()
        prompt_template      = request.form.get('prompt_template', '').strip()
        badge_color          = request.form.get('badge_color', '#888888').strip()
        sort_order           = int(request.form.get('sort_order', 0) or 0)
        is_cyclical          = request.form.get('is_cyclical') == '1'

        error = _validate_pitch_type_form(name, archive_dropbox_path, prompt_template, badge_color)
        if error:
            flash(error, 'error')
            return render_template('pitch_machine/config_form.html',
                                   mode='edit', pt=pt, form=request.form)

        conflict = PitchTypeConfig.query.filter(
            PitchTypeConfig.name == name,
            PitchTypeConfig.id != tid,
        ).first()
        if conflict:
            flash(f'Another pitch type is already named {name!r}.', 'error')
            return render_template('pitch_machine/config_form.html',
                                   mode='edit', pt=pt, form=request.form)

        pt.name                 = name
        pt.archive_dropbox_path = archive_dropbox_path
        pt.prompt_template      = prompt_template
        pt.badge_color          = badge_color
        pt.sort_order           = sort_order
        pt.is_cyclical          = is_cyclical
        db.session.commit()
        flash(f'Pitch type {name!r} updated.', 'success')
        return redirect(url_for('pitch_machine.config_list'))

    return render_template('pitch_machine/config_form.html',
                           mode='edit', pt=pt, form={
                               'name':                 pt.name,
                               'archive_dropbox_path': pt.archive_dropbox_path,
                               'prompt_template':      pt.prompt_template,
                               'badge_color':          pt.badge_color,
                               'sort_order':           pt.sort_order,
                               'is_cyclical':          '1' if pt.is_cyclical else '0',
                           })


@pm_bp.route('/config/<int:tid>/toggle', methods=['POST'])
@login_required
@csrf.exempt
def config_toggle(tid: int):
    _require_super_admin()
    from app.models.pitch_config import PitchTypeConfig
    pt = PitchTypeConfig.query.get_or_404(tid)
    pt.active = not pt.active
    db.session.commit()
    state = 'activated' if pt.active else 'deactivated'
    return jsonify({'ok': True, 'active': pt.active, 'message': f'{pt.name} {state}.'})


def _validate_pitch_type_form(name, archive_dropbox_path, prompt_template, badge_color) -> str:
    from app.models.pitch_config import PitchTypeConfig
    if not name:
        return 'Name is required.'
    if not archive_dropbox_path:
        return 'Dropbox archive path is required.'
    if not archive_dropbox_path.startswith('/'):
        return 'Dropbox archive path must start with /.'
    if not prompt_template:
        return 'Prompt template is required.'
    template_error = PitchTypeConfig.validate_template(prompt_template)
    if template_error:
        return template_error
    if not badge_color.startswith('#') or len(badge_color) != 7:
        return 'Badge color must be a 7-character hex value (e.g. #5aaa7a).'
    return ''


# ── Helpers ──────────────────────────────────────────────────────────────────────

def _get_type_colors() -> dict:
    """Return {name: badge_color} for all pitch types. Empty dict if DB unavailable."""
    try:
        from app.models.pitch_config import PitchTypeConfig
        return {c.name: c.badge_color for c in PitchTypeConfig.query.all()}
    except Exception:
        return {}


def _pending_gen_count() -> int:
    return APITaskQueue.query.filter_by(
        platform='pitch_machine', task_type='generate_draft', status='pending'
    ).count()


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
    from app.integrations.pitch_queue import (
        QUEUE_PATH, detect_fieldnames, parse_queue, serialize_queue,
    )
    try:
        content = get_or_create_queue_csv()
        # Derive fieldnames from the actual file header so no column is silently
        # dropped if the running code's COLUMNS doesn't yet match the CSV schema.
        fieldnames = detect_fieldnames(content)
        items = parse_queue(content)
        changed = False
        for item in items:
            if item.name == company_name and item.status == 'queued':
                item.status = 'pitched'
                changed = True
        if changed:
            upload_file(QUEUE_PATH, serialize_queue(items, fieldnames=fieldnames))
    except Exception as e:
        import sys
        print(f'[queue sheet] remove failed for {company_name!r}: {e}', file=sys.stderr)


def _mark_queue_sheet_not_a_fit(company_name: str, reason: str, hubspot_id: str = '') -> None:
    """Mark a queue item as 'not_a_fit' with an optional reason and rewrite the CSV.

    For hs: items that have no CSV row yet, appends a new row so the sync engine
    picks up the rejection on next run.
    """
    from app.integrations.dropbox_sync import (
        DropboxError,
        get_or_create_queue_csv,
        upload_file,
    )
    from app.integrations.pitch_queue import (
        QUEUE_PATH, QueueItem, detect_fieldnames, parse_queue, serialize_queue,
    )
    try:
        content = get_or_create_queue_csv()
        fieldnames = detect_fieldnames(content)
        items = parse_queue(content)
        changed = False
        for item in items:
            if item.name == company_name and item.status == 'queued':
                item.status = 'not_a_fit'
                item.not_a_fit_reason = reason
                changed = True
        if not changed and hubspot_id:
            items.append(QueueItem(
                name=company_name,
                hubspot_id=hubspot_id,
                status='not_a_fit',
                not_a_fit_reason=reason,
            ))
            changed = True
        if changed:
            upload_file(QUEUE_PATH, serialize_queue(items, fieldnames=fieldnames))
    except Exception as e:
        import sys
        print(f'[queue sheet] not_a_fit write failed for {company_name!r}: {e}', file=sys.stderr)


def _mirror_not_a_fit_to_hubspot(hubspot_id: str, company_name: str, reason: str) -> None:
    """Set hs_lead_status=UNQUALIFIED and log a note on the HubSpot company record.

    Best-effort: failures are logged but never raised. The CSV write must already
    have succeeded before this is called. Never read this status back — Portal is
    source of truth for not_a_fit.

    reason goes into a HubSpot Note (Activity feed) so Maeve can see *why*,
    not just *that* — without touching the 10/10 custom property cap.
    """
    import sys
    if not hubspot_id:
        return
    try:
        client = HubSpotClient()
        client.update_company(hubspot_id, {'hs_lead_status': 'UNQUALIFIED'})
        if reason:
            client.create_company_note(hubspot_id, f'Not a fit — {company_name}: {reason}')
    except Exception as e:
        print(f'[hubspot] not_a_fit mirror failed for {company_name!r}: {e}', file=sys.stderr)


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
