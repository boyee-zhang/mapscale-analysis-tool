"""
The filter semantics of /bekendmakingen, and the window guards around it.

These run without Postgres, ORS or Redis on purpose. Every case below is answered before
any I/O, so needing a fixture here later would mean a short-circuit has moved below the
line it is supposed to guard — which is exactly how the POI category bug happened.
"""
import asyncio
from datetime import date

import pytest
from fastapi.testclient import TestClient

from api.main import db
from api.main.app import app
from api.main.domains.bekendmaking import service

WINDOW = (date(2026, 1, 1), date(2026, 12, 31))


def call(**kw):
    return asyncio.run(service.get_bekendmakingen(4.9, 52.37, *WINDOW, **kw))


@pytest.mark.parametrize("field", ["doc_type", "activiteit"])
def test_explicitly_empty_filter_matches_nothing(field):
    """`[]` means "nothing", and is not the "no restriction" that `None` means."""
    out = call(**{field: []})
    assert out["total"] == 0
    assert out["items"] == []
    assert out["shape"] == "none"


@pytest.mark.parametrize("field", ["doc_type", "activiteit"])
def test_empty_filter_is_answered_without_a_database(field):
    """It must short-circuit above db.enabled(), not fall through to the unavailable path."""
    assert not db.enabled()
    assert call(**{field: []})["shape"] == "none"


def test_no_database_is_not_reported_as_zero_results():
    """An empty list and "we have no source" look identical to a caller. Only one is true."""
    assert not db.enabled()
    out = call()
    assert out["total"] == 0
    assert out["shape"] == "unavailable"
    assert out["reason"] == "postgres not configured"


def test_building_permit_preset_names_both_fields():
    """doc_type alone also covers felling, signage and zoning — the activity is what narrows it."""
    assert service.BUILDING_PERMIT == {
        "doc_type": ["omgevingsvergunning"],
        "activiteit": ["bouwen"],
    }


# --- router-level guards -----------------------------------------------------------

client = TestClient(app)
BASE = "/api/main/bekendmakingen?lng=4.9&lat=52.37"


def test_reversed_window_is_rejected():
    r = client.get(f"{BASE}&date_from=2026-05-01&date_to=2026-01-01")
    assert r.status_code == 422


def test_window_wider_than_the_cap_is_rejected():
    r = client.get(f"{BASE}&date_from=2020-01-01&date_to=2026-01-01")
    assert r.status_code == 422


def test_minutes_is_bounded():
    assert client.get(f"{BASE}&minutes=99").status_code == 422
    assert client.get(f"{BASE}&minutes=0").status_code == 422


def test_shorthand_does_not_override_an_explicit_filter(monkeypatch):
    """building_permits=true is a convenience, not a precedence rule.

    Asserted on what the router hands the service, because without a database the
    response body cannot show which filters were used.
    """
    seen = {}

    # **kwargs as well as the positional list: the router passes positionally, and this spy
    # silently became wrong the moment the service grew `noise` and `sort` -- it raised
    # TypeError, the router wrapped it in a 500, and the assertion then failed on an empty
    # dict rather than on the thing under test. Accepting the extras keeps a future parameter
    # from being reported as a filter-precedence bug.
    async def spy(lng, lat, date_from, date_to, minutes, profile, doc_type, activiteit,
                  noise=None, sort="recent", **kwargs):
        seen.update(doc_type=doc_type, activiteit=activiteit, noise=noise, sort=sort)
        return {"shape": "none", "total": 0, "items": []}

    monkeypatch.setattr("api.main.routers.bekendmakingen.service.get_bekendmakingen", spy)

    client.get(f"{BASE}&building_permits=true&doc_type=verkeersbesluit%20of%20-mededeling")
    assert seen == {"doc_type": ["verkeersbesluit of -mededeling"], "activiteit": None,
                    "noise": None, "sort": "recent"}

    seen.clear()
    client.get(f"{BASE}&building_permits=true")
    assert seen == {"doc_type": ["omgevingsvergunning"], "activiteit": ["bouwen"],
                    "noise": None, "sort": "recent"}

    # The noise filter and the sort reach the service as given, and neither is touched by the
    # building_permits shorthand.
    seen.clear()
    client.get(f"{BASE}&noise=noisy&sort=noise")
    assert seen == {"doc_type": None, "activiteit": None,
                    "noise": ["noisy"], "sort": "noise"}


def test_noise_and_sort_are_validated():
    """`sort` is interpolated into the ORDER BY, so the router must reject anything else.

    It is a `pattern=` on the Query rather than a check in the service because the service
    also has a dict lookup with a default -- two layers, and this one is what makes a bad
    value a 422 instead of a silent fallback to `recent`.
    """
    assert client.get(f"{BASE}&sort=published_at%3B%20DROP%20TABLE").status_code == 422
    assert client.get(f"{BASE}&sort=noise").status_code != 422
    assert client.get(f"{BASE}&sort=recent").status_code != 422
