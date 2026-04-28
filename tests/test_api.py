"""
API contract tests — run against a live deployment via BASE_URL env var.

Every test validates that the endpoint:
  1. Returns HTTP 200
  2. Returns the exact shape the frontend expects
"""
import httpx
import pytest

# Amsterdam city centre — stable, real coordinates with data in all APIs
LNG = 4.9041
LAT = 52.3676


# ── /health ──────────────────────────────────────────────────────────────────

def test_health(base_url):
    r = httpx.get(f"{base_url}/api/main/health", timeout=15)
    assert r.status_code == 200
    assert r.json() == {"status": "ok"}


# ── /search ──────────────────────────────────────────────────────────────────

def test_search_returns_list_of_labeled_points(base_url):
    r = httpx.get(f"{base_url}/api/main/search", params={"q": "Damrak Amsterdam"}, timeout=20)
    assert r.status_code == 200
    items = r.json()
    assert isinstance(items, list), "response must be a list"
    assert len(items) > 0, "search must return at least one result"
    for item in items:
        assert isinstance(item["label"], str) and item["label"], "label must be non-empty string"
        assert isinstance(item["lng"], float), "lng must be float"
        assert isinstance(item["lat"], float), "lat must be float"


# ── /isochrone ────────────────────────────────────────────────────────────────

def test_isochrone_returns_geojson_polygon(base_url):
    r = httpx.get(
        f"{base_url}/api/main/isochrone",
        params={"lng": LNG, "lat": LAT, "minutes": 10, "profile": "walking"},
        timeout=30,
    )
    assert r.status_code == 200
    body = r.json()
    assert body["type"] == "FeatureCollection"
    features = body["features"]
    assert len(features) > 0, "must contain at least one feature"
    geom = features[0]["geometry"]
    assert geom["type"] in ("Polygon", "MultiPolygon"), f"unexpected geometry type: {geom['type']}"
    assert len(geom["coordinates"]) > 0, "coordinates must not be empty"


@pytest.mark.parametrize("profile", ["walking", "cycling", "driving"])
def test_isochrone_all_profiles(base_url, profile):
    r = httpx.get(
        f"{base_url}/api/main/isochrone",
        params={"lng": LNG, "lat": LAT, "minutes": 10, "profile": profile},
        timeout=30,
    )
    assert r.status_code == 200, f"profile={profile} returned {r.status_code}"
    body = r.json()
    assert body["type"] == "FeatureCollection"
    assert len(body["features"]) > 0


# ── /pois ─────────────────────────────────────────────────────────────────────

def test_pois_returns_overpass_elements(base_url):
    r = httpx.get(
        f"{base_url}/api/main/pois",
        params={"lng": LNG, "lat": LAT, "minutes": 10, "profile": "walking"},
        timeout=30,
    )
    assert r.status_code == 200
    body = r.json()
    assert "elements" in body, "response must have 'elements' key"
    assert isinstance(body["elements"], list)
    # Amsterdam centre always has supermarkets/gyms within 10min walk
    assert len(body["elements"]) > 0, "expected at least one POI in Amsterdam centre"
    for el in body["elements"]:
        assert "id" in el
        assert "lat" in el and "lon" in el
        assert "tags" in el


# ── /directions ───────────────────────────────────────────────────────────────

def test_directions_returns_geojson_linestring(base_url):
    # Amsterdam Centraal → Dam Square (~600m walk)
    r = httpx.get(
        f"{base_url}/api/main/directions",
        params={
            "start_lng": 4.9001, "start_lat": 52.3791,
            "end_lng": 4.8952, "end_lat": 52.3731,
            "mode": "walking",
        },
        timeout=30,
    )
    assert r.status_code == 200
    body = r.json()
    assert body["type"] == "FeatureCollection"
    feature = body["features"][0]
    geom = feature["geometry"]
    assert geom["type"] == "LineString"
    assert len(geom["coordinates"]) > 1, "route must have at least two coordinate points"
    summary = feature["properties"]["summary"]
    assert summary["distance"] > 0, "distance must be positive"
    assert summary["duration"] > 0, "duration must be positive"


# ── /region ───────────────────────────────────────────────────────────────────

def test_region_returns_municipality(base_url):
    r = httpx.get(f"{base_url}/api/main/region", params={"lng": LNG, "lat": LAT}, timeout=20)
    assert r.status_code == 200
    body = r.json()
    assert isinstance(body["name"], str) and body["name"], "name must be non-empty"
    assert body["regionCode"].startswith("GM"), f"regionCode must start with 'GM', got: {body['regionCode']}"



# ── /traffic/tile-url ─────────────────────────────────────────────────────────

def test_traffic_tile_url_is_https(base_url):
    r = httpx.get(f"{base_url}/api/main/traffic/tile-url", timeout=15)
    assert r.status_code == 200
    body = r.json()
    assert "flowTileUrl" in body
    url = body["flowTileUrl"]
    assert url.startswith("https://"), f"flowTileUrl must be https, got: {url}"
    assert "{z}" in url and "{x}" in url and "{y}" in url, "URL must be a tile template"


# ── /traffic/flow ─────────────────────────────────────────────────────────────

def test_traffic_flow_schema(base_url):
    r = httpx.get(
        f"{base_url}/api/main/traffic/flow",
        params={"lng": LNG, "lat": LAT},
        timeout=20,
    )
    assert r.status_code == 200
    body = r.json()
    assert isinstance(body["currentSpeed"], (int, float)), "currentSpeed must be numeric"
    assert isinstance(body["freeFlowSpeed"], (int, float)), "freeFlowSpeed must be numeric"
    assert isinstance(body["congestionFactor"], float), "congestionFactor must be float"
    assert 0.0 <= body["congestionFactor"] <= 1.0, f"congestionFactor out of range: {body['congestionFactor']}"


# ── /traffic/incidents ────────────────────────────────────────────────────────

def test_traffic_incidents_returns_geojson_feature_collection(base_url):
    r = httpx.get(
        f"{base_url}/api/main/traffic/incidents",
        params={"min_lng": 4.85, "min_lat": 52.35, "max_lng": 4.95, "max_lat": 52.40},
        timeout=20,
    )
    assert r.status_code == 200
    body = r.json()
    assert body["type"] == "FeatureCollection"
    assert isinstance(body["features"], list)
    for feature in body["features"]:
        assert feature["type"] == "Feature"
        assert feature["geometry"]["type"] == "LineString"
        assert len(feature["geometry"]["coordinates"]) >= 2
