from datetime import datetime, timezone
from fastapi import APIRouter

router = APIRouter()


@router.get("/api/main/health")
async def health():
    return {"status": "ok", "timestamp": datetime.now(timezone.utc).isoformat()}
