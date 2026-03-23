from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
import httpx
import os
import json
import asyncio
from dotenv import load_dotenv

load_dotenv()

app = FastAPI()

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

ORS_API_KEY = os.getenv("ORS_API_KEY")

MODE_MAPPING = {
    "walking": "foot-walking",
    "cycling": "cycling-regular",
    "driving": "driving-car"
}

SPEED_MAP = {
    "walking": 80,
    "cycling": 250,
    "driving": 800
}

# ---------------------------------------------------------------------------
# Upstash KV cache (gracefully skipped if env vars not set)
# ---------------------------------------------------------------------------

_UPSTASH_URL = os.getenv("UPSTASH_REDIS_REST_URL")
_UPSTASH_TOKEN = os.getenv("UPSTASH_REDIS_REST_TOKEN")


async def kv_get(key: str):
    if not _UPSTASH_URL or not _UPSTASH_TOKEN:
        return None
    try:
        async with httpx.AsyncClient(timeout=3.0) as client:
            resp = await client.get(
                f"{_UPSTASH_URL}/get/{key}",
                headers={"Authorization": f"Bearer {_UPSTASH_TOKEN}"}
            )
            result = resp.json().get("result")
            return json.loads(result) if result else None
    except Exception as e:
        print(f"[kv] GET {key} failed: {e}")
        return None


async def kv_set(key: str, value, ttl_seconds: int):
    if not _UPSTASH_URL or not _UPSTASH_TOKEN:
        return
    try:
        async with httpx.AsyncClient(timeout=3.0) as client:
            await client.post(
                f"{_UPSTASH_URL}/set/{key}",
                headers={"Authorization": f"Bearer {_UPSTASH_TOKEN}"},
                json={"value": json.dumps(value), "ex": ttl_seconds}
            )
    except Exception as e:
        print(f"[kv] SET {key} failed: {e}")


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------

@app.get("/api/main/isochrone")
async def get_isochrone(lng: float, lat: float, minutes: int = 10, profile: str = "walking"):
    print(f"[isochrone] lng={lng} lat={lat} minutes={minutes} profile={profile}")

    cache_key = f"iso:{lng:.5f}:{lat:.5f}:{minutes}:{profile}"
    cached = await kv_get(cache_key)
    if cached:
        print(f"[isochrone] cache hit {cache_key}")
        return cached

    ors_profile = MODE_MAPPING.get(profile, "foot-walking")
    url = f"https://api.openrouteservice.org/v2/isochrones/{ors_profile}"
    body = {"locations": [[lng, lat]], "range": [minutes * 60], "range_type": "time"}

    async with httpx.AsyncClient(timeout=15.0) as client:
        try:
            resp = await client.post(url, json=body, headers={"Authorization": ORS_API_KEY})
            resp.raise_for_status()
            data = resp.json()
        except httpx.TimeoutException:
            raise HTTPException(status_code=504, detail="ORS timeout")
        except httpx.HTTPStatusError as e:
            raise HTTPException(status_code=502, detail=f"ORS error: {e.response.status_code}")
        except httpx.RequestError as e:
            raise HTTPException(status_code=502, detail=f"ORS network error: {e}")

    print(f"[isochrone] features={len(data.get('features', []))}")
    await kv_set(cache_key, data, ttl_seconds=86400)  # 24h
    return data


@app.get("/api/main/pois")
async def get_pois(lng: float, lat: float, minutes: int = 10, profile: str = "walking"):
    print(f"[pois] lng={lng} lat={lat} minutes={minutes} profile={profile}")

    cache_key = f"pois:{lng:.3f}:{lat:.3f}:{minutes}:{profile}"
    cached = await kv_get(cache_key)
    if cached:
        print(f"[pois] cache hit {cache_key}")
        return cached

    radius = minutes * SPEED_MAP.get(profile, 80)
    query = f"""
    [out:json];
    (
      node["shop"~"supermarket|convenience"](around:{radius}, {lat}, {lng});
      node["leisure"="fitness_centre"](around:{radius}, {lat}, {lng});
      node["amenity"="gym"](around:{radius}, {lat}, {lng});
    );
    out body;
    """

    async with httpx.AsyncClient(timeout=20.0) as client:
        try:
            resp = await client.post("https://overpass-api.de/api/interpreter", data={"data": query})
            resp.raise_for_status()
            data = resp.json()
        except httpx.ReadTimeout:
            print("[pois] Overpass read timeout")
            raise HTTPException(status_code=504, detail="Overpass timed out, try again")
        except httpx.HTTPStatusError as e:
            print(f"[pois] Overpass HTTP error: {e.response.status_code}")
            raise HTTPException(status_code=502, detail=f"Overpass error: {e.response.status_code}")
        except httpx.RequestError as e:
            print(f"[pois] network error: {e}")
            raise HTTPException(status_code=502, detail=f"Overpass network error: {e}")

    print(f"[pois] elements={len(data.get('elements', []))}")
    await kv_set(cache_key, data, ttl_seconds=21600)  # 6h
    return data


