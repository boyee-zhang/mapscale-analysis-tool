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

    async def spy(lng, lat, date_from, date_to, minutes, profile, doc_type, activiteit):
        seen.update(doc_type=doc_type, activiteit=activiteit)
        return {"shape": "none", "total": 0, "items": []}

    monkeypatch.setattr("api.main.routers.bekendmakingen.service.get_bekendmakingen", spy)

    client.get(f"{BASE}&building_permits=true&doc_type=verkeersbesluit%20of%20-mededeling")
    assert seen == {"doc_type": ["verkeersbesluit of -mededeling"], "activiteit": None}

    seen.clear()
    client.get(f"{BASE}&building_permits=true")
    assert seen == {"doc_type": ["omgevingsvergunning"], "activiteit": ["bouwen"]}
