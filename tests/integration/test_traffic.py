import httpx
from tests.conftest import BASE_URL

LNG, LAT = 4.9041, 52.3676


def test_traffic_tile_url_is_valid_template():
    r = httpx.get(f"{BASE_URL}/api/main/traffic/tile-url", timeout=10)
    assert r.status_code == 200
    url = r.json()["flowTileUrl"]
    assert url.startswith("https://")
    assert "tomtom.com" in url
    assert "{z}" in url and "{x}" in url and "{y}" in url


def test_traffic_flow_schema():
    r = httpx.get(f"{BASE_URL}/api/main/traffic/flow", params={"lng": LNG, "lat": LAT}, timeout=15)
    assert r.status_code == 200
    body = r.json()
    assert isinstance(body["currentSpeed"], (int, float))
    assert isinstance(body["freeFlowSpeed"], (int, float))
    assert isinstance(body["congestionFactor"], float)
    assert 0.0 <= body["congestionFactor"] <= 1.0


def test_traffic_incidents_returns_geojson():
    params = {"min_lng": 4.85, "min_lat": 52.35, "max_lng": 4.95, "max_lat": 52.40}
    r = httpx.get(f"{BASE_URL}/api/main/traffic/incidents", params=params, timeout=15)
    assert r.status_code == 200
    body = r.json()
    assert body["type"] == "FeatureCollection"
    assert isinstance(body["features"], list)
    for feature in body["features"]:
        assert feature["type"] == "Feature"
        assert feature["geometry"]["type"] == "LineString"
        assert len(feature["geometry"]["coordinates"]) >= 2
