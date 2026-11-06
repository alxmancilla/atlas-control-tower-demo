from __future__ import annotations

from fastapi import Depends, FastAPI, HTTPException, Request

from .config import Settings
from .db import connection_status, get_db, mongo_lifespan
from .profiles import PROFILES, get_profile
from .services import entity_overview, kpi_snapshot, portfolio_overview


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or Settings.from_env(require_uri=False)
    app = FastAPI(title="Atlas Control Tower Demo (FastAPI)", version="0.1.0-python", lifespan=mongo_lifespan)
    app.state.settings = settings

    @app.get("/health")
    async def health():
        return {"status": "ok", "implementation": "python-fastapi"}

    @app.get("/api/status")
    async def status(request: Request):
        return await connection_status(request.app)

    @app.get("/api/profiles")
    async def profiles():
        return {"defaultProfile": settings.default_profile, "profiles": [profile.public() for profile in PROFILES.values()]}

    def profile_from_query(profile: str | None) -> object:
        try:
            return get_profile(profile or settings.default_profile)
        except ValueError as error:
            raise HTTPException(status_code=400, detail=str(error)) from error

    @app.get("/api/overview")
    async def overview(request: Request, profile: str | None = None):
        return await entity_overview(get_db(request.app), profile_from_query(profile))

    @app.get("/api/portfolio")
    async def portfolio(request: Request, profile: str | None = None):
        return await portfolio_overview(get_db(request.app), profile_from_query(profile))

    @app.get("/api/kpis")
    async def kpis(request: Request, profile: str | None = None):
        return await kpi_snapshot(get_db(request.app), profile_from_query(profile))

    return app


app = create_app()