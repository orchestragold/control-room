"""
Round-trip tests for the pitch_targets sync engine (G-1).

Coverage:
  - Stage mapping pure functions
  - Spreadsheet XLSX parsing (fixture XLSX, no Dropbox)
  - DB round-trip: write → read back → assert all columns survived

The failure mode these tests guard against: a silent field-drop in the
sync write path where a column in the record dict doesn't make it into
the DB row. The "all_columns_survive" tests verify every column has the
expected value after the write-read cycle.
"""

from __future__ import annotations

import io
from datetime import date, datetime

import openpyxl
import pytest

from app.models.hubspot_cache import HubSpotCompany
from app.models.pitch_target import PitchTarget
from app.pitch_machine.pitch_target_sync import (
    SPREADSHEET_SHEET_NAME,
    SyncResult,
    compute_stage,
    csv_status_to_stage,
    derive_hs_stage,
    normalize_name,
    parse_spreadsheet_bytes,
    spreadsheet_status_to_stage,
    sync_pitch_targets,
)
from app.pitch_machine.stages import PMStage, get_stage

# ── Fixture helpers ───────────────────────────────────────────────────────────

def _make_xlsx(rows: list[list], sheet_name: str = SPREADSHEET_SHEET_NAME) -> bytes:
    """Build an in-memory XLSX from a list of rows (first row is header)."""
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = sheet_name
    for row in rows:
        ws.append(row)
    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()


_EMPTY_CSV = (
    'name,pitch_type,source,deadline,status,notes,'
    'date_added,hubspot_id,email_address,not_a_fit_reason\n'
)

_SAMPLE_CSV = (
    'name,pitch_type,source,deadline,status,notes,'
    'date_added,hubspot_id,email_address,not_a_fit_reason\n'
    'KBOO 90.7 FM,PNW Tour - Media,cowork,2026-09-15,queued,'
    'Brandon Lieberman,2026-08-18,,news@kboo.fm,\n'
    'Williams Center for the Arts,Festival,cowork,2027-06-01,not_a_fit,'
    ',,,,too institutional\n'
)


def _make_company(**kwargs) -> HubSpotCompany:
    defaults = dict(
        hubspot_id     = 'hs-test',
        name           = 'Test Festival',
        hs_lead_status = None,
        reach_out_1    = None,
        last_synced_at = datetime.utcnow(),
    )
    defaults.update(kwargs)
    return HubSpotCompany(**defaults)


# ── Stage mapping (pure functions, no DB) ─────────────────────────────────────

class TestSpreadsheetStatusToStage:
    def test_pitch_sent(self):
        assert spreadsheet_status_to_stage('Pitch Sent') == PMStage.SENT.value

    def test_pitch_sent_with_suffix(self):
        assert spreadsheet_status_to_stage('Pitch Sent (Gmail)') == PMStage.SENT.value

    def test_pitch_scheduled(self):
        assert spreadsheet_status_to_stage('Pitch Scheduled') == PMStage.QUEUED.value

    def test_pitch_scheduled_with_suffix(self):
        assert spreadsheet_status_to_stage('Pitch Scheduled (Gmail)') == PMStage.QUEUED.value

    def test_no_outreach_needed(self):
        assert spreadsheet_status_to_stage('No Outreach Needed') == PMStage.DEPRIORITIZED.value

    def test_inactive(self):
        assert spreadsheet_status_to_stage('INACTIVE') == PMStage.DECLINED.value

    def test_inactive_with_suffix(self):
        assert spreadsheet_status_to_stage('INACTIVE - venue closed') == PMStage.DECLINED.value

    def test_unrecognized_returns_none(self):
        # Free-text research-workflow notes are unmappable — return None (not
        # NEEDS_OUTREACH) so compute_stage can treat them as 'no signal' rather
        # than 'says NEEDS_OUTREACH', eliminating false conflicts with HubSpot.
        assert spreadsheet_status_to_stage('Research Phase') is None
        assert spreadsheet_status_to_stage('TBD') is None
        assert spreadsheet_status_to_stage('Buyer Named - Generic Contact Only') is None

    def test_empty_string(self):
        assert spreadsheet_status_to_stage('') == PMStage.NEEDS_OUTREACH.value

    def test_none(self):
        assert spreadsheet_status_to_stage(None) == PMStage.NEEDS_OUTREACH.value


