import os
import pytest

BASE_URL = os.environ.get("BASE_URL", "http://localhost:8000").rstrip("/")


@pytest.fixture
def base_url():
    return BASE_URL

# Skip markers for tests that depend on third-party API keys.
# ORS free tier blocks cloud/datacenter IP ranges (GitHub Actions uses Azure IPs),
# so isochrone tests are skipped in CI and only run in local dev.
requires_ors = pytest.mark.skipif(
    not os.environ.get("ORS_API_KEY") or bool(os.environ.get("CI")),
    reason="ORS blocks cloud/CI IPs — run locally with ORS_API_KEY set",
)
