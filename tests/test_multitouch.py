"""
Multi-touch slice tests.

Covers:
  T1  — process_queue stores RFC Message-ID in approval.sent_message_id after Touch 1
  T2  — process_queue passes inReplyTo/references for Touch 2/3 sends
  T3  — _schedule_followup_touches computes correct send_date and scheduled_at
  T4  — _schedule_followup_touches does nothing when pitch type has no intervals
  T5  — run_generate_next (browser) creates 3 PitchApproval rows when pitch type is configured
  T5b — generate_drafts (CLI) creates 3 PitchApproval rows when pitch type is configured
  T6  — generate_drafts creates only 1 row when pitch type has no intervals
  T7  — cancel_replied_touches cancels pending Touch 2/3 on real reply
  T8  — cancel_replied_touches leaves Touch 2/3 pending on OOO subject
  T9  — cancel_replied_touches does not cancel Touch 1 (only followups)
  T10 — Festival - Cold CSV migration: pitched → Pitched Before; queued → Cold
  T11 — send_date / scheduled_at agreement (one test enforces their agreement)
  T12 — config_edit round-trips touch1_to_touch2_days through the form
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


# ── T3: generate-at-send-time — Touch 2 is created when Touch 1 sends ─────────
#
# Previously T3 tested _schedule_followup_touches (pre-scheduled at approval time).
# Architecture changed 2026-09-15: Touch 2/3 are generated inline by process_queue
# the moment their predecessor sends. _schedule_followup_touches is removed.
# T11 (scheduled_at agreement) is folded into this class.

def _seed_sequence_config(db, app, name='Festival - Cold', t1_to_t2=30, t2_to_t3=30):
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
                touch2_prompt='write t2',
                touch3_prompt='write t3',
            ))
            db.session.commit()


_MOCK_FOLLOWUP_STANDARD = ('<p>Follow-up body.</p>', 'Re: Test', '')
_MOCK_FOLLOWUP_PUBPROCESS = (
    '<p>I see you publish at fest.com/apply</p>',
    'Re: Test',
    'Version: published-process — festival publishes an online application',
)


class TestT3GenerateAtSendTime:
    """T3: Touch 2 is generated inline when Touch 1 sends.
    T11 (scheduled_at agreement) is also covered here."""

    def test_touch2_created_after_touch1_sends(self, app, db, runner):
        """process_queue creates a Touch 2 row (approved + scheduled) when Touch 1 sends."""
        _seed_sequence_config(db, app, t1_to_t2=30)

        touch1_id = _make_approval(db, app, touch_number=1, status='approved')
        _make_task(db, app, touch1_id, task_type='send_pitch_touch1')

        with patch('app.integrations.zoho_mail.send_email',
                   return_value={'data': {'messageId': '1'}}):
            with patch('app.integrations.zoho_mail.get_message_rfc_id', return_value=None):
                with patch('app.pitch_machine.cli._generate_followup_body',
                           return_value=_MOCK_FOLLOWUP_STANDARD):
                    runner.invoke(app.cli, ['process-queue'])

        with app.app_context():
            from app.models.pitch import PitchApproval
            from app.utils.dates import local_today
            rows = PitchApproval.query.filter(PitchApproval.touch_number > 1).all()
            assert len(rows) == 1, (
                f"Expected 1 Touch 2 row after Touch 1 sends, got {len(rows)}."
            )
            t2 = rows[0]
            assert t2.touch_number == 2
            assert t2.status == 'approved', (
                f"Touch 2 status={t2.status!r}; standard branch should be auto-approved."
            )
            expected_send = local_today() + timedelta(days=30)
            assert t2.send_date == expected_send, (
                f"Touch 2 send_date={t2.send_date}; expected today+30={expected_send}."
            )

    def test_scheduled_at_matches_send_date(self, app, db, runner):
        """Touch 2 task.scheduled_at must equal 09:00 Pacific on send_date, in UTC.
        Two representations of the same send moment must agree — T11."""
        from zoneinfo import ZoneInfo
        _seed_sequence_config(db, app, t1_to_t2=14)

        touch1_id = _make_approval(db, app, touch_number=1, status='approved')
        _make_task(db, app, touch1_id, task_type='send_pitch_touch1')

        with patch('app.integrations.zoho_mail.send_email',
                   return_value={'data': {'messageId': '1'}}):
            with patch('app.integrations.zoho_mail.get_message_rfc_id', return_value=None):
                with patch('app.pitch_machine.cli._generate_followup_body',
                           return_value=_MOCK_FOLLOWUP_STANDARD):
                    runner.invoke(app.cli, ['process-queue'])

        with app.app_context():
            from app.models.pitch import PitchApproval
            from app.models.queue import APITaskQueue
            t2 = PitchApproval.query.filter_by(touch_number=2).first()
            assert t2 is not None, "Touch 2 row not created"
            task = APITaskQueue.query.filter_by(task_type='send_pitch_touch2').first()
            assert task is not None, "No send_pitch_touch2 task created"

            sd = t2.send_date
            expected_utc = datetime(sd.year, sd.month, sd.day, 9, 0, 0,
                                    tzinfo=ZoneInfo('America/Los_Angeles')
                                    ).astimezone(ZoneInfo('UTC')).replace(tzinfo=None)
            assert task.scheduled_at == expected_utc, (
                f"scheduled_at={task.scheduled_at!r} ≠ {expected_utc!r}. "
                "Two representations of the send moment must agree: "
                "approval.send_date and task.scheduled_at both encode the same local date, "
                "and scheduled_at must be 09:00 Pacific converted to UTC."
            )

    def test_published_process_branch_creates_pending_row(self, app, db, runner):
        """Published-process branch in Touch 2 notes → status='pending', no send task."""
        _seed_sequence_config(db, app)

        touch1_id = _make_approval(db, app, touch_number=1, status='approved')
        _make_task(db, app, touch1_id, task_type='send_pitch_touch1')

        with patch('app.integrations.zoho_mail.send_email',
                   return_value={'data': {'messageId': '1'}}):
            with patch('app.integrations.zoho_mail.get_message_rfc_id', return_value=None):
                with patch('app.pitch_machine.cli._generate_followup_body',
                           return_value=_MOCK_FOLLOWUP_PUBPROCESS):
                    runner.invoke(app.cli, ['process-queue'])

        with app.app_context():
            from app.models.pitch import PitchApproval
            from app.models.queue import APITaskQueue
            t2 = PitchApproval.query.filter_by(touch_number=2).first()
            assert t2 is not None, "Touch 2 row not created"
            assert t2.status == 'pending', (
                f"Touch 2 status={t2.status!r}; published-process branch must hold for review."
            )
            task = APITaskQueue.query.filter_by(task_type='send_pitch_touch2').first()
            assert task is None, (
                "No send task should be queued for a pending Touch 2 (published-process branch)."
            )

    def test_touch2_research_notes_are_its_own_brief(self, app, db, runner):
        """Defect 2 regression: Touch 2's research_notes must be its own parsed brief,
        not a copy of Touch 1's brief."""
        _seed_sequence_config(db, app)

        touch1_id = _make_approval(db, app, touch_number=1, status='approved')
        _make_task(db, app, touch1_id, task_type='send_pitch_touch1')

        t2_notes = 'Talent buyer: Jane Smith (confirmed via website)'
        mock_followup = ('<p>Touch 2 body.</p>', 'Re: Test', t2_notes)

        with patch('app.integrations.zoho_mail.send_email',
                   return_value={'data': {'messageId': '1'}}):
            with patch('app.integrations.zoho_mail.get_message_rfc_id', return_value=None):
                with patch('app.pitch_machine.cli._generate_followup_body',
                           return_value=mock_followup):
                    runner.invoke(app.cli, ['process-queue'])

        with app.app_context():
            from app.models.pitch import PitchApproval
            t1 = db.session.get(PitchApproval, touch1_id)
            t2 = PitchApproval.query.filter_by(touch_number=2).first()
            assert t2 is not None
            assert t2.research_notes == t2_notes, (
                f"Touch 2 research_notes={t2.research_notes!r}; "
                "must be Touch 2's own parsed brief, not Touch 1's."
            )
            # Touch 1's notes are separate
            assert t2.research_notes != t1.research_notes or t1.research_notes == t2_notes, (
                "Touch 1 and Touch 2 briefs must be independently stored."
            )


