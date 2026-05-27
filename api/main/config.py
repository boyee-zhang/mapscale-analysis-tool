import os
from dotenv import load_dotenv

load_dotenv()  # loads .env from project root

ORS_API_KEY = os.getenv("ORS_API_KEY")
ES_URL = os.getenv("ES_URL")  # optional — omit to disable ES and fall back to PDOK/Nominatim
TOMTOM_API_KEY = os.getenv("TOMTOM_API_KEY")
UPSTASH_URL = os.getenv("UPSTASH_REDIS_REST_URL")
UPSTASH_TOKEN = os.getenv("UPSTASH_REDIS_REST_TOKEN")
ETL_CRON_TOKEN = os.getenv("ETL_CRON_TOKEN")
DEEPSEEK_API_KEY = os.getenv("DEEPSEEK_API_KEY")

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
