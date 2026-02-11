"""Entry point — python -m server"""

import uvicorn
from .config import HOST, PORT

uvicorn.run("server.app:app", host=HOST, port=PORT, reload=True)