class TestCsvStatusToStage:
    def test_queued(self):
        assert csv_status_to_stage('queued') == PMStage.QUEUED.value

    def test_pitched(self):
        assert csv_status_to_stage('pitched') == PMStage.SENT.value

    def test_removed(self):
        assert csv_status_to_stage('removed') == PMStage.DECLINED.value

    def test_not_a_fit(self):
        # not_a_fit maps to DEPRIORITIZED; the not_a_fit flag is the exclusion mechanism
        assert csv_status_to_stage('not_a_fit') == PMStage.DEPRIORITIZED.value

    def test_none(self):
        assert csv_status_to_stage(None) == PMStage.NEEDS_OUTREACH.value


class TestDeriveHsStage:
    def test_attempted_to_contact(self):
        assert derive_hs_stage('ATTEMPTED_TO_CONTACT', None) == PMStage.SENT.value

    def test_connected(self):
        assert derive_hs_stage('CONNECTED', None) == PMStage.IN_NEGOTIATION.value

    def test_bad_timing(self):
        assert derive_hs_stage('BAD_TIMING', None) == PMStage.DEPRIORITIZED.value

    def test_unqualified(self):
        assert derive_hs_stage('UNQUALIFIED', None) == PMStage.DECLINED.value

    def test_open_deal(self):
        assert derive_hs_stage('OPEN_DEAL', None) == PMStage.CONFIRMED.value

    def test_open(self):
        assert derive_hs_stage('OPEN', None) == PMStage.NEEDS_REVIEW.value

    def test_new_is_queued(self):
        assert derive_hs_stage('NEW', None) == PMStage.QUEUED.value

    def test_none_with_reach_out_1_is_queued(self):
        assert derive_hs_stage(None, date(2026, 9, 1)) == PMStage.QUEUED.value

    def test_none_no_reach_out_is_needs_outreach(self):
        assert derive_hs_stage(None, None) == PMStage.NEEDS_OUTREACH.value


class TestNormalizeName:
    def test_strips_and_lowercases(self):
        assert normalize_name('  WOMAD UK  ') == 'womad uk'

    def test_already_normalized(self):
        assert normalize_name('womad') == 'womad'

    def test_mixed_case(self):
        assert normalize_name('Primavera Sound') == 'primavera sound'


# ── get_stage / derive_hs_stage parity ───────────────────────────────────────

class TestGetStageDeriveHsStageParity:
    """
    get_stage (kanban board) and derive_hs_stage (pitch_targets sync) must
    agree on every hs_lead_status value. If one is changed, the test fails,
    forcing the other to be updated at the same time.

    These functions cannot share an implementation without a larger refactor,
    so this test is the enforcement mechanism.
    """

    _KNOWN_STATUSES = [
        'ATTEMPTED_TO_CONTACT',
        'CONNECTED',
        'BAD_TIMING',
        'UNQUALIFIED',
        'OPEN_DEAL',
        'OPEN',
        'IN_PROGRESS',
        'NEW',
        None,
    ]

    def test_all_statuses_agree_without_reach_out_1(self, app):
        with app.app_context():
            for status in self._KNOWN_STATUSES:
                company    = _make_company(hs_lead_status=status, reach_out_1=None)
                board      = get_stage(company)
                sync_stage = derive_hs_stage(status, None)
                assert board is not None, f"get_stage returned None for {status!r}"
                assert board.value == sync_stage, (
                    f"hs_lead_status={status!r}: "
                    f"get_stage={board.value!r} but derive_hs_stage={sync_stage!r}. "
                    "Update both functions together."
                )

    def test_none_status_with_reach_out_1_both_return_queued(self, app):
        from datetime import date
        with app.app_context():
            company    = _make_company(hs_lead_status=None, reach_out_1=date(2026, 9, 1))
            board      = get_stage(company)
            sync_stage = derive_hs_stage(None, date(2026, 9, 1))
            assert board.value == sync_stage == PMStage.QUEUED.value


