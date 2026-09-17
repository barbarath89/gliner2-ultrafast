"""Date parsing and ISO normalization for explicit dates in task goals.

Supports English month names, ISO dates and US-style numeric dates. Calendar
controls are matched using their observed labels; no task dates are embedded.
"""

import re
from datetime import date

MONTHS = {
    month: number
    for number, month in enumerate(
        ["jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct", "nov", "dec"], 1
    )
}
_MONTH = "jan|feb|mar|apr|may|jun|jul|aug|sep|sept|oct|nov|dec"
DATE = re.compile(
    rf"\b(?:(?P<iso>\d{{4}}-\d{{1,2}}-\d{{1,2}})"
    rf"|(?P<month>{_MONTH})[a-z]*\.?\s+(?P<day>\d{{1,2}})(?:st|nd|rd|th)?(?:,?\s+(?P<year>\d{{4}}))?"
    rf"|(?P<day2>\d{{1,2}})(?:st|nd|rd|th)?\s+(?P<month2>{_MONTH})[a-z]*\.?(?:,?\s+(?P<year2>\d{{4}}))?"
    r"|(?P<m>\d{1,2})/(?P<d>\d{1,2})/(?P<y>\d{4}))\b",
    re.IGNORECASE,
)


def first_date(text, today=None):
    """The first date in the text, or None. A missing year is this year or next."""
    today = today or date.today()
    found = DATE.search(str(text or ""))
    if not found:
        return None
    try:
        if found.group("iso"):
            year, month, day = (int(part) for part in found.group("iso").split("-"))
            return date(year, month, day)
        if found.group("m"):
            return date(int(found.group("y")), int(found.group("m")), int(found.group("d")))
        month = MONTHS[(found.group("month") or found.group("month2"))[:3].lower()]
        day = int(found.group("day") or found.group("day2"))
        year = found.group("year") or found.group("year2")
        parsed = date(int(year) if year else today.year, month, day)
        # An undated "20 September" that has already passed means the next one.
        if not year and (today - parsed).days > 0:
            parsed = parsed.replace(year=today.year + 1)
        return parsed
    except (ValueError, KeyError):
        return None


def normalise(typed, wanted):
    """The typed value in ISO form, when it is the date the task asked for.

    A writer that returns a different date than the task stated is not corrected
    here; that would be the executor inventing a value. It is only reformatted.
    """
    if not wanted:
        return typed
    parsed = first_date(typed)
    return wanted.isoformat() if parsed == wanted else typed


def same_date(label, wanted):
    """Does this control's name mean exactly the date the task asked for?"""
    if not wanted:
        return False
    found = first_date(label)
    return found is not None and found == wanted
