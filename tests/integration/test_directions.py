import httpx
from tests.conftest import BASE_URL, requires_ors

# Amsterdam Centraal → Dam Square (~600m walk)
PARAMS = {
    "start_lng": 4.9001, "start_lat": 52.3791,
    "end_lng": 4.8952, "end_lat": 52.3731,
    "mode": "walking",
}


@requires_ors
def test_directions_returns_geojson_linestring():
    r = httpx.get(f"{BASE_URL}/api/main/directions", params=PARAMS, timeout=30)
    assert r.status_code == 200, f"Got {r.status_code}: {r.text}"
    body = r.json()
    assert body["type"] == "FeatureCollection"
    feature = body["features"][0]
    assert feature["geometry"]["type"] == "LineString"
    assert len(feature["geometry"]["coordinates"]) > 1


@requires_ors
def test_directions_has_summary():
    r = httpx.get(f"{BASE_URL}/api/main/directions", params=PARAMS, timeout=30)
    assert r.status_code == 200, f"Got {r.status_code}: {r.text}"
    summary = r.json()["features"][0]["properties"]["summary"]
    assert summary["distance"] > 0
    assert summary["duration"] > 0
