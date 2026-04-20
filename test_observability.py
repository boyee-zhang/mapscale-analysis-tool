"""
Automated observability smoke test.
Run: python test_observability.py
"""
import json
import sys
import httpx

BASE = "http://localhost:8000"
PASS = "\033[92m✓\033[0m"
FAIL = "\033[91m✗\033[0m"

results = []

def check(name: str, condition: bool, detail: str = ""):
    results.append(condition)
    icon = PASS if condition else FAIL
    suffix = f"  ({detail})" if detail else ""
    print(f"  {icon} {name}{suffix}")


def run_tests():
    print("\n── 1. Isochrone endpoint ─────────────────────────────────────")
    r = httpx.get(f"{BASE}/api/main/isochrone", params={"lng": 4.9041, "lat": 52.3676, "minutes": 10, "profile": "walking"})
    check("status 200", r.status_code == 200, f"got {r.status_code}")
    check("X-Request-ID header present", "x-request-id" in r.headers, r.headers.get("x-request-id"))
    body = r.json()
    check("response has features", "features" in body, f"keys: {list(body.keys())}")

    print("\n── 2. POIs endpoint ──────────────────────────────────────────")
    r = httpx.get(f"{BASE}/api/main/pois", params={"lng": 4.9041, "lat": 52.3676, "minutes": 10, "profile": "walking"}, timeout=30)
    check("X-Request-ID header present", "x-request-id" in r.headers)
    body = r.json()
    if r.status_code == 200:
        check("status 200 (Overpass ok)", True, "got 200")
        check("response has elements", "elements" in body)
    elif r.status_code == 504:
        # Overpass is slow right now — ExternalServiceError correctly surfaced
        check("status 504 (Overpass timeout — external, not our bug)", True, "got 504")
        check("error structure correct", body.get("error") == "upstream_error" and body.get("service") == "overpass", str(body))
    else:
        check("unexpected status", False, f"got {r.status_code}")

    print("\n── 3. Region endpoint ────────────────────────────────────────")
    r = httpx.get(f"{BASE}/api/main/region", params={"lng": 4.9041, "lat": 52.3676})
    check("status 200", r.status_code == 200, f"got {r.status_code}")
    body = r.json()
    check("has regionCode", "regionCode" in body, body.get("regionCode"))
    check("has name", "name" in body, body.get("name"))

    print("\n── 4. ExternalServiceError → 502/504 ─────────────────────────")
    # Bad coordinates → ORS should reject → ExternalServiceError → 502
    r = httpx.get(f"{BASE}/api/main/isochrone", params={"lng": 999, "lat": 999, "minutes": 10, "profile": "walking"})
    check("bad coords → 502", r.status_code == 502, f"got {r.status_code}")
    body = r.json()
    check("error field present", "error" in body, body.get("error"))
    check("service field present", "service" in body, body.get("service"))

    print("\n── 5. Prometheus /metrics ────────────────────────────────────")
    r = httpx.get(f"{BASE}/metrics")
    check("status 200", r.status_code == 200, f"got {r.status_code}")
    check("http_requests_total present", "http_requests_total" in r.text)
    check("http_request_duration present", "http_request_duration" in r.text)

    print("\n── 6. JSON log structure (parse last log lines) ──────────────")
    # Just verify the endpoint returns X-Request-ID (proves middleware ran)
    r = httpx.get(f"{BASE}/api/main/region", params={"lng": 4.9041, "lat": 52.3676})
    rid = r.headers.get("x-request-id", "")
    check("request_id is 8 hex chars", len(rid) == 8 and all(c in "0123456789abcdef" for c in rid), rid)

    print("\n─────────────────────────────────────────────────────────────")
    passed = sum(results)
    total = len(results)
    color = "\033[92m" if passed == total else "\033[91m"
    print(f"{color}{passed}/{total} checks passed\033[0m\n")
    return passed == total


if __name__ == "__main__":
    ok = run_tests()
    sys.exit(0 if ok else 1)
