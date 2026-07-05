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

import os
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

app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        "https://ledger-command.duckdns.org",
        "http://localhost:8000",
        "http://127.0.0.1:8000",
    ],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(api_router)


# Frontend files change on every deploy but have no versioned filenames, so
# make browsers revalidate instead of trusting heuristic caching: unchanged
# files still get a cheap 304, changed ones arrive without a hard refresh.
@app.middleware("http")
async def revalidate_frontend_cache(request, call_next):
    response = await call_next(request)
    if request.url.path.startswith("/static") or request.url.path in (
        "/", "/login", "/register", "/integrations/callback",
    ):
        response.headers["Cache-Control"] = "no-cache"
    return response


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

    @app.get("/integrations/callback")
    def serve_integrations_callback():
        """
        Landing page Composio redirects the user's browser back to after they
        complete a real OAuth consent flow (e.g. connecting Gmail). The app
        itself polls GET /api/integrations/gmail/status separately, so this
        page just needs to tell the user they're done and can close the tab.
        """
        return FileResponse(FRONTEND_DIR / "integrations-callback.html")


if __name__ == "__main__":
    # PORT is set in production (systemd runs this on 8001 behind Apache);
    # proxy_headers makes uvicorn honor X-Forwarded-Proto/Host from the
    # reverse proxy so request.base_url reflects the public https origin.
    uvicorn.run(
        "server:app",
        host="0.0.0.0",
        port=int(os.environ.get("PORT", "8000")),
        reload=os.environ.get("ENV", "dev") != "production",
        proxy_headers=True,
        forwarded_allow_ips="127.0.0.1",
    )