# ── Spreadsheet XLSX parsing (no DB) ─────────────────────────────────────────

class TestParseSpreadsheetBytes:
    def test_parses_name_and_status(self):
        xlsx = _make_xlsx([
            ['Festival Name', 'Status', 'HubSpot Linked'],
            ['WOMAD', 'Pitch Sent (Gmail)', ''],
        ])
        rows = parse_spreadsheet_bytes(xlsx)
        assert len(rows) == 1
        assert rows[0]['name'] == 'WOMAD'
        assert rows[0]['status'] == 'Pitch Sent (Gmail)'

    def test_extracts_hubspot_linked_column(self):
        xlsx = _make_xlsx([
            ['Festival Name', 'Status', 'HubSpot Linked'],
            ['WOMAD', 'Pitch Sent', 'Yes (338507124464)'],
        ])
        rows = parse_spreadsheet_bytes(xlsx)
        assert rows[0]['hubspot_linked'] == 'Yes (338507124464)'

    def test_skips_rows_with_no_name(self):
        xlsx = _make_xlsx([
            ['Festival Name', 'Status'],
            ['WOMAD', 'Queued'],
            ['', 'Pitch Sent'],
            [None, 'Pitch Sent'],
        ])
        rows = parse_spreadsheet_bytes(xlsx)
        assert len(rows) == 1

    def test_tolerates_missing_optional_columns(self):
        xlsx = _make_xlsx([
            ['Festival Name'],
            ['WOMAD'],
        ])
        rows = parse_spreadsheet_bytes(xlsx)
        assert len(rows) == 1
        assert rows[0]['name'] == 'WOMAD'
        assert rows[0]['status'] is None
        assert rows[0]['hubspot_linked'] is None

    def test_column_aliases_are_case_insensitive(self):
        xlsx = _make_xlsx([
            ['festival name', 'status'],
            ['ArcTanGent', 'Pitch Scheduled'],
        ])
        rows = parse_spreadsheet_bytes(xlsx)
        assert rows[0]['name'] == 'ArcTanGent'
        assert rows[0]['status'] == 'Pitch Scheduled'

    def test_fallback_to_active_sheet_when_named_sheet_absent(self):
        xlsx = _make_xlsx(
            [['Festival Name', 'Status'], ['Desert Daze', 'Pitch Sent']],
            sheet_name='Some Other Sheet',
        )
        rows = parse_spreadsheet_bytes(xlsx)
        assert rows[0]['name'] == 'Desert Daze'

    def test_row_number_recorded(self):
        xlsx = _make_xlsx([
            ['Festival Name', 'Status'],
            ['WOMAD', 'Pitch Sent'],       # row 2
            ['ArcTanGent', 'Pitch Sent'],  # row 3
        ])
        rows = parse_spreadsheet_bytes(xlsx)
        assert rows[0]['row'] == 2
        assert rows[1]['row'] == 3

    def test_empty_workbook_returns_empty_list(self):
        xlsx = _make_xlsx([])
        assert parse_spreadsheet_bytes(xlsx) == []


# ── DB round-trip tests ───────────────────────────────────────────────────────

