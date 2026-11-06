from __future__ import annotations

from datetime import datetime, timezone

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import JSONResponse

from .config import Settings
from .db import connection_status, get_db, mongo_lifespan
from .errors import AppError, Conflict
from .events import ingest_event
from .exceptions import apply_exception_action, decide_recommended_action, exception_queue
from .profiles import PROFILES, get_profile
from .schemas import EventInput, ExceptionActionInput, RecommendationDecisionInput
from .security import guard_write_request
from .services import entity_overview, kpi_snapshot, portfolio_overview


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or Settings.from_env(require_uri=False)
    app = FastAPI(title="Atlas Control Tower Demo (FastAPI)", version="0.1.0-python", lifespan=mongo_lifespan)
    app.state.settings = settings
    app.middleware("http")(guard_write_request)

    @app.exception_handler(AppError)
    async def app_error_handler(_: Request, error: AppError):
        return JSONResponse(status_code=error.status_code, content={"detail": error.detail})

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

    async def write_ready(request: Request, profile) -> object:
        db = get_db(request.app)
        now = datetime.now(timezone.utc)
        active = await db["demo_state"].find_one({"_id": "maintenance", "active": True, "until": {"$gt": now}}, {"op": 1})
        if active:
            raise HTTPException(status_code=503, detail=f"Maintenance in progress ({active.get('op', 'unknown')}). Views resume automatically when it finishes.")
        entity = await db["tracked_entities"].find_one({"profile": profile.id, "trackedEntityId": profile.entity_id}, {"_id": 1})
        if not entity:
            raise Conflict("The demo database is not initialized. Initialize the Node reference demo before submitting events.")
        return db

    @app.get("/api/overview")
    async def overview(request: Request, profile: str | None = None):
        return await entity_overview(get_db(request.app), profile_from_query(profile))

    @app.get("/api/portfolio")
    async def portfolio(request: Request, profile: str | None = None):
        return await portfolio_overview(get_db(request.app), profile_from_query(profile))

    @app.get("/api/kpis")
    async def kpis(request: Request, profile: str | None = None, fresh: bool = False):
        return await kpi_snapshot(get_db(request.app), profile_from_query(profile), max_age_seconds=0 if fresh else 15)

    @app.get("/api/exceptions")
    async def exceptions(request: Request, profile: str | None = None):
        selected = profile_from_query(profile)
        return {"queue": await exception_queue(get_db(request.app), selected)}

    @app.post("/api/events")
    async def post_event(request: Request, body: EventInput, profile: str | None = None):
        selected = profile_from_query(profile)
        return await ingest_event(await write_ready(request, selected), selected, body)

    @app.post("/api/exceptions/{exception_id}/action")
    async def post_exception_action(request: Request, exception_id: str, body: ExceptionActionInput, profile: str | None = None):
        selected = profile_from_query(profile)
        return await apply_exception_action(await write_ready(request, selected), exception_id, body.model_dump())

    @app.post("/api/exceptions/{exception_id}/recommendation")
    async def post_recommendation_decision(request: Request, exception_id: str, body: RecommendationDecisionInput, profile: str | None = None):
        selected = profile_from_query(profile)
        return await decide_recommended_action(await write_ready(request, selected), exception_id, body.model_dump())

    return app


app = create_app()