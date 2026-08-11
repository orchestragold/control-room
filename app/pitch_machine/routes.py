from collections import defaultdict
from datetime import date, datetime

from flask import Blueprint, abort, jsonify, render_template, request
from flask_login import current_user, login_required

from app.extensions import db
from app.integrations.hubspot import (
    HubSpotClient,
    HubSpotError,
    get_cached_companies,
    sync_companies_to_cache,
)
from app.models.hubspot_cache import HubSpotCompany
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

_NEEDS_OUTREACH_CAP = 15  # cards shown in Needs outreach before "+ N more"


def _require_access():
    if not current_user.has_project_access('pitch-machine'):
        abort(403)


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

    # Group by stage, excluding duplicates
    buckets: dict[PMStage, list] = defaultdict(list)
    for c in companies:
        stage = get_stage(c)
        if stage is not None:
            buckets[stage].append(c)

    # Sort within each column
    buckets[PMStage.NEEDS_OUTREACH].sort(key=lambda c: c.name.lower())
    buckets[PMStage.QUEUED].sort(key=lambda c: (c.reach_out_1 or date.max))
    buckets[PMStage.SENT].sort(key=lambda c: (c.reach_out_1 or date.min), reverse=True)
    for s in (PMStage.IN_NEGOTIATION, PMStage.CONFIRMED,
              PMStage.DEPRIORITIZED, PMStage.DECLINED, PMStage.NEEDS_REVIEW):
        buckets[s].sort(key=lambda c: c.name.lower())

    # Sync age string
    sync_age = None
    if companies:
        most_recent = max(c.last_synced_at for c in companies)
        minutes = int((datetime.utcnow() - most_recent).total_seconds() / 60)
        sync_age = f'{minutes}m ago' if minutes < 60 else f'{minutes // 60}h ago'

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
    hubspot_id = str(data.get('hubspot_id', '')).strip()
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

    cfg = stage_config(to_stage)
    new_status = cfg.hs_status  # always set for ALLOWED_MOVE_TARGETS

    try:
        client = HubSpotClient()
        client.update_company(hubspot_id, {'hs_lead_status': new_status})
        company.hs_lead_status = new_status
        db.session.commit()
        return jsonify({'ok': True, 'new_status': new_status})
    except HubSpotError as e:
        db.session.rollback()
        return jsonify({'error': str(e)}), 500
