import os
import pytest

BASE_URL = os.environ.get("BASE_URL", "http://localhost:8000").rstrip("/")

# Skip markers for tests that depend on third-party API keys.
# Tests are skipped (not failed) when the corresponding key is absent —
# this keeps CI green when secrets aren't configured.
requires_ors = pytest.mark.skipif(
    not os.environ.get("ORS_API_KEY"),
    reason="ORS_API_KEY not set",
)