# ── T4: no interval → no Touch 2 generated ────────────────────────────────────

class TestT4NoIntervals:
    def test_no_followup_when_interval_null(self, app, db):
        """_generate_and_schedule_followup is a no-op when interval is None."""
        from app.models.pitch import PitchApproval
        from app.models.pitch_config import PitchTypeConfig
        from app.models.queue import APITaskQueue
        from app.pitch_machine.cli import _generate_and_schedule_followup

        with app.app_context():
            if PitchTypeConfig.query.filter_by(name='WAA').first() is None:
                db.session.add(PitchTypeConfig(
                    name='WAA',
                    archive_dropbox_path='/WAA pitches.docx',
                    prompt_template='Draft {name} {website} {description}',
                    badge_color='#5a7aaa',
                    touch1_to_touch2_days=None,
                ))
                db.session.commit()

            t1 = PitchApproval(
                hubspot_contact_id='', company_name='WAA Presenter',
                pitch_type='WAA', touch_number=1,
                draft_subject='S', draft_body='<p>X</p>',
                to_email='p@waa.com', cc_email='', status='sent',
            )
            db.session.add(t1)
            db.session.commit()
            initial_approval_count = PitchApproval.query.count()
            initial_task_count     = APITaskQueue.query.count()

            _generate_and_schedule_followup(t1, next_touch_num=2)
            db.session.commit()

            assert PitchApproval.query.count() == initial_approval_count, (
                "No PitchApproval should be created when touch1_to_touch2_days is None."
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


# ── T5: run_generate_next creates 3 rows ──────────────────────────────────────

def _seed_pitch_type_with_sequence(db, app, name='Festival - Cold'):
    from app.models.pitch_config import PitchTypeConfig
    with app.app_context():
        if PitchTypeConfig.query.filter_by(name=name).first() is None:
            db.session.add(PitchTypeConfig(
                name=name,
                archive_dropbox_path='/test.docx',
                prompt_template='Draft {name} {website} {description}',
                badge_color='#5aaa7a',
                active=True,
                touch1_to_touch2_days=30,
                touch2_to_touch3_days=30,
                touch2_prompt='Write a brief Touch 2 follow-up for the target.',
                touch3_prompt='Write a graceful close-out for the target.',
            ))
            db.session.commit()


def _seed_generate_task(db, app, pitch_type='Festival - Cold', hubspot_id='hs-t5'):
    from app.models.queue import APITaskQueue
    with app.app_context():
        task = APITaskQueue(
            platform='pitch_machine',
            task_type='generate_draft',
            status='pending',
            payload={
                'entry_type': 'hubspot',
                'hubspot_id': hubspot_id,
                'pitch_type': pitch_type,
                'name': 'T5 Festival',
                'website': 'https://t5fest.com',
                'description': 'A test festival for T5',
                'send_date': None,
            },
        )
        db.session.add(task)
        db.session.commit()


def _mock_draft():
    from unittest.mock import MagicMock
    d = MagicMock()
    d.subject = 'Orchestra GOLD ✱ T5 Festival 2027'
    d.body    = '<p>Touch 1 body.</p>'
    d.research_notes = 'Contact: buyer@t5fest.com'
    return d


class TestT5BrowserPathCreatesOneRow:
    """T5: run_generate_next creates exactly 1 PitchApproval (Touch 1) at generation
    time, regardless of whether the pitch type has a sequence configured.
    Touch 2/3 are generated at send time by process_queue."""

    def test_one_row_created_via_browser_path(self, app, db, client):
        from app.models.pitch import PitchApproval

        _seed_pitch_type_with_sequence(db, app)
        _seed_generate_task(db, app)

        with patch('app.integrations.claude_drafts.DraftGenerator') as MockGen:
            MockGen.return_value.generate.return_value = _mock_draft()
            resp = client.post(
                '/projects/orchestra-gold/pitch-machine/run-generate-next',
                content_type='application/json',
            )

        assert resp.status_code == 200
        data = resp.get_json()
        assert 'error' not in data, f"run_generate_next returned error: {data.get('error')}"

        with app.app_context():
            rows = PitchApproval.query.all()
            assert len(rows) == 1, (
                f"Expected 1 PitchApproval row (Touch 1 only at generation time), got {len(rows)}. "
                "Touch 2/3 are generated at send time, not at generation time."
            )
            assert rows[0].touch_number == 1
            assert rows[0].status == 'pending'

    def test_one_row_when_no_sequence_configured(self, app, db, client):
        """T6: pitch type with no intervals → still only Touch 1 created (unchanged)."""
        from app.models.pitch import PitchApproval
        from app.models.pitch_config import PitchTypeConfig

        with app.app_context():
            if PitchTypeConfig.query.filter_by(name='WAA').first() is None:
                db.session.add(PitchTypeConfig(
                    name='WAA',
                    archive_dropbox_path='/waa.docx',
                    prompt_template='Draft {name} {website} {description}',
                    badge_color='#5a7aaa',
                    active=True,
                    touch1_to_touch2_days=None,
                ))
                db.session.commit()

        _seed_generate_task(db, app, pitch_type='WAA', hubspot_id='hs-t6')

        with patch('app.integrations.claude_drafts.DraftGenerator') as MockGen:
            MockGen.return_value.generate.return_value = _mock_draft()
            client.post(
                '/projects/orchestra-gold/pitch-machine/run-generate-next',
                content_type='application/json',
            )

        with app.app_context():
            rows = PitchApproval.query.all()
            assert len(rows) == 1
            assert rows[0].touch_number == 1


# ── T5b: CLI path also creates 3 rows ─────────────────────────────────────────

class TestT5bCLIPathCreatesOneRow:
    """T5b: generate-drafts CLI also creates only Touch 1 at generation time.
    Both run_generate_next and generate-drafts share _process_generate_draft_task —
    this test is a regression guard that they stay in sync."""

    def test_cli_creates_one_row(self, app, db, runner):
        from app.models.pitch import PitchApproval

        _seed_pitch_type_with_sequence(db, app)
        _seed_generate_task(db, app)

        with patch('app.integrations.claude_drafts.DraftGenerator') as MockGen:
            MockGen.return_value.generate.return_value = _mock_draft()
            with patch('app.integrations.dropbox_sync.get_or_create_queue_csv',
                       return_value=''):
                with patch('app.integrations.dropbox_sync.sync_knowledge_to_cache'):
                    runner.invoke(app.cli, ['generate-drafts'])

        with app.app_context():
            rows = PitchApproval.query.order_by(PitchApproval.touch_number).all()
            assert len(rows) == 1, (
                f"Expected 1 PitchApproval row (Touch 1 only) from CLI path, got {len(rows)}. "
                "Touch 2/3 are generated at send time by process_queue."
            )
            assert rows[0].touch_number == 1


# ── T12: config_edit round-trips touch1_to_touch2_days ────────────────────────

class TestT12ConfigEditInterval:
    """T12: editing a pitch type via the form must persist touch1_to_touch2_days.
    This test would have caught the gap where the DB had the column but the route
    never read it from the form."""

    CONFIG_URL = '/projects/orchestra-gold/pitch-machine/config'

    def _create_pitch_type(self, db, app):
        from app.models.pitch_config import PitchTypeConfig
        with app.app_context():
            pt = PitchTypeConfig(
                name='T12 Type',
                archive_dropbox_path='/t12.docx',
                prompt_template='Draft {name} {website} {description}',
                badge_color='#888888',
                active=True,
            )
            db.session.add(pt)
            db.session.commit()
            return pt.id

    def test_interval_persisted_after_edit(self, app, db, client):
        from app.models.pitch_config import PitchTypeConfig

        tid = self._create_pitch_type(db, app)

        resp = client.post(
            f'{self.CONFIG_URL}/{tid}/edit',
            data={
                'name':                  'T12 Type',
                'archive_dropbox_path':  '/t12.docx',
                'prompt_template':       'Draft {name} {website} {description}',
                'badge_color':           '#888888',
                'sort_order':            '0',
                'is_cyclical':           '1',
                'touch1_to_touch2_days': '21',
                'touch2_to_touch3_days': '14',
                'touch2_prompt':         'Write a brief follow-up.',
                'touch3_prompt':         'Write a graceful close-out.',
            },
            follow_redirects=False,
        )
        assert resp.status_code in (302, 200), f"Unexpected status {resp.status_code}"

        with app.app_context():
            pt = PitchTypeConfig.query.get(tid)
            assert pt.touch1_to_touch2_days == 21, (
                f"touch1_to_touch2_days={pt.touch1_to_touch2_days!r}; expected 21. "
                "The config_edit route did not save the interval from the form field."
            )
            assert pt.touch2_to_touch3_days == 14
            assert pt.touch2_prompt == 'Write a brief follow-up.'
            assert pt.touch3_prompt == 'Write a graceful close-out.'

    def test_blank_interval_saves_as_null(self, app, db, client):
        """Submitting an empty interval field must store NULL, not 0 or ''."""
        from app.models.pitch_config import PitchTypeConfig

        tid = self._create_pitch_type(db, app)

        client.post(
            f'{self.CONFIG_URL}/{tid}/edit',
            data={
                'name':                  'T12 Type',
                'archive_dropbox_path':  '/t12.docx',
                'prompt_template':       'Draft {name} {website} {description}',
                'badge_color':           '#888888',
                'sort_order':            '0',
                'is_cyclical':           '1',
                'touch1_to_touch2_days': '',
                'touch2_to_touch3_days': '',
                'touch2_prompt':         '',
                'touch3_prompt':         '',
            },
        )

        with app.app_context():
            pt = PitchTypeConfig.query.get(tid)
            assert pt.touch1_to_touch2_days is None, (
                f"touch1_to_touch2_days={pt.touch1_to_touch2_days!r}; expected None. "
                "A blank interval must not schedule any follow-up touches."
            )
