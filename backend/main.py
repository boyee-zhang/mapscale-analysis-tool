from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
import httpx
import os
from dotenv import load_dotenv
import asyncio 

load_dotenv()
app = FastAPI()

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

SUPPORTED_SCHEMA_VERSIONS = {"1.0", "1.1"}

MODE_MAPPING = {
    "walking": "foot-walking",
    "cycling": "cycling-regular",
    "driving": "driving-car"
}

ORS_API_KEY = os.getenv("ORS_API_KEY") #  OpenRouteService Key

@app.get("/api/isochrone")
async def get_isochrone(lng: float, lat: float, minutes: int = 10, profile: str = "walking"):
    # walking -> foot-walking, cycling -> cycling-regular, driving -> driving-car
    profile_map = {
        "walking": "foot-walking",
        "cycling": "cycling-regular",
        "driving": "driving-car"
    }
    ors_profile = profile_map.get(profile, "foot-walking")
    
    url = f"https://api.openrouteservice.org/v2/isochrones/{ors_profile}"
    headers = {"Authorization": ORS_API_KEY}
    body = {
        "locations": [[lng, lat]],
        "range": [minutes * 60], 
        "range_type": "time"
    }
    
    async with httpx.AsyncClient() as client:
        resp = await client.post(url, json=body, headers=headers)
        if resp.status_code != 200:
            raise HTTPException(status_code=400, detail=f"ORS Error: {resp.text}")
        return resp.json()

@app.get("/api/pois")
async def get_pois(lng: float, lat: float, minutes: int = 10, profile: str = "walking"):
    speeds = {"walking": 80, "cycling": 250, "driving": 800}
    radius = minutes * speeds.get(profile, 80)
    
    overpass_url = "https://overpass-api.de/api/interpreter"
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
            resp = await client.post(overpass_url, data={"data": query})
            resp.raise_for_status() # if Overpass throw Exception
            return resp.json()
        except httpx.ReadTimeout:
            print("Overpass API timed out!")
            raise HTTPException(status_code=504, detail="Area too large, Overpass timed out")

@app.get("/api/analysis")
async def get_analysis(lng: float, lat: float, minutes: int = 10, profile: str = "walking"):
    speeds = {
        "walking": 80,  # 80m/min
        "cycling": 250, # 250m/min
        "driving": 600  # 600m/min
    }
    radius = minutes * speeds.get(profile, 80)

    query = f"""
    [out:json];
    (
      node["shop"~"supermarket|convenience"](around:{radius}, {lat}, {lng});
      node["leisure"="fitness_centre"](around:{radius}, {lat}, {lng});
    );
    out body;
    """
    iso_geojson = await fetch_isochrone_from_provider(lng, lat, minutes, profile)

    return {
        "iso": iso_geojson,
        "pois": await fetch_overpass_data(query)
    }

@app.get("/api/directions")
async def get_directions(
    start_lng: float, 
    start_lat: float, 
    end_lng: float, 
    end_lat: float, 
    mode: str = "walking" 
):
    profile = MODE_MAPPING.get(mode, "foot-walking")
    
    url = f"https://api.openrouteservice.org/v2/directions/{profile}"
    params = {
        "api_key": ORS_API_KEY,
        "start": f"{start_lng},{start_lat}",
        "end": f"{end_lng},{end_lat}"
    }
    
    async with httpx.AsyncClient() as client:
        response = await client.get(url, params=params)
        if response.status_code != 200:
            print(f"ORS Error: {response.text}")
            raise HTTPException(status_code=500, detail="Route calculation failed")
        
        return response.json()
    
    import asyncio

@app.get("/api/bulk_directions")
async def get_bulk_directions(start_lng: float, start_lat: float, targets: str, mode: str = "walking"):
    """
    targets formate: "lng1,lat1;lng2,lat2;..."
    """
    target_list = [t.split(',') for t in targets.split(';')]
    profile = MODE_MAPPING.get(mode, "foot-walking")
    
    async def fetch_one(end_lng, end_lat):
        url = f"https://api.openrouteservice.org/v2/directions/{profile}"
        params = {"api_key": ORS_API_KEY, "start": f"{start_lng},{start_lat}", "end": f"{end_lng},{end_lat}"}
        async with httpx.AsyncClient() as client:
            res = await client.get(url, params=params)
            return res.json() if res.status_code == 200 else None

    tasks = [fetch_one(t[0], t[1]) for t in target_list]
    results = await asyncio.gather(*tasks)
    
    return {f"{t[0]},{t[1]}": r for t, r in zip(target_list, results) if r}

@app.get("/api/region")
async def get_region(lng: float, lat: float):
    """Reverse geocode coordinates to Dutch municipality CBS code via PDOK."""
    url = "https://api.pdok.nl/bzk/locatieserver/search/v3_1/reverse"
    params = {"lat": lat, "lon": lng, "type": "gemeente", "fl": "gemeentecode,gemeentenaam,id"}
    print(f"[GET /api/region] lng={lng} lat={lat}")
    async with httpx.AsyncClient(timeout=10.0) as client:
        try:
            resp = await client.get(url, params=params)
            resp.raise_for_status()
            docs = resp.json().get("response", {}).get("docs", [])
            if not docs:
                raise HTTPException(status_code=404, detail="No municipality found for these coordinates")
            doc = docs[0]
            result = {
                "name": doc.get("gemeentenaam", ""),
                "regionCode": f"GM{doc.get('gemeentecode', '')}"
            }
            print(f"[GET /api/region] OK: {result}")
            return result
        except httpx.TimeoutException:
            print("[region] PDOK request timed out")
            raise HTTPException(status_code=504, detail="PDOK timeout")
        except httpx.RequestError as e:
            print(f"[region] network error: {e}")
            raise HTTPException(status_code=502, detail=f"PDOK network error: {e}")


if __name__ == "__main__":
    import uvicorn
    uvicorn.run("main:app", host="0.0.0.0", port=8000, reload=True)