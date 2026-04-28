import os
import pytest


@pytest.fixture(scope="session")
def base_url():
    url = os.environ.get("BASE_URL", "").rstrip("/")
    if not url:
        pytest.skip("BASE_URL environment variable not set")
    return url
