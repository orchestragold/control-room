"""
Round-trip tests for pitch queue CSV serialization.

These tests cover the seam between parse_queue/serialize_queue and the Dropbox
write-back path. The failure mode they guard against: a silent schema-narrowing
write where the running code's COLUMNS list doesn't match the live CSV schema,
causing columns (and all data in them) to be silently dropped on the next write.

That failure erased all 30 email_address values in production. The tests here
would have caught it.
"""
import csv
import io

import pytest

from app.integrations.pitch_queue import (
    COLUMNS,
    detect_fieldnames,
    parse_queue,
    serialize_queue,
    QueueItem,
)

# A minimal CSV that matches the current 9-column schema.
_SAMPLE = """\
name,pitch_type,source,deadline,status,notes,date_added,hubspot_id,email_address
XRAY.fm,PNW Tour - Media,cowork,2026-09-15,queued,Theo Craig music director,2026-08-18,,theo@xray.fm
KBOO 90.7 FM,PNW Tour - Media,cowork,2026-09-15,queued,Brandon Lieberman,2026-08-18,,news@kboo.fm
"""

# An old-schema CSV (8 columns, no email_address) — simulates a server running
# pre-migration code that wrote back the file before the restart.
_OLD_SCHEMA = """\
name,pitch_type,source,deadline,status,notes,date_added,hubspot_id
XRAY.fm,PNW Tour - Media,cowork,2026-09-15,queued,Theo Craig,2026-08-18,
"""


# ── detect_fieldnames ────────────────────────────────────────────────────────

class TestDetectFieldnames:

    def test_preserves_email_address_column(self):
        names = detect_fieldnames(_SAMPLE)
        assert 'email_address' in names

    def test_preserves_column_order(self):
        names = detect_fieldnames(_SAMPLE)
        # email_address is last in the sample; verify it lands after hubspot_id
        assert names.index('email_address') > names.index('hubspot_id')

    def test_adds_missing_schema_columns_to_old_csv(self):
        """Old 8-column CSV gets email_address appended — forward migration."""
        names = detect_fieldnames(_OLD_SCHEMA)
        assert 'email_address' in names

    def test_all_schema_columns_present(self):
        """detect_fieldnames output is a superset of COLUMNS."""
        names = detect_fieldnames(_SAMPLE)
        for col in COLUMNS:
            assert col in names, f'COLUMNS entry {col!r} missing from detect_fieldnames output'


# ── serialize_queue round-trip ───────────────────────────────────────────────

class TestSerializeQueueRoundtrip:

    def _read_header(self, csv_text: str) -> list[str]:
        return list(csv.DictReader(io.StringIO(csv_text)).fieldnames or [])

    def _read_rows(self, csv_text: str) -> list[dict]:
        return list(csv.DictReader(io.StringIO(csv_text)))

    def test_header_preserved_after_roundtrip(self):
        fieldnames = detect_fieldnames(_SAMPLE)
        items = parse_queue(_SAMPLE)
        out = serialize_queue(items, fieldnames=fieldnames)
        assert 'email_address' in self._read_header(out)

    def test_email_values_preserved_after_roundtrip(self):
        fieldnames = detect_fieldnames(_SAMPLE)
        items = parse_queue(_SAMPLE)
        out = serialize_queue(items, fieldnames=fieldnames)
        rows = {r['name']: r for r in self._read_rows(out)}
        assert rows['XRAY.fm']['email_address'] == 'theo@xray.fm'
        assert rows['KBOO 90.7 FM']['email_address'] == 'news@kboo.fm'

    def test_status_change_does_not_drop_email(self):
        """
        Simulates the exact operation that caused data loss: mark a row 'pitched'
        and write it back. email_address must survive.
        """
        fieldnames = detect_fieldnames(_SAMPLE)
        items = parse_queue(_SAMPLE)
        for item in items:
            if item.name == 'XRAY.fm':
                item.status = 'pitched'
        out = serialize_queue(items, fieldnames=fieldnames)
        rows = {r['name']: r for r in self._read_rows(out)}
        assert rows['XRAY.fm']['status'] == 'pitched'
        assert rows['XRAY.fm']['email_address'] == 'theo@xray.fm'
        assert rows['KBOO 90.7 FM']['email_address'] == 'news@kboo.fm'

    def test_old_schema_csv_gains_email_column_on_roundtrip(self):
        """Old 8-column CSV: after detect + serialize, output includes email_address."""
        fieldnames = detect_fieldnames(_OLD_SCHEMA)
        items = parse_queue(_OLD_SCHEMA)
        out = serialize_queue(items, fieldnames=fieldnames)
        assert 'email_address' in self._read_header(out)

    def test_no_fieldnames_arg_uses_columns(self):
        """Omitting fieldnames falls back to COLUMNS — existing behavior unchanged."""
        items = parse_queue(_SAMPLE)
        out = serialize_queue(items)
        assert self._read_header(out) == COLUMNS

    def test_row_count_preserved(self):
        """No rows are silently dropped by the round-trip."""
        fieldnames = detect_fieldnames(_SAMPLE)
        items = parse_queue(_SAMPLE)
        out = serialize_queue(items, fieldnames=fieldnames)
        assert len(self._read_rows(out)) == 2


