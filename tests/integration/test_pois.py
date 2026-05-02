import httpx
from tests.conftest import BASE_URL

PARAMS = {"lng": 4.936, "lat": 52.338, "minutes": 10, "profile": "walking"}


def test_pois_returns_elements():
    r = httpx.get(f"{BASE_URL}/api/main/pois", params=PARAMS, timeout=30)
    assert r.status_code == 200
    data = r.json()
    assert "elements" in data


def test_pois_element_shape():
    r = httpx.get(f"{BASE_URL}/api/main/pois", params=PARAMS, timeout=30)
    assert r.status_code == 200
    elements = r.json()["elements"]
    if elements:
        el = elements[0]
        assert "lat" in el
        assert "lon" in el
        assert "tags" in el
