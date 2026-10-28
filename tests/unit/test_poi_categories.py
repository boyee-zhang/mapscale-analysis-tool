"""An empty category selection must not become the default set.

These run without Postgres, Redis or ORS on purpose: the empty-selection case short-circuits
before any of them is touched, so if one of these tests ever needs a fixture, the
short-circuit has moved below the I/O and the bug is back.
"""
import asyncio

from api.main.domains.geo import service


def call(categories):
    return asyncio.run(service.get_pois(4.9622, 52.3421, 10, "walking", categories))


class TestEmptySelection:
    def test_empty_list_returns_nothing(self):
        # The regression: `if categories` is also false for [], so deselecting every type in
        # the picker used to come back with supermarket/convenience/gym.
        assert call([]) == {"elements": []}

    def test_empty_list_makes_no_upstream_call(self, monkeypatch):
        def explode(*a, **k):
            raise AssertionError("an empty selection must not reach an upstream or the db")

        monkeypatch.setattr(service, "get_isochrone", explode)
        monkeypatch.setattr(service.db, "enabled", explode)
        assert call([]) == {"elements": []}


class TestOmittedSelection:
    def test_none_is_not_the_same_as_empty(self, monkeypatch):
        # None means "the caller said nothing", which keeps the documented default. Proven by
        # the categories the service passes downstream, so it does not need a live database.
        seen = {}

        def fake_enabled():
            return False

        async def fake_fetch(lng, lat, minutes, profile):
            seen["called"] = True
            return {"elements": []}

        async def fake_kv_get(key):
            return None

        async def fake_kv_set(key, value, ttl_seconds=None):
            return None

        monkeypatch.setattr(service.db, "enabled", fake_enabled)
        monkeypatch.setattr(service, "fetch_pois", fake_fetch)
        monkeypatch.setattr(service, "kv_get", fake_kv_get)
        monkeypatch.setattr(service, "kv_set", fake_kv_set)

        assert call(None) == {"elements": []}
        assert seen.get("called"), "omitting categories must still query upstream"

    def test_default_set_is_the_legacy_three(self):
        assert set(service.DEFAULT_CATEGORIES) == {"supermarket", "convenience", "gym"}
