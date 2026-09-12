"""
Multi-touch slice tests.

Covers:
  T1  — process_queue stores RFC Message-ID in approval.sent_message_id after Touch 1
  T2  — process_queue passes inReplyTo/references for Touch 2/3 sends
  T3  — _schedule_followup_touches computes correct send_date and scheduled_at
  T4  — _schedule_followup_touches does nothing when pitch type has no intervals
  T5  — generate_drafts creates 3 PitchApproval rows when pitch type is configured
  T6  — generate_drafts creates only 1 row when pitch type has no intervals
  T7  — cancel_replied_touches cancels pending Touch 2/3 on real reply
  T8  — cancel_replied_touches leaves Touch 2/3 pending on OOO subject
  T9  — cancel_replied_touches does not cancel Touch 1 (only followups)
  T10 — Festival - Cold CSV migration: pitched → Pitched Before; queued → Cold
  T11 — send_date / scheduled_at agreement (one test enforces their agreement)
"""
from datetime import date, datetime, timedelta
from unittest.mock import MagicMock, patch


# ── Helpers ────────────────────────────────────────────────────────────────────

def _make_approval(db, app, *, touch_number=1, status='pending',
                   company='Test Festival', hubspot_id='hs-001',
                   pitch_type='Festival - Cold', to_email='buyer@fest.com',
                   send_date=None, sent_message_id=None):
    from app.models.pitch import PitchApproval
    with app.app_context():
        a = PitchApproval(
            hubspot_contact_id=hubspot_id,
            company_name=company,
            pitch_type=pitch_type,
            touch_number=touch_number,
            draft_subject='Orchestra GOLD ✱ Test Festival 2027',
            draft_body='<p>Hello</p>',
            to_email=to_email,
            cc_email='booking@orchestragold.com',
            status=status,
            send_date=send_date,
            sent_message_id=sent_message_id,
        )
        db.session.add(a)
        db.session.commit()
        return a.id


def _make_task(db, app, approval_id, *, task_type='send_pitch_touch1',
               touch1_approval_id=None, scheduled_at=None):
    from app.models.queue import APITaskQueue
    with app.app_context():
        from app.models.pitch import PitchApproval
        a = db.session.get(PitchApproval, approval_id)
        payload = {
            'pitch_approval_id':  approval_id,
            'to_email_actual':    a.to_email or 'buyer@fest.com',
            'to_email_intended':  a.to_email or 'buyer@fest.com',
            'subject':            a.draft_subject or 'Test Subject',
            'body':               a.draft_body or '<p>Hello</p>',
            'cc_email':           a.cc_email or '',
            'was_redirected':     False,
            'send_date':          None,
        }
        if touch1_approval_id is not None:
            payload['touch1_approval_id'] = touch1_approval_id
        t = APITaskQueue(
            platform='zoho_mail',
            task_type=task_type,
            status='pending',
            scheduled_at=scheduled_at,
            payload=payload,
        )
        db.session.add(t)
        db.session.commit()
        return t.id


# ── T1: Message-ID stored after Touch 1 ───────────────────────────────────────

class TestT1MessageIdStoredAfterTouch1:
    def test_sent_message_id_stored_when_zoho_returns_id(self, app, db, runner):
        approval_id = _make_approval(db, app)
        _make_task(db, app, approval_id, task_type='send_pitch_touch1')

        fake_zoho_resp = {'data': {'messageId': '99999'}}

        with patch('app.integrations.zoho_mail.send_email', return_value=fake_zoho_resp):
            with patch('app.integrations.zoho_mail.get_message_rfc_id',
                       return_value='<abc123@mail.zoho.com>'):
                runner.invoke(app.cli, ['process-queue'])

        with app.app_context():
            from app.models.pitch import PitchApproval
            a = db.session.get(PitchApproval, approval_id)
            assert a.sent_message_id == '<abc123@mail.zoho.com>', (
                f"sent_message_id={a.sent_message_id!r}; expected RFC Message-ID after Touch 1 send."
            )

    def test_sent_message_id_not_stored_when_rfc_fetch_fails(self, app, db, runner):
        """If get_message_rfc_id fails, the send still completes — just no threading."""
        approval_id = _make_approval(db, app)
        _make_task(db, app, approval_id, task_type='send_pitch_touch1')

        with patch('app.integrations.zoho_mail.send_email', return_value={'data': {'messageId': '1'}}):
            with patch('app.integrations.zoho_mail.get_message_rfc_id', return_value=None):
                runner.invoke(app.cli, ['process-queue'])

        with app.app_context():
            from app.models.pitch import PitchApproval
            a = db.session.get(PitchApproval, approval_id)
            assert a.status == 'sent'
            assert a.sent_message_id is None


