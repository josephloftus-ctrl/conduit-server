"""Entry point — python -m server"""

import os

import uvicorn
from .config import HOST, PORT

uvicorn.run(
    "server.app:app",
    host=HOST,
    port=PORT,
    reload=os.getenv("UVICORN_RELOAD", "").lower() == "true",
)
