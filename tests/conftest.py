import os
import pytest

BASE_URL = os.environ.get("BASE_URL", "http://localhost:8000").rstrip("/")


@pytest.fixture
def base_url():
    return BASE_URL

# Skip markers for tests that depend on third-party API keys.
#
# The ORS tests are skipped in CI for two measured reasons, and NOT because of an IP ban.
# An earlier version of this comment claimed the free tier blocks datacenter ranges; that
# hypothesis was disproved by varying one axis at a time — the same key from the same host
# answered 200 in 240 ms on a different endpoint. See docs/pg-notes/ors-profile-outage.md.
# The real reasons:
#   1. The free tier allows 50 requests/day/ENDPOINT (measured; undocumented). A CI run on
#      every push would spend a visible fraction of the day's quota on each endpoint.
#   2. The cycling-* graphs are broken upstream while still answering HTTP 200, so a green
#      assertion there would prove nothing anyway.
requires_ors = pytest.mark.skipif(
    not os.environ.get("ORS_API_KEY") or bool(os.environ.get("CI")),
    reason="ORS free tier is 50 req/day/endpoint — run locally with ORS_API_KEY set",
)
