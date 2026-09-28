"""Vercel serverless entry point: exposes the FastAPI app from backend/.

Vercel routes /api/* here (see vercel.json). The same app runs under uvicorn locally
and in Docker; nothing in backend/ is Vercel-specific.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "backend"))

from app.main import app  # noqa: E402

__all__ = ["app"]
