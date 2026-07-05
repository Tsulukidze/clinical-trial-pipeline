"""Tests for the staging helpers and the format router.

These need the sqlalchemy package installed (the modules import it),
but no running database: I only test the pure helper functions.
"""

from pipeline.load.staging import _batches, _extract_nct_id
from pipeline.transform.transformer import detect_format, transform_payload
from tests.samples import api_payload, csv_row


# --- _extract_nct_id -----------------------------------------------------

def test_extract_from_flat_csv_column():
    assert _extract_nct_id({"nct_number": " NCT01234567 "}) == "NCT01234567"


def test_extract_from_flat_sql_column():
    assert _extract_nct_id({"nct_id": "NCT00000001"}) == "NCT00000001"


def test_extract_from_nested_api_json():
    assert _extract_nct_id(api_payload()) == "NCT04280705"


def test_extract_missing_returns_none():
    assert _extract_nct_id({"title": "no id in sight"}) is None


# --- _batches --------------------------------------------------------------

def test_batches_split_evenly_with_remainder():
    sizes = [len(b) for b in _batches(iter(range(25)), 10)]
    assert sizes == [10, 10, 5]


def test_batches_empty_input_yields_nothing():
    assert list(_batches(iter([]), 10)) == []


# --- format detection + routing ------------------------------------------

def test_api_payload_detected_as_api():
    assert detect_format(api_payload()) == "api"


def test_flat_row_detected_as_csv():
    assert detect_format(csv_row()) == "csv"


def test_transform_payload_routes_both_formats():
    api_record, _ = transform_payload(api_payload())
    csv_record, _ = transform_payload(csv_row())
    assert api_record.nct_id == "NCT04280705"
    assert csv_record.nct_id == "NCT04321174"