@app.get("/api/main/directions")
async def get_directions(
    start_lng: float, start_lat: float,
    end_lng: float, end_lat: float,
    mode: str = "walking"
):
    print(f"[directions] {start_lng},{start_lat} → {end_lng},{end_lat} mode={mode}")
    profile = MODE_MAPPING.get(mode, "foot-walking")
    url = f"https://api.openrouteservice.org/v2/directions/{profile}"
    params = {"api_key": ORS_API_KEY, "start": f"{start_lng},{start_lat}", "end": f"{end_lng},{end_lat}"}

    async with httpx.AsyncClient(timeout=15.0) as client:
        try:
            resp = await client.get(url, params=params)
            resp.raise_for_status()
            return resp.json()
        except httpx.TimeoutException:
            raise HTTPException(status_code=504, detail="ORS directions timeout")
        except httpx.HTTPStatusError as e:
            raise HTTPException(status_code=502, detail=f"ORS error: {e.response.status_code}")
        except httpx.RequestError as e:
            raise HTTPException(status_code=502, detail=f"ORS network error: {e}")


@app.get("/api/main/bulk_directions")
async def get_bulk_directions(start_lng: float, start_lat: float, targets: str, mode: str = "walking"):
    """targets format: 'lng1,lat1;lng2,lat2;...'"""
    target_list = [t.split(',') for t in targets.split(';')]
    profile = MODE_MAPPING.get(mode, "foot-walking")

    async def fetch_one(end_lng, end_lat):
        url = f"https://api.openrouteservice.org/v2/directions/{profile}"
        params = {"api_key": ORS_API_KEY, "start": f"{start_lng},{start_lat}", "end": f"{end_lng},{end_lat}"}
        async with httpx.AsyncClient(timeout=15.0) as client:
            try:
                res = await client.get(url, params=params)
                return res.json() if res.status_code == 200 else None
            except Exception:
                return None

    results = await asyncio.gather(*[fetch_one(t[0], t[1]) for t in target_list], return_exceptions=True)
    return {f"{t[0]},{t[1]}": r for t, r in zip(target_list, results) if r and not isinstance(r, Exception)}


@app.get("/api/main/region")
async def get_region(lng: float, lat: float):
    print(f"[region] lng={lng} lat={lat}")

    cache_key = f"region:{lng:.3f}:{lat:.3f}"
    cached = await kv_get(cache_key)
    if cached:
        print(f"[region] cache hit {cache_key}")
        return cached

    url = "https://api.pdok.nl/bzk/locatieserver/search/v3_1/reverse"
    params = {"lat": lat, "lon": lng, "type": "gemeente", "fl": "gemeentecode,gemeentenaam,id"}

    async with httpx.AsyncClient(timeout=10.0) as client:
        try:
            resp = await client.get(url, params=params)
            resp.raise_for_status()
            docs = resp.json().get("response", {}).get("docs", [])
            if not docs:
                raise HTTPException(status_code=404, detail="No municipality found")
            doc = docs[0]
            result = {"name": doc.get("gemeentenaam", ""), "regionCode": f"GM{doc.get('gemeentecode', '')}"}
        except httpx.TimeoutException:
            raise HTTPException(status_code=504, detail="PDOK timeout")
        except httpx.HTTPStatusError as e:
            raise HTTPException(status_code=502, detail=f"PDOK error: {e.response.status_code}")
        except httpx.RequestError as e:
            raise HTTPException(status_code=502, detail=f"PDOK network error: {e}")

    print(f"[region] OK: {result}")
    await kv_set(cache_key, result, ttl_seconds=604800)  # 7d
    return result


if __name__ == "__main__":
    import uvicorn
    uvicorn.run("backend.main:app", host="0.0.0.0", port=8000, reload=True)
