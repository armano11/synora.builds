"""FastAPI server runner.

Starts the FastAPI server on port 8000 using Uvicorn.
"""
from __future__ import annotations

import uvicorn

if __name__ == "__main__":
    uvicorn.run("server:app", host="0.0.0.0", port=8000, reload=True)