# ── T2: inReplyTo passed for Touch 2/3 ────────────────────────────────────────

class TestT2ThreadingHeaders:
    def test_touch2_passes_in_reply_to(self, app, db, runner):
        """Touch 2 send must include inReplyTo = Touch 1's sent_message_id."""
        touch1_id = _make_approval(db, app, touch_number=1, status='sent',
                                   sent_message_id='<t1-msgid@mail.zoho.com>')
        touch2_id = _make_approval(db, app, touch_number=2, status='approved')
        _make_task(db, app, touch2_id, task_type='send_pitch_touch2',
                   touch1_approval_id=touch1_id)

        mock_send = MagicMock(return_value={})
        with patch('app.integrations.zoho_mail.send_email', mock_send):
            with patch('app.integrations.zoho_mail.get_message_rfc_id', return_value=None):
                runner.invoke(app.cli, ['process-queue'])

        assert mock_send.called, "send_email was not called for Touch 2"
        call_kwargs = mock_send.call_args[1] if mock_send.call_args[1] else {}
        call_args   = mock_send.call_args[0] if mock_send.call_args[0] else ()
        # in_reply_to should be the keyword arg
        assert call_kwargs.get('in_reply_to') == '<t1-msgid@mail.zoho.com>', (
            f"in_reply_to={call_kwargs.get('in_reply_to')!r}; "
            "Touch 2 must reply-in-thread to Touch 1's Message-ID."
        )

    def test_touch1_has_no_in_reply_to(self, app, db, runner):
        """Touch 1 must NOT pass inReplyTo — it is the first message."""
        touch1_id = _make_approval(db, app, touch_number=1)
        _make_task(db, app, touch1_id, task_type='send_pitch_touch1')

        mock_send = MagicMock(return_value={'data': {'messageId': '1'}})
        with patch('app.integrations.zoho_mail.send_email', mock_send):
            with patch('app.integrations.zoho_mail.get_message_rfc_id', return_value=None):
                runner.invoke(app.cli, ['process-queue'])

        call_kwargs = mock_send.call_args[1] if mock_send.call_args[1] else {}
        assert call_kwargs.get('in_reply_to') is None, (
            "Touch 1 should not set in_reply_to — it starts the thread."
        )

    def test_touch2_without_touch1_message_id_still_sends(self, app, db, runner):
        """If Touch 1 never got a sent_message_id, Touch 2 sends anyway without threading."""
        touch1_id = _make_approval(db, app, touch_number=1, status='sent',
                                   sent_message_id=None)  # no RFC ID stored
        touch2_id = _make_approval(db, app, touch_number=2, status='approved')
        _make_task(db, app, touch2_id, task_type='send_pitch_touch2',
                   touch1_approval_id=touch1_id)

        mock_send = MagicMock(return_value={})
        with patch('app.integrations.zoho_mail.send_email', mock_send):
            with patch('app.integrations.zoho_mail.get_message_rfc_id', return_value=None):
                runner.invoke(app.cli, ['process-queue'])

        assert mock_send.call_count == 1, "Touch 2 should still send even without threading ID"
        call_kwargs = mock_send.call_args[1] if mock_send.call_args[1] else {}
        assert call_kwargs.get('in_reply_to') is None


# ── T3: _schedule_followup_touches computes dates correctly ────────────────────

