"""
Tests for the generate() route — covers the O1 gap from the code review.

O1: generate() deduplication only checks status='pending'. An 'approved'
or 'sent' approval for the same company does NOT block a second draft
from being created — which could eventually lead to double-sending.

This test is EXPECTED TO FAIL until O1 is fixed, since it documents a
known gap that rides with Session G. A failure here is informational,
not a regression.
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

    @pytest.mark.xfail(
        reason="O1 not yet fixed — generate() only checks status='pending', not 'approved'/'sent'. "
               "This test documents the gap; it will be fixed in Session G.",
        strict=True,
    )
    def test_approved_approval_blocks_new_draft(self, app, db, client):
        """
        O1 gap: status='approved' should block a new draft but currently DOES NOT.
        Marked xfail — this test is expected to fail until O1 is implemented.
        A company with an 'approved' (queued-to-send) approval can be drafted again,
        creating two approvals that could result in two sends to the same person.
        """
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

    @pytest.mark.xfail(
        reason="O1 not yet fixed — same gap as approved, just the 'sent' variant.",
        strict=True,
    )
    def test_sent_approval_blocks_new_draft(self, app, db, client):
        """
        O1 gap: status='sent' should also block a new draft but currently does not.
        A company that's already been pitched can be re-drafted and re-sent.
        """
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
