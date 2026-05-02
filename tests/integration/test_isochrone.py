import httpx
import pytest
from tests.conftest import BASE_URL

PARAMS = {"lng": 4.936, "lat": 52.338, "minutes": 10, "profile": "walking"}


def test_isochrone_returns_geojson():
    r = httpx.get(f"{BASE_URL}/api/main/isochrone", params=PARAMS, timeout=30)
    assert r.status_code == 200
    data = r.json()
    assert data.get("type") == "FeatureCollection"
    assert len(data["features"]) == 1


def test_isochrone_has_bbox():
    r = httpx.get(f"{BASE_URL}/api/main/isochrone", params=PARAMS, timeout=30)
    assert r.status_code == 200
    assert "bbox" in r.json()


def test_isochrone_cycling_mode():
    params = {**PARAMS, "profile": "cycling", "minutes": 15}
    r = httpx.get(f"{BASE_URL}/api/main/isochrone", params=params, timeout=30)
    assert r.status_code == 200
    assert len(r.json()["features"]) == 1


def test_isochrone_driving_mode():
    params = {**PARAMS, "profile": "driving-car", "minutes": 5}
    r = httpx.get(f"{BASE_URL}/api/main/isochrone", params=params, timeout=30)
    assert r.status_code == 200
    assert len(r.json()["features"]) == 1
