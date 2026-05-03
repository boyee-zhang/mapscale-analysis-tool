import httpx
from tests.conftest import BASE_URL

# Amsterdam city centre
PARAMS = {"lng": 4.9041, "lat": 52.3676}


def test_region_returns_municipality():
    r = httpx.get(f"{BASE_URL}/api/main/region", params=PARAMS, timeout=15)
    assert r.status_code == 200
    body = r.json()
    assert isinstance(body["name"], str) and body["name"]
    assert body["regionCode"].startswith("GM"), f"unexpected regionCode: {body['regionCode']}"
