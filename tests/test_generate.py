"""
Tests for the generate() route and generate-drafts CLI.

O1 (fixed in session 2): generate() now checks status IN ('pending','approved','sent'),
blocking a second draft for any company that already has an active approval.

D3 (fixed 2026-09-01): generate-drafts CLI was using _first_email(research_notes) as
a fallback for qs: items even when payload['email_address'] was present. The fix aligns
the CLI with the browser route: qs: items use email_address with no fallback, so an
empty TO is visible rather than silently wrong.
"""
import pytest
from unittest.mock import patch, MagicMock


GENERATE_URL = '/projects/orchestra-gold/pitch-machine/generate'


def _seed_hubspot_company(app, db, hubspot_id='hs-123', name='Acme Festival'):
    """Add a HubSpotCompany to the cache so generate() can find it."""
    from app.models.hubspot_cache import HubSpotCompany
    from datetime import date
    with app.app_context():
        company = HubSpotCompany(
            hubspot_id=hubspot_id,
            name=name,
            hs_lead_status='NEW',
            reach_out_1=date(2026, 9, 1),
            website='https://acmefest.com',
        )
        db.session.add(company)
        db.session.commit()
    return hubspot_id


def _seed_approval(app, db, hubspot_id, status):
    """Create a PitchApproval in the given status for a HubSpot company."""
    from app.models.pitch import PitchApproval
    with app.app_context():
        approval = PitchApproval(
            hubspot_contact_id=hubspot_id,
            company_name='Acme Festival',
            pitch_type='Festival',
            touch_number=1,
            draft_subject='Existing Subject',
            draft_body='<p>Existing body</p>',
            to_email='buyer@acmefest.com',
            cc_email='',
            status=status,
        )
        db.session.add(approval)
        db.session.commit()


class TestO1GenerateDedup:
    """O1: generate() should not create a second draft when one already exists."""

    def test_pending_approval_blocks_new_draft(self, app, db, client):
        """Baseline: status='pending' IS blocked (existing behavior)."""
        from app.models.pitch import PitchApproval

        hs_id = _seed_hubspot_company(app, db)
        _seed_approval(app, db, hs_id, status='pending')

        mock_draft = MagicMock()
        mock_draft.subject = 'New Subject'
        mock_draft.body = '<p>New body</p>'
        mock_draft.research_notes = ''

        with patch('app.integrations.hubspot.get_cached_companies') as mock_co:
            with patch('app.integrations.dropbox_sync.get_or_create_queue_csv', return_value=''):
                # Configure the mock company
                mock_company = MagicMock()
                mock_company.hubspot_id = hs_id
                mock_company.name = 'Acme Festival'
                mock_company.is_duplicate = False
                mock_company.website = None
                mock_company.description = None
                mock_co.return_value = [mock_company]

                response = client.post(GENERATE_URL, data={'entry_id': f'hs:{hs_id}'})

        with app.app_context():
            count = PitchApproval.query.filter_by(hubspot_contact_id=hs_id).count()
            assert count == 1, f"Expected 1 approval (blocked), got {count}"

    def test_approved_approval_blocks_new_draft(self, app, db, client):
        """O1 fixed: status='approved' blocks a new draft (no duplicate task queued)."""
        from app.models.pitch import PitchApproval

        hs_id = _seed_hubspot_company(app, db)
        _seed_approval(app, db, hs_id, status='approved')

        mock_draft = MagicMock()
        mock_draft.subject = 'New Subject'
        mock_draft.body = '<p>New body</p>'
        mock_draft.research_notes = ''

        with patch('app.integrations.claude_drafts.DraftGenerator') as MockGen:
            MockGen.return_value.generate.return_value = mock_draft
            with patch('app.integrations.hubspot.get_cached_companies') as mock_co:
                with patch('app.integrations.dropbox_sync.get_or_create_queue_csv', return_value=''):
                    mock_company = MagicMock()
                    mock_company.hubspot_id = hs_id
                    mock_company.name = 'Acme Festival'
                    mock_company.is_duplicate = False
                    mock_company.website = None
                    mock_company.description = None
                    mock_co.return_value = [mock_company]

                    client.post(GENERATE_URL, data={'entry_id': f'hs:{hs_id}'})

        with app.app_context():
            count = PitchApproval.query.filter_by(hubspot_contact_id=hs_id).count()
            assert count == 1, (
                f"O1 gap confirmed: {count} approvals exist for the same company. "
                "An 'approved' status did not prevent a second draft being generated. "
                "Fix: extend the dedup check to include status='approved' and status='sent'."
            )

    def test_sent_approval_blocks_new_draft(self, app, db, client):
        """O1 fixed: status='sent' blocks a new draft."""
        from app.models.pitch import PitchApproval

        hs_id = _seed_hubspot_company(app, db)
        _seed_approval(app, db, hs_id, status='sent')

        mock_draft = MagicMock()
        mock_draft.subject = 'New Subject'
        mock_draft.body = '<p>New body</p>'
        mock_draft.research_notes = ''

        with patch('app.integrations.claude_drafts.DraftGenerator') as MockGen:
            MockGen.return_value.generate.return_value = mock_draft
            with patch('app.integrations.hubspot.get_cached_companies') as mock_co:
                with patch('app.integrations.dropbox_sync.get_or_create_queue_csv', return_value=''):
                    mock_company = MagicMock()
                    mock_company.hubspot_id = hs_id
                    mock_company.name = 'Acme Festival'
                    mock_company.is_duplicate = False
                    mock_company.website = None
                    mock_company.description = None
                    mock_co.return_value = [mock_company]

                    client.post(GENERATE_URL, data={'entry_id': f'hs:{hs_id}'})

        with app.app_context():
            count = PitchApproval.query.filter_by(hubspot_contact_id=hs_id).count()
            assert count == 1, (
                f"O1 gap confirmed: {count} approvals exist for a 'sent' company. "
                "The portal can re-pitch someone who's already been contacted."
            )


