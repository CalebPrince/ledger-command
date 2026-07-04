"""
server.py
----------
Entry point for the AI-Powered Accounting Command Center backend.

Run with:
    python server.py

This starts a Uvicorn server on http://0.0.0.0:8000, serves the
Bootstrap/Vanilla-JS frontend from ../frontend, and mounts all
RBAC-protected API routes from routes.py.
"""

from contextlib import asynccontextmanager
from pathlib import Path

import uvicorn
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse

from database import init_db
from routes import router as api_router

FRONTEND_DIR = Path(__file__).parent.parent / "frontend"


@asynccontextmanager
async def lifespan(app: FastAPI):
    init_db(seed=True)
    yield


app = FastAPI(
    title="AI-Powered Accounting Command Center",
    description="RBAC-secured backend for Super Admins, Admins, Employees, and Clients.",
    version="1.0.0",
    lifespan=lifespan,
)

# CORS: tighten allow_origins to your real domain(s) in production.
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(api_router)

# Serve the frontend as static files so the whole app can run from one process.
if FRONTEND_DIR.exists():
    app.mount("/static", StaticFiles(directory=FRONTEND_DIR), name="static")

    @app.get("/")
    def serve_landing():
        """Public marketing landing page -- the front door of the site."""
        return FileResponse(FRONTEND_DIR / "index.html")

    @app.get("/login")
    def serve_login():
        """The authenticated SPA shell (login screen + role-scoped app)."""
        return FileResponse(FRONTEND_DIR / "app.html")

    @app.get("/register")
    def serve_register():
        """Public self-service firm signup page."""
        return FileResponse(FRONTEND_DIR / "register.html")


if __name__ == "__main__":
    uvicorn.run("server:app", host="0.0.0.0", port=8000, reload=True)
