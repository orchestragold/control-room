"""
Tests for the approve() route — covers C3 from the code review.

C3: Dropbox and HubSpot writes must happen AFTER db.session.commit().
Before the fix, the writes happened before the commit. If the commit
failed, external systems would reflect a "pitched" state but no send
task existed — the item silently disappeared from the queue forever.

With the fix: commit first (approval + task row durably created), THEN
external writes. If those fail, the task row still exists and
process-queue will send the email.
"""
from unittest.mock import patch, MagicMock, call


APPROVE_URL = '/projects/orchestra-gold/pitch-machine/approve/{pid}'
APPROVE_FORM = {
    'subject':    'Test Subject',
    'body':       '<p>Test body</p>',
    'to_email':   'buyer@festival.com',
    'cc_email':   '',
    'send_date':  '',
}


def _make_pending_approval(app, db):
    from app.models.pitch import PitchApproval
    with app.app_context():
        approval = PitchApproval(
            hubspot_contact_id='',
            company_name='Test Festival',
            pitch_type='Festival',
            touch_number=1,
            draft_subject='Original Subject',
            draft_body='<p>Original body</p>',
            to_email='',
            cc_email='',
            status='pending',
        )
        db.session.add(approval)
        db.session.commit()
        db.session.refresh(approval)
        return approval.id


class TestC3ApproveOrdering:
    """C3: external writes happen after the commit, not before."""

    def test_approve_creates_task_row_on_success(self, app, db, client):
        """Baseline: approve() creates an APITaskQueue row when commit succeeds."""
        from app.models.queue import APITaskQueue

        pid = _make_pending_approval(app, db)

        with patch('app.pitch_machine.routes._remove_from_queue_sheet'):
            with patch('app.pitch_machine.routes._write_hubspot_reach_out_1', return_value=None):
                response = client.post(APPROVE_URL.format(pid=pid), data=APPROVE_FORM)

        assert response.status_code in (200, 302)

        with app.app_context():
            task = APITaskQueue.query.filter_by(
                platform='zoho_mail', task_type='send_pitch_touch1'
            ).first()
            assert task is not None, "No APITaskQueue row created — approve() may have failed"
            assert task.status == 'pending'

    def test_external_writes_happen_after_commit(self, app, db, client):
        """
        C3 ordering test: Dropbox and HubSpot writes happen AFTER db.session.commit().
        We verify this by checking that when _remove_from_queue_sheet is called,
        the approval row is already committed to the DB (status='approved').
        """
        from app.models.pitch import PitchApproval

        pid = _make_pending_approval(app, db)
        approval_status_at_dropbox_call = []

        def spy_dropbox(company_name):
            # What does the DB say at the moment this runs?
            with app.app_context():
                a = db.session.get(PitchApproval, pid)
                approval_status_at_dropbox_call.append(a.status if a else 'NOT FOUND')

        with patch('app.pitch_machine.routes._remove_from_queue_sheet', side_effect=spy_dropbox):
            with patch('app.pitch_machine.routes._write_hubspot_reach_out_1', return_value=None):
                client.post(APPROVE_URL.format(pid=pid), data=APPROVE_FORM)

        assert len(approval_status_at_dropbox_call) == 1, \
            "_remove_from_queue_sheet was not called"
        assert approval_status_at_dropbox_call[0] == 'approved', (
            f"At Dropbox write time, approval.status='{approval_status_at_dropbox_call[0]}'. "
            "Expected 'approved' — this means the commit happened BEFORE the Dropbox write. "
            "If 'pending': C3 fix is broken; the write happens before the commit."
        )

    def test_commit_precedes_external_writes_in_call_order(self, app, db, client):
        """
        C3 ordering proof: db.session.commit() must appear in the call log
        BEFORE _remove_from_queue_sheet and _write_hubspot_reach_out_1.

        This is the precise property the C3 fix establishes. If commit fails,
        the external writes are never reached (they come after in the code).
        We verify the ordering using a call log spy rather than actually failing
        commits (which triggers SQLAlchemy session teardown and masks the assertion).
        """
        pid = _make_pending_approval(app, db)
        call_log = []

        from app.extensions import db as ext_db
        real_commit = ext_db.session.commit

        def spy_commit():
            call_log.append('commit')
            return real_commit()

        def spy_dropbox(company_name):
            call_log.append('dropbox')

        def spy_hubspot(hs_id, send_date):
            call_log.append('hubspot')
            return None

        with patch.object(ext_db.session, 'commit', side_effect=spy_commit):
            with patch('app.pitch_machine.routes._remove_from_queue_sheet',
                       side_effect=spy_dropbox):
                with patch('app.pitch_machine.routes._write_hubspot_reach_out_1',
                           side_effect=spy_hubspot):
                    client.post(APPROVE_URL.format(pid=pid), data=APPROVE_FORM)

        assert 'commit' in call_log, "approve() never called db.session.commit()"
        assert 'dropbox' in call_log, "_remove_from_queue_sheet was never called"

        # The last commit before the external writes is the approve() commit.
        # All commits must precede the first external write.
        first_external = min(
            (call_log.index('dropbox') if 'dropbox' in call_log else len(call_log)),
            (call_log.index('hubspot') if 'hubspot' in call_log else len(call_log)),
        )
        last_commit = max(i for i, x in enumerate(call_log) if x == 'commit')

        assert last_commit < first_external, (
            f"Call order: {call_log}\n"
            f"Last commit at index {last_commit}, "
            f"first external write at index {first_external}.\n"
            "C3 fix requires commit to come BEFORE external writes. "
            "If external writes come first, a commit failure would leave "
            "Dropbox/HubSpot out of sync with no send task created."
        )
