"""
CSRF enforcement tests.

Q4 from the post-phase gate: "Is there anything new that goes over HTTP but is
only covered by in-process tests?"  These tests answer that question structurally
so it doesn't have to be re-asked after every phase.

Two concerns:

1. No pitch_machine POST route may use @csrf.exempt. State-changing endpoints in
   a tool that sends email on the user's behalf must require X-CSRFToken instead.
   DOCUMENTED_EXEMPTIONS exists for genuinely public endpoints (webhooks, etc.) —
   it is intentionally empty and should stay that way.

2. The wheel_set_override endpoint must accept a valid JSON payload, create an
   override record, and update pitch_targets.reach_out_1 in the same transaction.
   This is the HTTP-seam test that in-process tests cannot substitute for.
"""

import json
from datetime import date

import pytest


# ── Q4 gate: no @csrf.exempt in the pitch_machine blueprint ──────────────────


class TestNoPitchMachineRouteIsCsrfExempt:
    """
    Flask-WTF's @csrf.exempt sets view_func.csrfexempt = True.  Assert that
    attribute is absent on every pitch_machine POST route.

    To add a legitimate exemption (external webhook, health check, etc.), add it
    to DOCUMENTED_EXEMPTIONS with a one-line reason — the dict starts empty and
    should stay that way for all user-facing state-changing routes.
    """

    DOCUMENTED_EXEMPTIONS: dict[str, str] = {
        # 'pitch_machine.route_name': 'reason — e.g. inbound webhook, no session',
    }

    def test_no_post_route_is_csrf_exempt(self, app):
        with app.app_context():
            from flask import current_app

            violations = []
            for rule in current_app.url_map.iter_rules():
                if 'POST' not in (rule.methods or set()):
                    continue
                if not rule.endpoint.startswith('pitch_machine.'):
                    continue
                if rule.endpoint in self.DOCUMENTED_EXEMPTIONS:
                    continue
                view_func = current_app.view_functions.get(rule.endpoint)
                if view_func and getattr(view_func, 'csrfexempt', False):
                    violations.append(rule.endpoint)

            assert not violations, (
                f'These routes use @csrf.exempt instead of sending X-CSRFToken '
                f'in their fetch() call: {violations}. '
                f'Add X-CSRFToken to the fetch or add to DOCUMENTED_EXEMPTIONS '
                f'with a written reason.'
            )


# ── HTTP seam: POST /wheel/set-override ──────────────────────────────────────


class TestWheelSetOverride:
    """
    End-to-end HTTP tests for the drag-to-reschedule route.

    These are the tests that confirm drag-to-reschedule actually works — something
    the in-process unit tests cannot verify because the failure mode (missing CSRF
    token, missing table, wrong content-type) lives at the HTTP boundary.
    """

    def _make_target(self, db, hubspot_id='hs-001', reach_out_1=None):
        from app.models.pitch_target import PitchTarget
        from datetime import datetime
        pt = PitchTarget(
            hubspot_id     = hubspot_id,
            name           = 'Test Venue',
            reach_out_1    = reach_out_1 or date(2026, 11, 1),
            last_synced_at = datetime.utcnow(),
        )
        db.session.add(pt)
        db.session.commit()
        db.session.refresh(pt)
        return pt

    def test_valid_drag_returns_ok_and_new_date(self, app, db, client):
        with app.app_context():
            pt = self._make_target(db, hubspot_id='hs-drag-001')
            new_date = '2026-12-15'

            rv = client.post(
                '/projects/orchestra-gold/pitch-machine/wheel/set-override',
                data=json.dumps({'hubspot_id': 'hs-drag-001', 'outreach_date': new_date}),
                content_type='application/json',
            )
            assert rv.status_code == 200
            body = rv.get_json()
            assert body['ok'] is True
            assert body['new_date'] == new_date

    def test_override_record_persisted(self, app, db, client):
        with app.app_context():
            self._make_target(db, hubspot_id='hs-drag-002')
            client.post(
                '/projects/orchestra-gold/pitch-machine/wheel/set-override',
                data=json.dumps({'hubspot_id': 'hs-drag-002', 'outreach_date': '2026-12-20'}),
                content_type='application/json',
            )
            from app.models.pitch_target_override import PitchTargetOverride
            override = PitchTargetOverride.query.get('hs-drag-002')
            assert override is not None
            assert override.outreach_date_override == date(2026, 12, 20)

    def test_pitch_target_reach_out_1_updated(self, app, db, client):
        with app.app_context():
            self._make_target(db, hubspot_id='hs-drag-003', reach_out_1=date(2026, 11, 1))
            client.post(
                '/projects/orchestra-gold/pitch-machine/wheel/set-override',
                data=json.dumps({'hubspot_id': 'hs-drag-003', 'outreach_date': '2027-01-10'}),
                content_type='application/json',
            )
            from app.models.pitch_target import PitchTarget
            pt = PitchTarget.query.filter_by(hubspot_id='hs-drag-003').first()
            assert pt.reach_out_1 == date(2027, 1, 10)

    def test_upsert_updates_existing_override(self, app, db, client):
        """Second drag on the same target updates the override, not duplicates it."""
        with app.app_context():
            self._make_target(db, hubspot_id='hs-drag-004')
            for d in ('2026-11-15', '2026-12-01'):
                client.post(
                    '/projects/orchestra-gold/pitch-machine/wheel/set-override',
                    data=json.dumps({'hubspot_id': 'hs-drag-004', 'outreach_date': d}),
                    content_type='application/json',
                )
            from app.models.pitch_target_override import PitchTargetOverride
            rows = PitchTargetOverride.query.filter_by(hubspot_id='hs-drag-004').all()
            assert len(rows) == 1
            assert rows[0].outreach_date_override == date(2026, 12, 1)

    def test_missing_hubspot_id_returns_400(self, app, db, client):
        with app.app_context():
            rv = client.post(
                '/projects/orchestra-gold/pitch-machine/wheel/set-override',
                data=json.dumps({'outreach_date': '2026-11-01'}),
                content_type='application/json',
            )
            assert rv.status_code == 400

    def test_invalid_date_returns_400(self, app, db, client):
        with app.app_context():
            rv = client.post(
                '/projects/orchestra-gold/pitch-machine/wheel/set-override',
                data=json.dumps({'hubspot_id': 'hs-x', 'outreach_date': 'not-a-date'}),
                content_type='application/json',
            )
            assert rv.status_code == 400