class TestD3GenerateDraftsCLIEmail:
    """
    D3: generate-drafts CLI must use payload['email_address'] for qs: items,
    not fall back to _first_email(research_notes).

    Two paths that must agree: browser route (run_generate_next) and CLI
    (generate-drafts). The browser route has no fallback; the CLI previously
    did, and would silently pick the wrong address when notes contained emails.
    """

    def _seed_task(self, db, entry_type, email_address, research_notes_email):
        """
        Create a pending generate_draft task. research_notes_email is a DIFFERENT
        address embedded in the research notes — the test asserts it is NOT used.
        """
        from app.models.queue import APITaskQueue
        payload = {
            'entry_type': entry_type,
            'pitch_type': 'Festival',
            'send_date':  None,
        }
        if entry_type == 'hubspot':
            payload.update({'name': 'Acme Fest', 'hubspot_id': 'hs-d3', 'website': '', 'description': ''})
        else:
            payload.update({'item_name': 'Acme Fest', 'hubspot_id': '', 'notes': '',
                            'email_address': email_address})
        task = APITaskQueue(
            platform='pitch_machine', task_type='generate_draft',
            status='pending', payload=payload,
        )
        db.session.add(task)
        db.session.commit()
        return research_notes_email

    def _make_draft(self, to_in_notes):
        draft = MagicMock()
        draft.subject = 'Test subject'
        draft.body    = '<p>body</p>'
        # Research notes contain a DIFFERENT address — should not be picked for qs: items.
        draft.research_notes = f'Contact them at {to_in_notes} for bookings.'
        return draft

    def test_qs_uses_payload_email_not_notes(self, app, db, runner):
        """D3: qs: item → to_email comes from payload, ignoring research_notes."""
        from app.models.pitch import PitchApproval

        PAYLOAD_EMAIL = 'correct@venue.com'
        NOTES_EMAIL   = 'wrong@notes.com'

        with app.app_context():
            self._seed_task(db, 'queue_sheet', PAYLOAD_EMAIL, NOTES_EMAIL)

        draft = self._make_draft(NOTES_EMAIL)
        with patch('app.integrations.claude_drafts.DraftGenerator') as MockGen:
            MockGen.return_value.generate.return_value = draft
            with patch('app.integrations.dropbox_sync.get_or_create_queue_csv', return_value=''):
                with patch('app.integrations.dropbox_sync.sync_knowledge_to_cache'):
                    runner.invoke(args=['generate-drafts'])

        with app.app_context():
            approval = PitchApproval.query.first()
            assert approval is not None, 'No PitchApproval created by CLI'
            assert approval.to_email == PAYLOAD_EMAIL, (
                f'D3 regression: CLI used {approval.to_email!r} instead of '
                f'payload email_address {PAYLOAD_EMAIL!r}. '
                'The CLI fell back to _first_email(research_notes) for a qs: item.'
            )

    def test_qs_empty_email_stays_empty(self, app, db, runner):
        """D3: qs: item with no email_address → to_email is empty, not extracted from notes."""
        from app.models.pitch import PitchApproval

        NOTES_EMAIL = 'shouldnotuse@notes.com'

        with app.app_context():
            self._seed_task(db, 'queue_sheet', '', NOTES_EMAIL)

        draft = self._make_draft(NOTES_EMAIL)
        with patch('app.integrations.claude_drafts.DraftGenerator') as MockGen:
            MockGen.return_value.generate.return_value = draft
            with patch('app.integrations.dropbox_sync.get_or_create_queue_csv', return_value=''):
                with patch('app.integrations.dropbox_sync.sync_knowledge_to_cache'):
                    runner.invoke(args=['generate-drafts'])

        with app.app_context():
            approval = PitchApproval.query.first()
            assert approval is not None, 'No PitchApproval created by CLI'
            assert approval.to_email == '', (
                f'D3: empty email_address should stay empty, got {approval.to_email!r}. '
                'An empty TO is visible; a silently wrong one is not.'
            )
