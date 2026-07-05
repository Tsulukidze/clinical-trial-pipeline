"""Tests for the pure cleaning functions.

These are the functions every record passes through, so they get
the most detailed tests: normal values, edge cases, and broken
values that must raise ValueError.
"""

from datetime import date

import pytest

from pipeline.transform import cleaners


# --- clean_text -------------------------------------------------------------

def test_clean_text_trims_whitespace():
    assert cleaners.clean_text("  hello  ") == "hello"


def test_clean_text_empty_and_none_become_none():
    assert cleaners.clean_text("") is None
    assert cleaners.clean_text("   ") is None
    assert cleaners.clean_text(None) is None


# --- validate_nct_id ---------------------------------------------------------

def test_valid_nct_id_passes():
    assert cleaners.validate_nct_id("NCT01234567") == "NCT01234567"


def test_nct_id_is_uppercased_and_trimmed():
    assert cleaners.validate_nct_id("  nct01234567 ") == "NCT01234567"


def test_nct_id_wrong_shape_raises():
    with pytest.raises(ValueError):
        cleaners.validate_nct_id("NCT123")          # too short
    with pytest.raises(ValueError):
        cleaners.validate_nct_id("ABC01234567")     # wrong prefix
    with pytest.raises(ValueError):
        cleaners.validate_nct_id("NCT1234567X")     # letter in digits


def test_missing_nct_id_raises():
    with pytest.raises(ValueError):
        cleaners.validate_nct_id(None)
    with pytest.raises(ValueError):
        cleaners.validate_nct_id("  ")


# --- parse_date --------------------------------------------------------------

def test_parse_date_accepts_all_known_formats():
    assert cleaners.parse_date("2020-01-15") == date(2020, 1, 15)
    assert cleaners.parse_date("January 15, 2020") == date(2020, 1, 15)
    assert cleaners.parse_date("2020-03") == date(2020, 3, 1)
    assert cleaners.parse_date("March 2020") == date(2020, 3, 1)
    assert cleaners.parse_date("2020") == date(2020, 1, 1)


def test_parse_date_empty_is_none():
    assert cleaners.parse_date(None) is None
    assert cleaners.parse_date("") is None


def test_parse_date_garbage_raises():
    with pytest.raises(ValueError):
        cleaners.parse_date("not a date")
    with pytest.raises(ValueError):
        cleaners.parse_date("15/01/2020")  # format I do not accept blindly


def test_is_partial_date():
    assert cleaners.is_partial_date("2020-03") is True
    assert cleaners.is_partial_date("March 2020") is True
    assert cleaners.is_partial_date("2020-03-15") is False
    assert cleaners.is_partial_date("January 15, 2020") is False


# --- parse_enrollment ---------------------------------------------------------

def test_enrollment_normal_and_float_text():
    assert cleaners.parse_enrollment("150") == 150
    assert cleaners.parse_enrollment("150.0") == 150
    assert cleaners.parse_enrollment(150) == 150


def test_enrollment_empty_is_none():
    assert cleaners.parse_enrollment(None) is None


def test_enrollment_negative_raises():
    with pytest.raises(ValueError):
        cleaners.parse_enrollment("-5")


def test_enrollment_not_a_number_raises():
    with pytest.raises(ValueError):
        cleaners.parse_enrollment("many")


# --- standardize_phase --------------------------------------------------------

def test_phase_from_api_list():
    assert cleaners.standardize_phase(["PHASE1", "PHASE2"]) == "PHASE1/PHASE2"


def test_phase_from_csv_pipe_text():
    assert cleaners.standardize_phase("Phase 1|Phase 2") == "PHASE1/PHASE2"


def test_phase_not_applicable_becomes_na():
    assert cleaners.standardize_phase("Not Applicable") == "NA"
    assert cleaners.standardize_phase("N/A") == "NA"


def test_phase_early_phase_1():
    assert cleaners.standardize_phase("Early Phase 1") == "EARLY_PHASE1"


def test_phase_duplicates_collapse():
    assert cleaners.standardize_phase("Phase 2|PHASE2") == "PHASE2"


def test_phase_empty_is_none():
    assert cleaners.standardize_phase(None) is None
    assert cleaners.standardize_phase([]) is None


# --- standardize_enum ------------------------------------------------------------

def test_enum_spaces_become_underscores():
    assert cleaners.standardize_enum("Not yet recruiting") == "NOT_YET_RECRUITING"


def test_enum_empty_is_none():
    assert cleaners.standardize_enum(None) is None


# --- parse_age_years ------------------------------------------------------------

def test_age_years_and_months():
    assert cleaners.parse_age_years("18 Years") == 18.0
    assert cleaners.parse_age_years("6 Months") == 0.5


def test_age_na_is_none():
    assert cleaners.parse_age_years("N/A") is None
    assert cleaners.parse_age_years(None) is None


def test_age_garbage_raises():
    with pytest.raises(ValueError):
        cleaners.parse_age_years("old enough")


# --- parse_sex -----------------------------------------------------------------

def test_sex_normal_values():
    assert cleaners.parse_sex("All") == "ALL"
    assert cleaners.parse_sex("female") == "FEMALE"
    assert cleaners.parse_sex("M") == "MALE"


def test_sex_unknown_raises():
    with pytest.raises(ValueError):
        cleaners.parse_sex("unknown")


# --- parse_bool ------------------------------------------------------------------

def test_bool_values():
    assert cleaners.parse_bool(True) is True
    assert cleaners.parse_bool("Yes") is True
    assert cleaners.parse_bool("false") is False
    assert cleaners.parse_bool(None) is None
    assert cleaners.parse_bool("maybe") is None  # unknown wording: no guessing
