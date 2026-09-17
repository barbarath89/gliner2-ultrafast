"""The independent NYC verifier must reject mismatches without running an agent."""

import base64
from copy import deepcopy

import pytest

from scripts.record_flights_demo import verify


def result_page():
    encoded = base64.urlsafe_b64encode(b"2026-10-09").decode().rstrip("=")
    return {
        "url": "https://www.google.com/travel/flights/search?tfs=" + encoded,
        "actions": [
            {"label": label, "value": value}
            for label, value in [
                ("Change ticket type. One way", "One way"),
                ("Where from?", "New York"),
                ("Where to?", "San Francisco"),
                ("Departure", "Fri, Oct 9"),
                ("Flight departing Friday, October 9. Select flight", ""),
            ]
        ],
    }


@pytest.mark.parametrize("mismatch", ["host", "year", "origin", "destination", "date", "flights", "one_way"])
def test_nyc_verifier_rejects_mismatch(mismatch):
    page = result_page()
    assert verify(page)["passed"]
    page = deepcopy(page)
    if mismatch == "host":
        page["url"] = page["url"].replace("www.google.com", "example.test")
    elif mismatch == "year":
        page["url"] = page["url"].split("?")[0]
    elif mismatch == "flights":
        page["actions"].pop()
    else:
        label = {
            "origin": "Where from?",
            "destination": "Where to?",
            "date": "Departure",
            "one_way": "Change ticket type. One way",
        }[mismatch]
        next(a for a in page["actions"] if a["label"] == label)["value"] = "wrong"
    assert not verify(page)["passed"]
