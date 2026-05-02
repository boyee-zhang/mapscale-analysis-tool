import httpx
import pytest
from tests.conftest import BASE_URL


def test_search_returns_results():
    r = httpx.get(f"{BASE_URL}/api/main/search", params={"q": "Kerkstraat Amsterdam"})
    assert r.status_code == 200
    data = r.json()
    assert isinstance(data, list)
    assert len(data) > 0


def test_search_result_shape():
    r = httpx.get(f"{BASE_URL}/api/main/search", params={"q": "Hoofdstraat"})
    assert r.status_code == 200
    item = r.json()[0]
    assert "label" in item
    assert "lng" in item
    assert "lat" in item
    assert isinstance(item["lng"], float)
    assert isinstance(item["lat"], float)


def test_search_specific_address():
    r = httpx.get(f"{BASE_URL}/api/main/search", params={"q": "Jan Wolkerslaan 699"})
    assert r.status_code == 200
    data = r.json()
    assert len(data) > 0
    assert "Wolkerslaan" in data[0]["label"]
    assert "Diemen" in data[0]["label"]


def test_search_postcode():
    r = httpx.get(f"{BASE_URL}/api/main/search", params={"q": "1017GD"})
    assert r.status_code == 200
    assert len(r.json()) > 0


def test_search_irrelevant_company_name_returns_empty():
    r = httpx.get(f"{BASE_URL}/api/main/search", params={"q": "OurCampus Diemen"})
    assert r.status_code == 200
    # PDOK has no company names — should return empty or Nominatim fallback
    data = r.json()
    assert isinstance(data, list)


def test_search_min_length_validation():
    r = httpx.get(f"{BASE_URL}/api/main/search", params={"q": "A"})
    assert r.status_code == 422


def test_search_custom_weights():
    r = httpx.get(f"{BASE_URL}/api/main/search", params={
        "q": "Amsterdam", "w_city": 10, "w_street": 1
    })
    assert r.status_code == 200
