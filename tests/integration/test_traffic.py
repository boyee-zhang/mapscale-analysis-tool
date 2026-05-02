import httpx
from tests.conftest import BASE_URL


def test_traffic_tile_url_returns_url():
    r = httpx.get(f"{BASE_URL}/api/main/traffic/tile-url")
    assert r.status_code == 200
    data = r.json()
    assert "flowTileUrl" in data
    assert "tomtom.com" in data["flowTileUrl"]


def test_traffic_incidents_returns_geojson():
    params = {"min_lng": 4.88, "min_lat": 52.33, "max_lng": 4.96, "max_lat": 52.38}
    r = httpx.get(f"{BASE_URL}/api/main/traffic/incidents", params=params, timeout=15)
    assert r.status_code == 200
    data = r.json()
    assert data.get("type") == "FeatureCollection"
    assert "features" in data
