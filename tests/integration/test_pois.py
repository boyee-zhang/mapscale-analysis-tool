import httpx
from tests.conftest import BASE_URL

PARAMS = {"lng": 4.936, "lat": 52.338, "minutes": 10, "profile": "walking"}


def test_pois_returns_elements_with_expected_shape():
    """One request, both assertions.

    This was two tests issuing byte-identical parameters. On the Overpass fallback path that
    costs two serial upstream calls and takes the mirror-timeout bet twice: CI has no
    DATABASE_URL, so /pois does not read the `poi` table, and no Upstash, so nothing caches
    the first answer for the second test to reuse. Keep it as one request.
    """
    r = httpx.get(f"{BASE_URL}/api/main/pois", params=PARAMS, timeout=30)
    assert r.status_code == 200

    data = r.json()
    assert "elements" in data

    elements = data["elements"]
    if elements:
        el = elements[0]
        assert "lat" in el
        assert "lon" in el
        assert "tags" in el