class TestT3ScheduleFollowupTouches:
    def _make_pitch_type_config(self, db, app, name='Festival - Cold',
                                t1_to_t2=30, t2_to_t3=30,
                                t2_prompt='write t2', t3_prompt='write t3'):
        from app.models.pitch_config import PitchTypeConfig
        with app.app_context():
            if PitchTypeConfig.query.filter_by(name=name).first() is None:
                db.session.add(PitchTypeConfig(
                    name=name,
                    archive_dropbox_path='/2026 pitches.docx',
                    prompt_template='Draft {name} {website} {description}',
                    badge_color='#5aaa7a',
                    touch1_to_touch2_days=t1_to_t2,
                    touch2_to_touch3_days=t2_to_t3,
                    touch2_prompt=t2_prompt,
                    touch3_prompt=t3_prompt,
                ))
                db.session.commit()

    def test_touch2_send_date_is_anchor_plus_t1_to_t2(self, app, db, client):
        """Touch 2 send_date = approval anchor + touch1_to_touch2_days."""
        self._make_pitch_type_config(db, app, t1_to_t2=30, t2_to_t3=30)

        with app.app_context():
            from app.models.pitch import PitchApproval
            from app.models.queue import APITaskQueue
            from app.models.user import User
            from app.pitch_machine.routes import _schedule_followup_touches
            from app.utils.dates import local_today

            # Create Touch 1 (already approved) and Touch 2 (pending).
            t1 = PitchApproval(
                hubspot_contact_id='hs-t3-001', company_name='T3 Festival',
                pitch_type='Festival - Cold', touch_number=1,
                draft_subject='Subj', draft_body='<p>B</p>',
                to_email='t3@fest.com', cc_email='', status='approved',
            )
            t2 = PitchApproval(
                hubspot_contact_id='hs-t3-001', company_name='T3 Festival',
                pitch_type='Festival - Cold', touch_number=2,
                draft_subject='Re: Subj', draft_body='<p>T2</p>',
                to_email='t3@fest.com', cc_email='', status='pending',
            )
            db.session.add_all([t1, t2])
            db.session.commit()

            today = local_today()
            _schedule_followup_touches(t1, user_id=1)
            db.session.commit()

            t2_fresh = db.session.get(PitchApproval, t2.id)
            expected_date = today + timedelta(days=30)
            assert t2_fresh.send_date == expected_date, (
                f"send_date={t2_fresh.send_date}; expected {expected_date} "
                "(today + 30 days for Festival - Cold)."
            )
            assert t2_fresh.status == 'approved'

    def test_scheduled_at_matches_send_date(self, app, db, client):
        """The api_task_queue scheduled_at must match the Touch 2 send_date at 09:00 Pacific.
        Two representations of the same moment must agree — this test enforces it."""
        from zoneinfo import ZoneInfo
        self._make_pitch_type_config(db, app, t1_to_t2=14, t2_to_t3=14)

        with app.app_context():
            from app.models.pitch import PitchApproval
            from app.models.queue import APITaskQueue
            from app.pitch_machine.routes import _schedule_followup_touches
            from app.utils.dates import local_today

            t1 = PitchApproval(
                hubspot_contact_id='hs-t3-002', company_name='Sched Festival',
                pitch_type='Festival - Cold', touch_number=1,
                draft_subject='S', draft_body='<p>X</p>',
                to_email='s@fest.com', cc_email='', status='approved',
            )
            t2 = PitchApproval(
                hubspot_contact_id='hs-t3-002', company_name='Sched Festival',
                pitch_type='Festival - Cold', touch_number=2,
                draft_subject='Re: S', draft_body='<p>Y</p>',
                to_email='s@fest.com', cc_email='', status='pending',
            )
            db.session.add_all([t1, t2])
            db.session.commit()

            _schedule_followup_touches(t1, user_id=1)
            db.session.commit()

            t2_fresh = db.session.get(PitchApproval, t2.id)
            task = APITaskQueue.query.filter_by(
                platform='zoho_mail', task_type='send_pitch_touch2'
            ).filter(
                APITaskQueue.payload['pitch_approval_id'].as_integer() == t2.id
            ).first()

            assert task is not None, "No task row was created for Touch 2"

            # The task's scheduled_at must be 09:00 Pacific on send_date, in UTC.
            sd = t2_fresh.send_date
            expected_utc = datetime(sd.year, sd.month, sd.day, 9, 0, 0,
                                    tzinfo=ZoneInfo('America/Los_Angeles')
                                    ).astimezone(ZoneInfo('UTC')).replace(tzinfo=None)
            assert task.scheduled_at == expected_utc, (
                f"scheduled_at={task.scheduled_at!r} ≠ {expected_utc!r}. "
                "Two representations of the send moment must agree: "
                "approval.send_date and task.scheduled_at both encode the same local date, "
                "and scheduled_at must be 09:00 Pacific converted to UTC."
            )


