import os
from dotenv import load_dotenv

load_dotenv()
load_dotenv(os.path.join(os.path.dirname(__file__), "../../backend/.env"))  # local dev

ORS_API_KEY = os.getenv("ORS_API_KEY")
TOMTOM_API_KEY = os.getenv("TOMTOM_API_KEY")
UPSTASH_URL = os.getenv("UPSTASH_REDIS_REST_URL")
UPSTASH_TOKEN = os.getenv("UPSTASH_REDIS_REST_TOKEN")

MODE_MAPPING = {
    "walking": "foot-walking",
    "cycling": "cycling-regular",
    "driving": "driving-car",
}

SPEED_MAP = {
    "walking": 80,
    "cycling": 250,
    "driving": 800,
}
