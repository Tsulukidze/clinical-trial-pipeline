"""Small cleaning functions.

Each function here does one job: take a raw value, return a clean one.
They are pure functions (no database, no files), which makes them very
easy to unit test.

My general rule: if a value is empty, I return None quietly.
If a value is present but BROKEN, I raise ValueError, and the caller
decides what to do and logs a data quality issue.
"""

from __future__ import annotations

import re
from datetime import date, datetime

NCT_ID_PATTERN = re.compile(r"^NCT\d{8}$")

# Date formats I accept, from most to least specific.
# Real exports mix all of these.
_DATE_FORMATS = [
    "%Y-%m-%d",       # 2020-01-15
    "%B %d, %Y",      # January 15, 2020
    "%Y-%m",          # 2020-01        (day unknown)
    "%B %Y",          # January 2020   (day unknown)
    "%Y",             # 2020           (month and day unknown)
]


def clean_text(raw: object) -> str | None:
    """Trim whitespace. Empty or missing text becomes None."""
    if raw is None:
        return None
    text = str(raw).strip()
    return text or None


def validate_nct_id(raw: object) -> str:
    """Return a valid NCT ID or raise ValueError.

    A valid ID is the letters NCT plus exactly 8 digits.
    I uppercase first, because some files contain 'nct01234567'.
    """
    text = clean_text(raw)
    if text is None:
        raise ValueError("missing NCT ID")
    text = text.upper()
    if not NCT_ID_PATTERN.match(text):
        raise ValueError(f"invalid NCT ID: {text}")
    return text


def parse_date(raw: object) -> date | None:
    """Parse the date formats found in clinical trial data.

    Partial dates like '2020-01' become the first day of that period.
    That is a compromise: I keep the record usable for timeline
    analytics, and the caller logs a 'partial_date' issue so the
    approximation is never hidden.

    Raises ValueError if the text does not match any known format.
    """
    text = clean_text(raw)
    if text is None:
        return None
    for fmt in _DATE_FORMATS:
        try:
            return datetime.strptime(text, fmt).date()
        except ValueError:
            continue
    raise ValueError(f"unparseable date: {text}")


def is_partial_date(raw: object) -> bool:
    """True if the raw text is missing the day or the month."""
    text = clean_text(raw)
    if text is None:
        return False
    # A full date has either two dashes (2020-01-15)
    # or a comma (January 15, 2020). Everything else is partial.
    return text.count("-") != 2 and "," not in text


def parse_enrollment(raw: object) -> int | None:
    """Parse the number of participants.

    Raises ValueError for text that is not a number, and for
    negative numbers (a study cannot have -5 participants).
    """
    text = clean_text(raw)
    if text is None:
        return None
    try:
        value = int(float(text))  # float first: some files contain "150.0"
    except ValueError:
        raise ValueError(f"enrollment is not a number: {text}")
    if value < 0:
        raise ValueError(f"enrollment is negative: {value}")
    return value


def standardize_phase(raw: object) -> str | None:
    """Bring all phase spellings to one standard form.

    Examples of what arrives:  'Phase 1', 'PHASE1', 'Phase 1|Phase 2',
    ['PHASE1', 'PHASE2'], 'Not Applicable', 'N/A'
    What I produce:            'PHASE1', 'PHASE1/PHASE2', 'NA'
    """
    # The API sends a list, CSV sends text with '|' between values.
    if isinstance(raw, list):
        parts = [str(p) for p in raw]
    else:
        text = clean_text(raw)
        if text is None:
            return None
        parts = text.split("|")

    cleaned: list[str] = []
    for part in parts:
        p = part.strip().upper().replace(" ", "").replace("_", "")
        if p in ("", "N/A", "NA", "NOTAPPLICABLE", "NONE"):
            p = "NA"
        elif p == "EARLYPHASE1":
            p = "EARLY_PHASE1"
        # 'PHASE1'..'PHASE4' are already in final form after the cleanup
        if p not in cleaned:
            cleaned.append(p)

    if not cleaned:
        return None
    # 'NA' next to a real phase adds nothing, so I drop it then.
    if len(cleaned) > 1 and "NA" in cleaned:
        cleaned.remove("NA")
    return "/".join(sorted(cleaned))


def standardize_enum(raw: object) -> str | None:
    """Normalize status-like values: 'Not yet recruiting' -> 'NOT_YET_RECRUITING'.

    This gives GROUP BY queries one spelling per value, no matter
    which source the record came from.
    """
    text = clean_text(raw)
    if text is None:
        return None
    return re.sub(r"[\s\-,/]+", "_", text.upper()).strip("_")


def parse_age_years(raw: object) -> float | None:
    """Turn '18 Years', '6 Months' or '30 Days' into a number of years.

    Raises ValueError for text it cannot understand.
    """
    text = clean_text(raw)
    if text is None or text.upper() in ("N/A", "NA", "NONE"):
        return None
    match = re.match(r"^(\d+(?:\.\d+)?)\s*(YEAR|MONTH|WEEK|DAY|HOUR|MINUTE)S?", text.upper())
    if not match:
        raise ValueError(f"unparseable age: {text}")
    value = float(match.group(1))
    unit = match.group(2)
    per_year = {"YEAR": 1, "MONTH": 12, "WEEK": 52, "DAY": 365, "HOUR": 8760, "MINUTE": 525600}
    return round(value / per_year[unit], 2)


def parse_sex(raw: object) -> str | None:
    """Normalize sex to 'ALL', 'MALE' or 'FEMALE'. Unknown values raise."""
    text = clean_text(raw)
    if text is None:
        return None
    value = text.upper()
    if value in ("ALL", "MALE", "FEMALE"):
        return value
    if value in ("M",):
        return "MALE"
    if value in ("F",):
        return "FEMALE"
    raise ValueError(f"unknown sex value: {text}")


def parse_bool(raw: object) -> bool | None:
    """Turn 'true'/'yes'/'Accepts Healthy Volunteers' style text into bool."""
    if isinstance(raw, bool):
        return raw
    text = clean_text(raw)
    if text is None:
        return None
    value = text.upper()
    if value in ("TRUE", "YES", "T", "Y", "1", "ACCEPTS HEALTHY VOLUNTEERS"):
        return True
    if value in ("FALSE", "NO", "F", "N", "0"):
        return False
    return None  # unknown wording: I prefer None over guessing
