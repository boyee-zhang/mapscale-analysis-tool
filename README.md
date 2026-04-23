# MapScale Analysis Tool 

![Project Demo](./docs/ScreenshotDriving.png)
![Project Demo](./docs/ScreenshotWalking.png)
![Project Demo](./docs/ScreenshotPathing.png)
A professional-grade geographic analysis tool built for urban accessibility and POI (Point of Interest) density studies. This project demonstrates the integration of complex spatial data with a modern reactive web stack.

## Tech Stack
* Frontend: React (Vite), MapLibre GL, Turf.js, Axios

* Backend: FastAPI (Python), HTTPX

* Data Providers: OpenRouteService (Isochrones), OpenStreetMap/Overpass API (POIs), Nominatim (Geocoding)

## Core Features
* Dynamic Isochrones: Calculate travel boundaries based on time (5-30 mins) and mode (Walking, Cycling, Driving) using real-world road networks.

* Reactive POI Analysis: Real-time filtering of supermarkets and gyms within the actual street-network boundary using the Turf.js spatial engine.

* Smart Address Search: Locate any Dutch address (e.g., `Jan Wolkerslaan 699` or postcode `1112ZH`) with smooth "FlyTo" animations and automatic re-calculation. Search is powered by a three-layer fallback: Elasticsearch cache → PDOK (Dutch address registry) → Nominatim (OSM).

* Operational Status: Integrated opening_hours logic to parse OSM data and display real-time status: Open Now, Closed, or Data Unknown.

## Engineering Insights: Map Rendering Logic
During development, I focused heavily on understanding the Map Rendering Pipeline. One of the major challenges was solving the "Marker Drifting" issue:

* The "Floating Point" Challenge: Initially, markers appeared to "drift" or lag behind when the map was zoomed or panned.

* Deep Understanding: I realized this was caused by a conflict between React's state-driven DOM updates and MapLibre's high-frequency coordinate-to-pixel projection.

* The Solution: By decoupling the UI state from the raw coordinate updates and strictly managing the Marker lifecycle (properly mounting/unmounting DOM elements via MapLibre's internal setLngLat API), I achieved perfect synchronization where markers remain pinned to their geographic coordinates regardless of map movement.

## System Architecture
### Backend (FastAPI)
* Parameterized Routing: Dynamically maps frontend profiles (walking/driving) to ORS routing engines.

* Adaptive Radius Querying: Automatically scales the Overpass QL around radius based on the selected travel mode to ensure sufficient data coverage.

* Resilience: Implemented custom HTTPX timeout and multi-endpoint fallback for high-latency spatial queries (e.g. three Overpass mirrors tried in sequence).

* Structured Logging: Every request is assigned a unique `X-Request-ID`. All log output is JSON (via `python-json-logger`), making it easy to trace a full request lifecycle across routers and external API clients.

### Frontend (React)
* Declarative Logic: Used useEffect observers to trigger data fetching only when core parameters (minutes, mode, centerLoc) change.

* Spatial Computing Layer: Utilizes useMemo to perform point-in-polygon collisions in the browser, ensuring the UI remains performant even with hundreds of POIs.

## Future Roadmap: AI Agent Integration
The next phase of this project involves pushing into the Geo-AI space:

* Natural Language GIS: Integrating an AI Agent (e.g., GPT-4o) to allow users to use voice commands: "Find me all gyms within a 15-minute walk of Diemen Zuid that are open right now."

* Auto-API Invocation: The Agent will parse intent, map it to our existing FastAPI endpoints, and visualize the results automatically.

* Smart Routing: Adding one-click navigation from the user's center point to any filtered POI.

## Address Search — Elasticsearch Setup (optional, local dev only)

The search feature uses a three-layer architecture. Elasticsearch is the first layer (fastest, supports custom field weights), but it is **optional** — the app falls back gracefully to PDOK and Nominatim when `ES_URL` is not set.

The live Vercel deployment runs without ES. To enable the full ES layer locally:

### 1. Start Elasticsearch
```bash
docker compose up -d   # starts ES on localhost:9200
```

### 2. Seed the index with Dutch addresses
```bash
# index 1 000 addresses across 20 common Dutch street names
python scripts/ingest_addresses.py

# or target a specific street / city
python scripts/ingest_addresses.py --terms "Jan Wolkerslaan" "Diemen" --rows 100
```

Data is sourced from the **PDOK Locatieserver** (Dutch government BAG address registry, free, no API key required). Subsequent searches automatically cache new results back into ES.

### 3. Configure the backend
Add to `.env` (project root):
```
ES_URL=http://localhost:9200
```

### Hosting ES in production
To run the full ES layer in a deployed environment, host Elasticsearch externally and set `ES_URL` to the connection string:

| Provider | Notes |
|---|---|
| **Elastic Cloud** | Official managed service. 14-day free trial, then from ~$16/month. |
| **Railway** | Deploy the ES Docker image directly. New accounts get $5 free credit. |
| **Self-hosted VPS** | Run `docker compose up -d` on any VPS (Hetzner, DigitalOcean, etc.). |

Set `ES_URL=https://user:password@your-cluster` in your environment and the app will automatically use ES instead of falling back to PDOK.

### Search field weights
Weights are tunable per-request via query params — no code change needed:
```
GET /api/main/search?q=1112ZH&w_postcode=8&w_street=1
GET /api/main/search?q=Hoofdstraat+Amsterdam&w_street=5&w_city=3
```

Default weights: `w_postcode=5`, `w_street=3`, `w_city=2`, `w_label=1`.

---

## Getting Started

### 1. Prerequisites
* Python 3.10+

* Node.js 18+

* An API Key from OpenRouteService

### 2. Clone the repo:
``` git clone https://github.com/boyee-zhang/mapscale-analysis-tool.git
cd mapscale-analysis-tool
```

### 3. Frontend Setup:
```bash
cd frontend
npm install
npm run dev
```
### 4. Backend Setup:
```bash
# from project root
python -m venv venv
source venv/bin/activate  # Windows: venv\Scripts\activate
pip install -r requirements.txt
```

Create a `.env` file in the project root:
```
ORS_API_KEY=your_openrouteservice_key
TOMTOM_API_KEY=your_tomtom_key
```

Start the backend:
```bash
uvicorn api.main.app:app --reload --port 8000
```
## 📝 License

Distributed under the Apache License 2.0. See `LICENSE` for more information.

The application will be available at http://localhost:5173.

This is just the start. Let's keep building, keep learning, and keep discovering. 🚀

**Happy New Year!** 🎇