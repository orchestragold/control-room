"""
Tests for process-queue CLI command — covers C1 and C2 from the code review.

C1: After send_email() succeeds, the task is committed as 'completed' before
    anything else. If subsequent DB work fails, the task is NOT left at
    'processing' (where it would be orphaned forever — process-queue only
    queries status='pending').

C2: The except block wraps only send_email(). An exception in the
    post-send success code (approval update, HubSpot write) must NOT
    increment retry_count or re-queue the task — that would cause a
    duplicate send on the next cron tick.
"""
from unittest.mock import patch, MagicMock, call


def _make_task_and_approval(app, db, *, approval_status='approved'):
    """Create a linked APITaskQueue + PitchApproval and return their IDs."""
    from app.models.pitch import PitchApproval
    from app.models.queue import APITaskQueue

    with app.app_context():
        approval = PitchApproval(
            hubspot_contact_id='',
            company_name='Acme Festival',
            pitch_type='Festival',
            touch_number=1,
            draft_subject='Hi',
            draft_body='<p>Hello</p>',
            to_email='buyer@acmefest.com',
            cc_email='',
            status=approval_status,
        )
        db.session.add(approval)
        db.session.flush()

        task = APITaskQueue(
            platform='zoho_mail',
            task_type='send_pitch_touch1',
            status='pending',
            payload={
                'pitch_approval_id': approval.id,
                'to_email_actual': 'buyer@acmefest.com',
                'to_email_intended': 'buyer@acmefest.com',
                'subject': 'Hi',
                'body': '<p>Hello</p>',
                'cc_email': '',
                'was_redirected': False,
                'send_date': None,
            },
        )
        db.session.add(task)
        db.session.commit()
        return task.id, approval.id


# ── C1 ────────────────────────────────────────────────────────────────────────

class TestC1TaskNotOrphaned:
    """C1: task is committed 'completed' before post-send work; won't get stuck."""

    def test_successful_send_marks_task_completed(self, app, db, runner):
        """Happy path: after send_email() succeeds, task.status == 'completed'."""
        task_id, _ = _make_task_and_approval(app, db)

        with patch('app.integrations.zoho_mail.send_email', return_value={}):
            runner.invoke(app.cli, ['process-queue'])

        with app.app_context():
            from app.models.queue import APITaskQueue
            task = db.session.get(APITaskQueue, task_id)
            assert task.status == 'completed', (
                f"Expected 'completed', got '{task.status}'. "
                "Task may be stuck — check process-queue split-commit fix."
            )

    def test_task_completed_even_when_postprocess_raises(self, app, db, runner):
        """
        C1 critical scenario: send_email() succeeds, then post-send code raises.
        Task must be 'completed' (not 'processing') so it won't be re-sent.
        With the fix, task='completed' is committed before approval/HubSpot work.
        """
        task_id, _ = _make_task_and_approval(app, db)

        # Replace the entire PitchApproval class so that query.get() raises.
        # We patch the module attribute — the CLI's lazy `from app.models.pitch
        # import PitchApproval` picks up this replacement at call time.
        # (Patching only `.query` fails because it's a SQLAlchemy descriptor.)
        mock_pa = MagicMock()
        mock_pa.query.get.side_effect = RuntimeError('Simulated post-send DB failure')

        with patch('app.integrations.zoho_mail.send_email', return_value={}):
            with patch('app.models.pitch.PitchApproval', mock_pa):
                runner.invoke(app.cli, ['process-queue'])

        with app.app_context():
            from app.models.queue import APITaskQueue
            task = db.session.get(APITaskQueue, task_id)
            assert task.status == 'completed', (
                f"Task status is '{task.status}' — expected 'completed'. "
                "If 'processing': C1 fix is not working; task is orphaned and "
                "the send is unrecorded. The split-commit must happen before "
                "the approval/HubSpot update block."
            )


# ── C2 ────────────────────────────────────────────────────────────────────────