# ── not_a_fit round-trip ─────────────────────────────────────────────────────

class TestNotAFitRoundtrip:
    """
    Guards the new write path: mark a row not_a_fit, write back, verify the
    full header and every cell survive unchanged.  Same failure mode as the
    email_address data loss — a new column that didn't exist in the live CSV
    must be forward-migrated by detect_fieldnames, not silently dropped.
    """

    def _read_header(self, csv_text: str) -> list[str]:
        return list(csv.DictReader(io.StringIO(csv_text)).fieldnames or [])

    def _read_rows(self, csv_text: str) -> list[dict]:
        return list(csv.DictReader(io.StringIO(csv_text)))

    def test_not_a_fit_status_and_reason_written(self):
        """Mark a row not_a_fit with a reason; verify status, reason, and all other cells."""
        fieldnames = detect_fieldnames(_SAMPLE)
        items = parse_queue(_SAMPLE)
        for item in items:
            if item.name == 'XRAY.fm':
                item.status = 'not_a_fit'
                item.not_a_fit_reason = 'classical presenter, not experimental'
        out = serialize_queue(items, fieldnames=fieldnames)
        rows = {r['name']: r for r in self._read_rows(out)}

        assert rows['XRAY.fm']['status'] == 'not_a_fit'
        assert rows['XRAY.fm']['not_a_fit_reason'] == 'classical presenter, not experimental'
        # email survives
        assert rows['XRAY.fm']['email_address'] == 'theo@xray.fm'
        # untouched row is intact
        assert rows['KBOO 90.7 FM']['status'] == 'queued'
        assert rows['KBOO 90.7 FM']['email_address'] == 'news@kboo.fm'

    def test_not_a_fit_header_includes_new_column(self):
        """not_a_fit_reason column appears in the header after forward migration."""
        fieldnames = detect_fieldnames(_SAMPLE)
        items = parse_queue(_SAMPLE)
        out = serialize_queue(items, fieldnames=fieldnames)
        assert 'not_a_fit_reason' in self._read_header(out)

    def test_not_a_fit_on_old_schema_csv(self):
        """Old 8-column CSV: not_a_fit_reason is forward-migrated and written correctly."""
        fieldnames = detect_fieldnames(_OLD_SCHEMA)
        items = parse_queue(_OLD_SCHEMA)
        for item in items:
            item.status = 'not_a_fit'
            item.not_a_fit_reason = 'govt office, not a buyer'
        out = serialize_queue(items, fieldnames=fieldnames)
        header = self._read_header(out)
        rows = self._read_rows(out)
        assert 'not_a_fit_reason' in header
        assert 'email_address' in header
        assert rows[0]['not_a_fit_reason'] == 'govt office, not a buyer'

    def test_empty_reason_is_allowed(self):
        """Reason is optional — blank string must round-trip cleanly."""
        fieldnames = detect_fieldnames(_SAMPLE)
        items = parse_queue(_SAMPLE)
        for item in items:
            if item.name == 'XRAY.fm':
                item.status = 'not_a_fit'
                item.not_a_fit_reason = ''
        out = serialize_queue(items, fieldnames=fieldnames)
        rows = {r['name']: r for r in self._read_rows(out)}
        assert rows['XRAY.fm']['status'] == 'not_a_fit'
        assert rows['XRAY.fm']['not_a_fit_reason'] == ''
        assert rows['XRAY.fm']['email_address'] == 'theo@xray.fm'

    def test_all_columns_survive_not_a_fit_write(self):
        """detect_fieldnames output is a superset of COLUMNS after not_a_fit path."""
        fieldnames = detect_fieldnames(_SAMPLE)
        for col in COLUMNS:
            assert col in fieldnames, f'COLUMNS entry {col!r} missing after not_a_fit detect'
