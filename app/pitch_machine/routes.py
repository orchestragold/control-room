from collections import defaultdict
from datetime import date, datetime

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
from app.extensions import db
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

_NEEDS_OUTREACH_CAP    = 15   # cards shown in "Needs outreach" before "+ N more"
_DRAFT_QUEUE_MAX_SHOWN = 50   # companies shown in the draft-selection checklist
_GENERATE_BATCH_LIMIT  = 5    # max companies per synchronous generation batch

_DEFAULT_CC = 'booking@orchestragold.com'


def _require_access():
    if not current_user.has_project_access('pitch-machine'):
        abort(403)


# ── Kanban board ────────────────────────────────────────────────────────────────

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
    for c in companies:
        stage = get_stage(c)
        if stage is not None:
            buckets[stage].append(c)

    buckets[PMStage.NEEDS_OUTREACH].sort(key=lambda c: c.name.lower())
    buckets[PMStage.QUEUED].sort(key=lambda c: (c.reach_out_1 or date.max))
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
def sync():
    _require_access()
    try:
        count = sync_companies_to_cache()
        return jsonify({'ok': True, 'count': count})
    except HubSpotError as e:
        return jsonify({'ok': False, 'error': str(e)}), 500


@pm_bp.route('/move', methods=['POST'])
@login_required
def move():
    _require_access()

    if not request.is_json:
        return jsonify({'error': 'JSON required'}), 400

    data = request.get_json(silent=True) or {}
    hubspot_id    = str(data.get('hubspot_id', '')).strip()
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


# ── Draft queue (festival selection) ────────────────────────────────────────────

@pm_bp.route('/draft-queue')
@login_required
def draft_queue():
    _require_access()

    try:
        companies = get_cached_companies()
    except HubSpotError as e:
        flash(str(e), 'error')
        return redirect(url_for('pitch_machine.board'))

    today = date.today()

    # Decision #5 (Session 14): only show Festival companies with an upcoming
    # reach_out_1 date set — not the full company list. Sorted nearest-first.
    # Excludes: duplicates, already-pitched (SENT and beyond), and companies
    # whose reach_out_1 is in the past (legacy calendar entries fall off here).
    queued_upcoming = sorted(
        [
            c for c in companies
            if not c.is_duplicate
            and c.reach_out_1 is not None
            and c.reach_out_1 >= today
            and get_stage(c) == PMStage.QUEUED
        ],
        key=lambda c: c.reach_out_1,
    )[:_DRAFT_QUEUE_MAX_SHOWN]

    # Flag companies that already have a pending draft
    existing_pending = {
        a.hubspot_contact_id
        for a in PitchApproval.query.filter_by(status='pending').all()
    }

    from app.integrations.dropbox_sync import DropboxError, get_knowledge_content
    knowledge_ready = True
    try:
        get_knowledge_content()
    except DropboxError:
        knowledge_ready = False

    return render_template(
        'pitch_machine/draft_queue.html',
        companies=queued_upcoming,
        existing_pending=existing_pending,
        knowledge_ready=knowledge_ready,
        batch_limit=_GENERATE_BATCH_LIMIT,
    )


# ── Generate drafts ─────────────────────────────────────────────────────────────

@pm_bp.route('/generate', methods=['POST'])
@login_required
def generate():
    _require_access()

    hubspot_ids = request.form.getlist('hubspot_id')
    if not hubspot_ids:
        flash('Select at least one festival.', 'error')
        return redirect(url_for('pitch_machine.draft_queue'))

    if len(hubspot_ids) > _GENERATE_BATCH_LIMIT:
        flash(
            f'Select up to {_GENERATE_BATCH_LIMIT} festivals per batch '
            f'({len(hubspot_ids)} selected).',
            'error',
        )
        return redirect(url_for('pitch_machine.draft_queue'))

    from app.integrations.claude_drafts import DraftGenerationError, DraftGenerator

    try:
        generator = DraftGenerator()
    except DraftGenerationError as e:
        flash(str(e), 'error')
        return redirect(url_for('pitch_machine.draft_queue'))

    errors: list[str] = []
    created = 0

    for hs_id in hubspot_ids:
        company = HubSpotCompany.query.filter_by(hubspot_id=hs_id).first()
        if company is None or company.is_duplicate:
            continue

        # Skip if a pending draft already exists for this company
        existing = PitchApproval.query.filter_by(
            hubspot_contact_id=hs_id, status='pending'
        ).first()
        if existing:
            errors.append(f'{company.name}: pending draft already exists — skipped')
            continue

        try:
            draft = generator.generate(
                name=company.name,
                website=company.website,
                description=company.description,
            )
        except DraftGenerationError as e:
            errors.append(f'{company.name}: {e}')
            continue

        approval = PitchApproval(
            hubspot_contact_id=hs_id,
            company_name=company.name,
            touch_number=1,
            draft_subject=draft.subject,
            draft_body=draft.body,
            research_notes=draft.research_notes,
            cc_email=_DEFAULT_CC,
            status='pending',
        )
        db.session.add(approval)
        created += 1

    db.session.commit()

    if errors:
        for err in errors:
            flash(err, 'error')

    if created:
        flash(
            f'{created} draft{"s" if created != 1 else ""} ready for review.',
            'success',
        )

    return redirect(url_for('pitch_machine.review'))


# ── Touch 1 review / approve ─────────────────────────────────────────────────────

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
    body     = request.form.get('body', '').strip()
    to_email = request.form.get('to_email', '').strip()
    cc_email = request.form.get('cc_email', '').strip()

    if not to_email:
        flash('Enter a recipient email before approving.', 'error')
        return redirect(url_for('pitch_machine.review'))
    if not body:
        flash('Draft body cannot be empty.', 'error')
        return redirect(url_for('pitch_machine.review'))

    actual_recipient, was_redirected = resolve_email_recipient(to_email)
    send_subject = f'[TEST → {to_email}] {subject}' if was_redirected else subject

    approval.draft_subject = subject
    approval.draft_body    = body
    approval.to_email      = to_email
    approval.cc_email      = cc_email
    approval.status        = 'approved'
    approval.approved_by   = current_user.id
    approval.approved_at   = datetime.utcnow()

    db.session.add(ApprovalLog(
        approver_id=current_user.id,
        action='approved',
        entity_type='pitch_approval',
        entity_id=str(pid),
        details={
            'company_name':    approval.company_name,
            'to_email':        to_email,
            'actual_recipient': actual_recipient,
            'was_redirected':  was_redirected,
            'subject':         subject,
        },
    ))

    db.session.add(APITaskQueue(
        platform='zoho_mail',
        task_type='send_pitch_touch1',
        payload={
            'pitch_approval_id': pid,
            'to_email_intended': to_email,
            'to_email_actual':   actual_recipient,
            'cc_email':          cc_email,
            'subject':           send_subject,
            'body':              body,
            'was_redirected':    was_redirected,
        },
        created_by=current_user.id,
    ))

    db.session.commit()

    if was_redirected:
        flash(
            f'Approved — queued to send to {actual_recipient} '
            f'(test mode; intended: {to_email}).',
            'success',
        )
    else:
        flash(f'Approved — queued to send to {to_email}.', 'success')

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
        approver_id=current_user.id,
        action='rejected',
        entity_type='pitch_approval',
        entity_id=str(pid),
        details={'company_name': approval.company_name},
    ))
    db.session.commit()

    flash(f'Draft for {approval.company_name} rejected.', 'success')
    return redirect(url_for('pitch_machine.review'))


def _get_redirect_email_display() -> str:
    from app.core.mode import get_test_redirect_email
    return get_test_redirect_email()