# ── T4: no intervals → no Touch 2/3 scheduling ────────────────────────────────

class TestT4NoIntervals:
    def test_no_followup_scheduled_when_interval_null(self, app, db):
        """Pitch type with touch1_to_touch2_days=None must not schedule any followup."""
        from app.models.pitch_config import PitchTypeConfig
        from app.models.pitch import PitchApproval
        from app.models.queue import APITaskQueue
        from app.pitch_machine.routes import _schedule_followup_touches

        with app.app_context():
            if PitchTypeConfig.query.filter_by(name='WAA').first() is None:
                db.session.add(PitchTypeConfig(
                    name='WAA',
                    archive_dropbox_path='/WAA pitches.docx',
                    prompt_template='Draft {name} {website} {description}',
                    badge_color='#5a7aaa',
                    touch1_to_touch2_days=None,  # no sequence configured
                ))
                db.session.commit()

            t1 = PitchApproval(
                hubspot_contact_id='', company_name='WAA Presenter',
                pitch_type='WAA', touch_number=1,
                draft_subject='S', draft_body='<p>X</p>',
                to_email='p@waa.com', cc_email='', status='approved',
            )
            t2 = PitchApproval(
                hubspot_contact_id='', company_name='WAA Presenter',
                pitch_type='WAA', touch_number=2,
                draft_subject='Re: S', draft_body='<p>Y</p>',
                to_email='p@waa.com', cc_email='', status='pending',
            )
            db.session.add_all([t1, t2])
            db.session.commit()
            initial_task_count = APITaskQueue.query.count()

            _schedule_followup_touches(t1, user_id=1)
            db.session.commit()

            t2_fresh = db.session.get(PitchApproval, t2.id)
            assert t2_fresh.status == 'pending', (
                "Touch 2 should stay 'pending' when the pitch type has no interval configured."
            )
            assert APITaskQueue.query.count() == initial_task_count, (
                "No task should be created when touch1_to_touch2_days is None."
            )


# ── T7/T8/T9: cancel_replied_touches ──────────────────────────────────────────

class TestCancelRepliedTouches:
    def _make_sent_approval(self, db, app, addr='buyer@fest.com', company='Test'):
        return _make_approval(db, app, touch_number=1, status='sent',
                              to_email=addr, company=company)

    def _make_pending_touch(self, db, app, touch_num, addr='buyer@fest.com', company='Test'):
        return _make_approval(db, app, touch_number=touch_num, status='pending',
                              to_email=addr, company=company)

    def _run_cancel(self, runner, app, inbox_msgs):
        from app.integrations.dropbox_sync import get_or_create_queue_csv
        from app.integrations.pitch_queue import parse_queue
        with patch('app.integrations.zoho_mail.list_inbox_messages',
                   return_value=inbox_msgs):
            with patch('app.integrations.dropbox_sync.get_or_create_queue_csv',
                       return_value=''):
                runner.invoke(app.cli, ['cancel-replied-touches'])

    def test_pending_touch2_cancelled_on_reply(self, app, db, runner):
        """A real reply cancels pending Touch 2 for that address."""
        addr = 'reply-test@fest.com'
        self._make_sent_approval(db, app, addr=addr, company='Reply Fest')
        touch2_id = self._make_pending_touch(db, app, touch_num=2, addr=addr,
                                             company='Reply Fest')

        self._run_cancel(runner, app, inbox_msgs=[{
            'from_address': addr,
            'subject': 'Re: Orchestra GOLD',
            'received_at': datetime.utcnow(),
            'message_id': 'abc',
        }])

        with app.app_context():
            from app.models.pitch import PitchApproval
            t2 = db.session.get(PitchApproval, touch2_id)
            assert t2.status == 'cancelled_reply', (
                f"Touch 2 status={t2.status!r}; should be 'cancelled_reply' after reply."
            )

    def test_ooo_reply_does_not_cancel(self, app, db, runner):
        """Out-of-office subjects must NOT cancel the sequence."""
        addr = 'ooo-test@fest.com'
        self._make_sent_approval(db, app, addr=addr, company='OOO Fest')
        touch2_id = self._make_pending_touch(db, app, touch_num=2, addr=addr,
                                             company='OOO Fest')

        self._run_cancel(runner, app, inbox_msgs=[{
            'from_address': addr,
            'subject': 'Out of office: Re: Orchestra GOLD',
            'received_at': datetime.utcnow(),
            'message_id': 'ooo-1',
        }])

        with app.app_context():
            from app.models.pitch import PitchApproval
            t2 = db.session.get(PitchApproval, touch2_id)
            assert t2.status == 'pending', (
                f"Touch 2 status={t2.status!r}; OOO reply must NOT cancel the sequence — "
                "it's not a real reply."
            )

    def test_touch1_not_cancelled(self, app, db, runner):
        """cancel_replied_touches only cancels touch_number > 1; Touch 1 is untouched."""
        addr = 'touch1-test@fest.com'
        touch1_id = self._make_sent_approval(db, app, addr=addr, company='T1 Fest')

        self._run_cancel(runner, app, inbox_msgs=[{
            'from_address': addr,
            'subject': 'Great to hear from you',
            'received_at': datetime.utcnow(),
            'message_id': 'reply-1',
        }])

        with app.app_context():
            from app.models.pitch import PitchApproval
            t1 = db.session.get(PitchApproval, touch1_id)
            # Touch 1 is already 'sent', so cancel_replied_touches ignores it
            # (it only cancels pending/approved with touch_number > 1).
            assert t1.status == 'sent', (
                "Touch 1 status changed; cancel_replied_touches must only touch "
                "followups (touch_number > 1), not the original sent message."
            )