class TestC2NoDuplicateSend:
    """C2: exception in post-send code must not trigger a retry."""

    def test_send_email_called_exactly_once_on_success(self, app, db, runner):
        """Happy path baseline: send_email is called once, task completes."""
        task_id, _ = _make_task_and_approval(app, db)
        mock_send = MagicMock(return_value={})

        with patch('app.integrations.zoho_mail.send_email', mock_send):
            runner.invoke(app.cli, ['process-queue'])

        assert mock_send.call_count == 1

    def test_postprocess_exception_does_not_increment_retry_count(self, app, db, runner):
        """
        C2 critical scenario: post-send code raises. Before the fix, the broad
        except block would catch it, increment retry_count, and set status back
        to 'pending' — causing a duplicate send on the next cron tick.

        With the fix, the except only wraps send_email(). A post-send exception
        propagates naturally; retry_count stays 0 and the task stays 'completed'.
        """
        task_id, _ = _make_task_and_approval(app, db)
        mock_send = MagicMock(return_value={})
        mock_pa = MagicMock()
        mock_pa.query.get.side_effect = RuntimeError('Simulated post-send failure')

        with patch('app.integrations.zoho_mail.send_email', mock_send):
            with patch('app.models.pitch.PitchApproval', mock_pa):
                runner.invoke(app.cli, ['process-queue'])

        with app.app_context():
            from app.models.queue import APITaskQueue
            task = db.session.get(APITaskQueue, task_id)

            assert mock_send.call_count == 1, (
                f"send_email called {mock_send.call_count}× — expected 1. "
                "Duplicate send risk detected."
            )
            assert task.retry_count == 0, (
                f"retry_count={task.retry_count}, expected 0. "
                "Post-send exception incorrectly triggered the retry logic — "
                "this would cause send_email to be called again on next cron tick."
            )
            assert task.status == 'completed', (
                f"Task status is '{task.status}' — expected 'completed'. "
                "If 'pending': task would be re-sent on next run."
            )

    def test_second_process_queue_run_does_not_resend(self, app, db, runner):
        """
        C2 end-to-end: even if the first run has a post-send failure, a second
        invocation of process-queue must NOT call send_email again.
        """
        task_id, _ = _make_task_and_approval(app, db)
        mock_send = MagicMock(return_value={})
        mock_pa = MagicMock()
        mock_pa.query.get.side_effect = RuntimeError('post-send failure')

        # First run: send succeeds, post-send raises → task becomes 'completed'
        with patch('app.integrations.zoho_mail.send_email', mock_send):
            with patch('app.models.pitch.PitchApproval', mock_pa):
                runner.invoke(app.cli, ['process-queue'])

        # Second run: task is already 'completed', must not be re-picked up
        with patch('app.integrations.zoho_mail.send_email', mock_send):
            runner.invoke(app.cli, ['process-queue'])

        assert mock_send.call_count == 1, (
            f"send_email called {mock_send.call_count}× across two runs. "
            "Second run should have found no pending tasks — the task is 'completed'."
        )


# ── Scheduled send ────────────────────────────────────────────────────────────

class TestScheduledAt:
    """process-queue must not fire tasks whose scheduled_at is in the future."""

    def test_future_scheduled_task_not_sent(self, app, db, runner):
        """A task with scheduled_at tomorrow must not be sent today."""
        from datetime import timedelta
        from unittest.mock import MagicMock, patch
        from app.models.pitch import PitchApproval
        from app.models.queue import APITaskQueue
        from datetime import datetime

        with app.app_context():
            approval = PitchApproval(
                hubspot_contact_id='', company_name='Future Festival',
                pitch_type='Festival', touch_number=1,
                draft_subject='Hi', draft_body='<p>Hello</p>',
                to_email='x@y.com', cc_email='', status='approved',
            )
            db.session.add(approval)
            db.session.flush()

            task = APITaskQueue(
                platform='zoho_mail', task_type='send_pitch_touch1',
                status='pending',
                scheduled_at=datetime.utcnow() + timedelta(days=1),
                payload={
                    'pitch_approval_id': approval.id,
                    'to_email_actual': 'x@y.com',
                    'to_email_intended': 'x@y.com',
                    'subject': 'Hi', 'body': '<p>Hello</p>',
                    'cc_email': '', 'was_redirected': False, 'send_date': None,
                },
            )
            db.session.add(task)
            db.session.commit()
            task_id = task.id

        mock_send = MagicMock(return_value={})
        with patch('app.integrations.zoho_mail.send_email', mock_send):
            runner.invoke(app.cli, ['process-queue'])

        assert mock_send.call_count == 0, (
            "send_email was called for a future-scheduled task — "
            "process-queue must filter on scheduled_at <= NOW()."
        )
        with app.app_context():
            t = db.session.get(APITaskQueue, task_id)
            assert t.status == 'pending', (
                f"Task status changed to {t.status!r}; it should remain 'pending' "
                "until its scheduled_at date arrives."
            )

    def test_past_scheduled_task_is_sent(self, app, db, runner):
        """A task with scheduled_at already past must be sent normally."""
        from datetime import timedelta
        from unittest.mock import MagicMock, patch
        from app.models.pitch import PitchApproval
        from app.models.queue import APITaskQueue
        from datetime import datetime

        with app.app_context():
            approval = PitchApproval(
                hubspot_contact_id='', company_name='Past Festival',
                pitch_type='Festival', touch_number=1,
                draft_subject='Hi', draft_body='<p>Hello</p>',
                to_email='a@b.com', cc_email='', status='approved',
            )
            db.session.add(approval)
            db.session.flush()

            task = APITaskQueue(
                platform='zoho_mail', task_type='send_pitch_touch1',
                status='pending',
                scheduled_at=datetime.utcnow() - timedelta(hours=1),
                payload={
                    'pitch_approval_id': approval.id,
                    'to_email_actual': 'a@b.com',
                    'to_email_intended': 'a@b.com',
                    'subject': 'Hi', 'body': '<p>Hello</p>',
                    'cc_email': '', 'was_redirected': False, 'send_date': None,
                },
            )
            db.session.add(task)
            db.session.commit()

        mock_send = MagicMock(return_value={})
        with patch('app.integrations.zoho_mail.send_email', mock_send):
            runner.invoke(app.cli, ['process-queue'])

        assert mock_send.call_count == 1, (
            "send_email was not called for a past-scheduled task — "
            "process-queue should send tasks whose scheduled_at has passed."
        )
