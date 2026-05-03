import httpx
from tests.conftest import BASE_URL


def test_health():
    r = httpx.get(f"{BASE_URL}/api/main/health", timeout=10)
    assert r.status_code == 200
    body = r.json()
    assert body["status"] == "ok"
    assert "timestamp" in body