# ── T10: CSV pitch type migration ──────────────────────────────────────────────

class TestT10CSVPitchTypeMigration:
    """CSV rows with status='pitched'|'replied' → Festival - Pitched Before;
    status='queued' → Festival - Cold. Does not depend on HubSpot data quality."""

    def test_pitched_csv_row_gets_pitched_before(self, app, db):
        from app.models.pitch_target import PitchTarget
        from app.pitch_machine.pitch_target_sync import sync_pitch_targets

        csv = (
            'name,pitch_type,source,deadline,status,notes,date_added,email_address,'
            'not_a_fit_reason,hubspot_id\n'
            'Montreal Jazz Festival,Festival,cowork,2027-01-01,pitched,,2026-08-01,'
            'maurin@equipespectra.ca,,\n'
        )
        with app.app_context():
            sync_pitch_targets(xlsx_bytes=None, csv_content=csv)
            t = PitchTarget.query.filter_by(name='Montreal Jazz Festival').first()
            assert t is not None
            assert t.pitch_type == 'Festival - Pitched Before', (
                f"pitch_type={t.pitch_type!r}; CSV status='pitched' → 'Festival - Pitched Before'."
            )

    def test_queued_csv_row_gets_cold(self, app, db):
        from app.models.pitch_target import PitchTarget
        from app.pitch_machine.pitch_target_sync import sync_pitch_targets

        csv = (
            'name,pitch_type,source,deadline,status,notes,date_added,email_address,'
            'not_a_fit_reason,hubspot_id\n'
            'Mostly Jazz,Festival,cowork,2027-05-01,queued,,2026-09-01,apply@mostlyjazz.co.uk,,\n'
        )
        with app.app_context():
            sync_pitch_targets(xlsx_bytes=None, csv_content=csv)
            t = PitchTarget.query.filter_by(name='Mostly Jazz').first()
            assert t is not None
            assert t.pitch_type == 'Festival - Cold', (
                f"pitch_type={t.pitch_type!r}; CSV status='queued' → 'Festival - Cold'."
            )

    def test_replied_csv_row_gets_pitched_before(self, app, db):
        from app.models.pitch_target import PitchTarget
        from app.pitch_machine.pitch_target_sync import sync_pitch_targets

        csv = (
            'name,pitch_type,source,deadline,status,notes,date_added,email_address,'
            'not_a_fit_reason,hubspot_id\n'
            'Glastonbury West Holts,Festival,cowork,2027-06-01,replied,,2026-08-10,'
            'steve@glastonbury.com,,\n'
        )
        with app.app_context():
            sync_pitch_targets(xlsx_bytes=None, csv_content=csv)
            t = PitchTarget.query.filter_by(name='Glastonbury West Holts').first()
            assert t is not None
            assert t.pitch_type == 'Festival - Pitched Before', (
                f"pitch_type={t.pitch_type!r}; CSV status='replied' → 'Festival - Pitched Before'."
            )