class TestSyncRoundtrip:
    """
    Every test follows the same pattern:
      1. Insert source data (HubSpotCompany rows, fixture XLSX bytes, fixture CSV text)
      2. Call sync_pitch_targets(xlsx_bytes=..., csv_content=...)
      3. Query PitchTarget.query.all()
      4. Assert all expected fields survived

    xlsx_bytes and csv_content are always passed explicitly so no Dropbox call
    is made. Tests that don't need one source pass the minimum valid content.
    """

    def test_hubspot_source_all_fields_survive(self, app, db):
        with app.app_context():
            db.session.add(_make_company(
                hubspot_id     = 'hs-001',
                name           = 'Primavera Sound',
                website        = 'https://primaverasound.com',
                description    = 'Major Barcelona festival',
                reach_out_1    = date(2026, 9, 1),
                hs_lead_status = 'NEW',
            ))
            db.session.commit()

            sync_pitch_targets(xlsx_bytes=None, csv_content=_EMPTY_CSV)

            targets = PitchTarget.query.all()
            assert len(targets) == 1
            t = targets[0]
            assert t.hubspot_id      == 'hs-001'
            assert t.name            == 'Primavera Sound'
            assert t.source_hubspot  is True
            assert t.source_spreadsheet is False
            assert t.source_queue_csv   is False
            assert t.website         == 'https://primaverasound.com'
            assert t.description     == 'Major Barcelona festival'
            assert t.reach_out_1     == date(2026, 9, 1)
            assert t.hs_lead_status  == 'NEW'
            assert t.stage           == PMStage.QUEUED.value
            assert t.stage_conflict  is False
            assert t.not_a_fit       is False

    def test_csv_email_survives_roundtrip(self, app, db):
        """email_address must survive — this is the column that was previously wiped."""
        with app.app_context():
            sync_pitch_targets(xlsx_bytes=None, csv_content=_SAMPLE_CSV)

            targets = {t.name: t for t in PitchTarget.query.all()}
            kboo = targets['KBOO 90.7 FM']
            assert kboo.email_address    == 'news@kboo.fm'
            assert kboo.source_queue_csv is True
            assert kboo.stage            == PMStage.QUEUED.value
            assert kboo.not_a_fit        is False

    def test_not_a_fit_row_is_included_in_pitch_targets(self, app, db):
        """not_a_fit rows must be present — the record of 'we said no' is the value."""
        with app.app_context():
            sync_pitch_targets(xlsx_bytes=None, csv_content=_SAMPLE_CSV)

            names = [t.name for t in PitchTarget.query.all()]
            assert 'Williams Center for the Arts' in names

    def test_not_a_fit_fields_survive_roundtrip(self, app, db):
        with app.app_context():
            sync_pitch_targets(xlsx_bytes=None, csv_content=_SAMPLE_CSV)

            targets = {t.name: t for t in PitchTarget.query.all()}
            williams = targets['Williams Center for the Arts']
            assert williams.not_a_fit        is True
            assert williams.not_a_fit_reason == 'too institutional'
            assert williams.source_queue_csv is True
            assert williams.queue_csv_status == 'not_a_fit'

    def test_spreadsheet_source_all_fields_survive(self, app, db):
        with app.app_context():
            xlsx = _make_xlsx([
                ['Festival Name', 'Status', 'HubSpot Linked'],
                ['ArcTanGent', 'Pitch Sent (Gmail)', ''],
            ])
            sync_pitch_targets(xlsx_bytes=xlsx, csv_content=_EMPTY_CSV)

            targets = PitchTarget.query.all()
            ss = [t for t in targets if t.source_spreadsheet]
            assert len(ss) == 1
            t = ss[0]
            assert t.name               == 'ArcTanGent'
            assert t.source_spreadsheet is True
            assert t.source_hubspot     is False
            assert t.spreadsheet_status == 'Pitch Sent (Gmail)'
            assert t.spreadsheet_row    == 2
            assert t.stage              == PMStage.SENT.value
            assert t.stage_conflict     is False

    def test_hubspot_linked_spreadsheet_row_merges_not_duplicates(self, app, db):
        """Spreadsheet row with HubSpot ID merges into the HS record, not a new row."""
        with app.app_context():
            db.session.add(_make_company(
                hubspot_id     = '999001',
                name           = 'WOMAD',
                hs_lead_status = 'NEW',
                reach_out_1    = None,
            ))
            db.session.commit()

            xlsx = _make_xlsx([
                ['Festival Name', 'Status', 'HubSpot Linked'],
                ['WOMAD', 'Pitch Sent (Gmail)', 'Yes (999001)'],
            ])
            sync_pitch_targets(xlsx_bytes=xlsx, csv_content=_EMPTY_CSV)

            targets = PitchTarget.query.all()
            assert len(targets) == 1   # merged, not two rows
            t = targets[0]
            assert t.hubspot_id         == '999001'
            assert t.source_hubspot     is True
            assert t.source_spreadsheet is True
            assert t.spreadsheet_status == 'Pitch Sent (Gmail)'

    def test_stage_conflict_detected_hubspot_wins(self, app, db):
        """
        The known real case: spreadsheet says 'Pitch Sent (Gmail)', HubSpot says NEW
        (QUEUED). Conflict flagged; HubSpot wins on canonical stage.
        """
        with app.app_context():
            db.session.add(_make_company(
                hubspot_id     = 'hs-conflict',
                name           = 'Roskilde Festival',
                hs_lead_status = 'NEW',
                reach_out_1    = None,
            ))
            db.session.commit()

            xlsx = _make_xlsx([
                ['Festival Name', 'Status', 'HubSpot Linked'],
                ['Roskilde Festival', 'Pitch Sent (Gmail)', 'Yes (hs-conflict)'],
            ])
            sync_pitch_targets(xlsx_bytes=xlsx, csv_content=_EMPTY_CSV)

            t = PitchTarget.query.filter_by(hubspot_id='hs-conflict').first()
            assert t.stage          == PMStage.QUEUED.value   # HubSpot wins
            assert t.stage_conflict is True
            assert t.conflict_note  is not None
            assert 'Pitch Sent' in t.conflict_note
            assert 'NEW' in t.conflict_note

    def test_unmappable_spreadsheet_does_not_conflict_with_hubspot(self, app, db):
        """
        Free-text spreadsheet Status values ('Buyer Named - Generic Contact Only'
        etc.) that match no known prefix must NOT generate a stage conflict.
        Only genuinely contradictory signals ('Pitch Sent' vs HubSpot NEW) should.
        This is the fix for the 32-conflict → ~4-real-conflict problem.
        """
        with app.app_context():
            db.session.add(_make_company(
                hubspot_id     = 'hs-noconflict',
                name           = 'Research Phase Festival',
                hs_lead_status = 'NEW',
                reach_out_1    = None,
            ))
            db.session.commit()

            xlsx = _make_xlsx([
                ['Festival Name', 'Status', 'HubSpot Linked'],
                ['Research Phase Festival', 'Buyer Named - Generic Contact Only',
                 'Yes (hs-noconflict)'],
            ])
            sync_pitch_targets(xlsx_bytes=xlsx, csv_content=_EMPTY_CSV)

            t = PitchTarget.query.filter_by(hubspot_id='hs-noconflict').first()
            assert t.stage          == PMStage.QUEUED.value   # HubSpot wins
            assert t.stage_conflict is False                   # unmappable ≠ conflict
            assert t.conflict_note  is None

    def test_unmappable_spreadsheet_csv_stage_wins(self, app, db):
        """
        No HubSpot. Spreadsheet status unmappable. CSV says queued.
        CSV stage should win (QUEUED), not NEEDS_OUTREACH, and no conflict.
        """
        with app.app_context():
            xlsx = _make_xlsx([
                ['Festival Name', 'Status'],
                ['TBD Festival', 'Research Phase'],
            ])
            csv = _EMPTY_CSV + 'TBD Festival,Festival,cowork,2026-12-01,queued,,2026-08-20,,,\n'
            sync_pitch_targets(xlsx_bytes=xlsx, csv_content=csv)

            t = PitchTarget.query.filter_by(name='TBD Festival').first()
            assert t.stage          == PMStage.QUEUED.value
            assert t.stage_conflict is False

    def test_name_based_dedup_merges_csv_into_hs_record(self, app, db):
        """CSV item with no hubspot_id merges with HubSpot record by exact name match."""
        with app.app_context():
            db.session.add(_make_company(
                hubspot_id     = 'hs-dedup',
                name           = 'KBOO 90.7 FM',
                hs_lead_status = 'NEW',
                reach_out_1    = None,
            ))
            db.session.commit()

            csv = (
                _EMPTY_CSV +
                'KBOO 90.7 FM,PNW,cowork,2026-09-15,queued,,2026-08-18,,news@kboo.fm,\n'
            )
            sync_pitch_targets(xlsx_bytes=None, csv_content=csv)

            targets = PitchTarget.query.all()
            assert len(targets) == 1   # merged by name
            t = targets[0]
            assert t.source_hubspot  is True
            assert t.source_queue_csv is True
            assert t.hubspot_id      == 'hs-dedup'
            assert t.email_address   == 'news@kboo.fm'

    def test_spreadsheet_only_row_becomes_new_record(self, app, db):
        """Spreadsheet rows with no HubSpot ID create independent pitch_target rows."""
        with app.app_context():
            xlsx = _make_xlsx([
                ['Festival Name', 'Status'],
                ['Desert Daze',   'Pitch Scheduled'],
                ['Levitation',    'Pitch Sent'],
            ])
            sync_pitch_targets(xlsx_bytes=xlsx, csv_content=_EMPTY_CSV)

            targets = {t.name: t for t in PitchTarget.query.all()}
            assert 'Desert Daze' in targets
            assert 'Levitation'  in targets
            assert targets['Desert Daze'].stage == PMStage.QUEUED.value
            assert targets['Levitation'].stage  == PMStage.SENT.value

    def test_sync_is_fully_idempotent(self, app, db):
        """Running sync twice produces exactly the same row count and values."""
        with app.app_context():
            db.session.add(_make_company(
                hubspot_id     = 'hs-idem',
                name           = 'Glastonbury',
                hs_lead_status = 'ATTEMPTED_TO_CONTACT',
            ))
            db.session.commit()

            sync_pitch_targets(xlsx_bytes=None, csv_content=_EMPTY_CSV)
            count_1 = PitchTarget.query.count()
            stage_1 = PitchTarget.query.filter_by(hubspot_id='hs-idem').one().stage

            sync_pitch_targets(xlsx_bytes=None, csv_content=_EMPTY_CSV)
            count_2 = PitchTarget.query.count()
            stage_2 = PitchTarget.query.filter_by(hubspot_id='hs-idem').one().stage

            assert count_1 == count_2 == 1
            assert stage_1 == stage_2 == PMStage.SENT.value

    def test_duplicate_hs_companies_excluded(self, app, db):
        """HubSpot companies whose names start with [DUPLICATE are never synced."""
        with app.app_context():
            db.session.add(_make_company(hubspot_id='hs-real', name='Real Festival'))
            db.session.add(_make_company(hubspot_id='hs-dup',  name='[DUPLICATE] Real Festival'))
            db.session.commit()

            sync_pitch_targets(xlsx_bytes=None, csv_content=_EMPTY_CSV)

            ids = [t.hubspot_id for t in PitchTarget.query.all()]
            assert 'hs-real' in ids
            assert 'hs-dup' not in ids

    def test_all_columns_survive_full_three_source_roundtrip(self, app, db):
        """
        A record touched by all three sources: verify every column has the
        expected value after the write-read cycle. This is the primary guard
        against silent field-dropping in the sync write path.
        """
        with app.app_context():
            db.session.add(_make_company(
                hubspot_id     = 'hs-full',
                name           = 'Full Test Festival',
                website        = 'https://full-test.example.com',
                description    = 'A comprehensive test fixture',
                reach_out_1    = date(2026, 11, 1),
                hs_lead_status = 'CONNECTED',
            ))
            db.session.commit()

            xlsx = _make_xlsx([
                ['Festival Name', 'Status', 'HubSpot Linked'],
                ['Full Test Festival', 'Pitch Sent (Gmail)', 'Yes (hs-full)'],
            ])
            csv = (
                _EMPTY_CSV +
                'Full Test Festival,Festival,cowork,2027-01-01,queued,,2026-08-20,'
                'hs-full,buyer@full-test.example.com,\n'
            )
            sync_pitch_targets(xlsx_bytes=xlsx, csv_content=csv)

            t = PitchTarget.query.filter_by(hubspot_id='hs-full').one()

            # Source flags
            assert t.source_hubspot     is True
            assert t.source_spreadsheet is True
            assert t.source_queue_csv   is True

            # HubSpot fields
            assert t.hubspot_id     == 'hs-full'
            assert t.name           == 'Full Test Festival'
            assert t.website        == 'https://full-test.example.com'
            assert t.description    == 'A comprehensive test fixture'
            assert t.reach_out_1    == date(2026, 11, 1)
            assert t.hs_lead_status == 'CONNECTED'

            # Stage: HubSpot wins (CONNECTED → IN_NEGOTIATION)
            assert t.stage == PMStage.IN_NEGOTIATION.value

            # Conflict: spreadsheet says SENT, HubSpot says IN_NEGOTIATION
            assert t.stage_conflict is True
            assert t.conflict_note  is not None
            assert 'Pitch Sent' in t.conflict_note

            # Spreadsheet fields
            assert t.spreadsheet_status == 'Pitch Sent (Gmail)'
            assert t.spreadsheet_row    == 2

            # CSV fields
            assert t.email_address   == 'buyer@full-test.example.com'
            assert t.queue_csv_status == 'queued'
            assert t.not_a_fit        is False
            assert t.not_a_fit_reason is None

            # last_synced_at was set
            assert t.last_synced_at is not None

    def test_spreadsheet_unavailable_does_not_abort_sync(self, app, db):
        """Passing xlsx_bytes=None with no SPREADSHEET_DROPBOX_PATH config skips cleanly."""
        with app.app_context():
            db.session.add(_make_company(hubspot_id='hs-no-ss', name='No Spreadsheet'))
            db.session.commit()

            # No SPREADSHEET_DROPBOX_PATH in test config → spreadsheet skipped with warning
            result = sync_pitch_targets(xlsx_bytes=None, csv_content=_EMPTY_CSV)

            assert result.spreadsheet_skipped is True
            assert result.hubspot_count == 1
            assert PitchTarget.query.count() == 1

    def test_sync_result_counts_are_accurate(self, app, db):
        with app.app_context():
            db.session.add(_make_company(hubspot_id='hs-count', name='Count Festival'))
            db.session.commit()

            xlsx = _make_xlsx([
                ['Festival Name', 'Status'],
                ['Spreadsheet Only Festival', 'Pitch Scheduled'],
            ])
            csv = _EMPTY_CSV + 'CSV Only Festival,Festival,cowork,2026-12-01,queued,,2026-08-20,,,\n'

            result = sync_pitch_targets(xlsx_bytes=xlsx, csv_content=csv)

            assert result.hubspot_count     == 1
            assert result.spreadsheet_count == 1
            assert result.csv_count         == 1
            assert result.total             == 3   # three distinct records

    def test_not_a_fit_count_in_sync_result(self, app, db):
        with app.app_context():
            result = sync_pitch_targets(xlsx_bytes=None, csv_content=_SAMPLE_CSV)
            assert result.not_a_fit_count == 1
