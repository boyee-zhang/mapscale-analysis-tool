import sys
import os

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from api.main.app import app  # single source of truth

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)
