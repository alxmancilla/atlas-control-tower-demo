from __future__ import annotations

import asyncio
import json
from datetime import datetime, timezone
from pathlib import Path

from fastapi import FastAPI, HTTPException, Request
from fastapi.encoders import jsonable_encoder
from fastapi.responses import FileResponse, JSONResponse, StreamingResponse

from .config import Settings
from .db import connection_status, get_db, mongo_lifespan
from .errors import AppError, Conflict
from .events import ingest_event
from .exceptions import apply_exception_action, decide_recommended_action, exception_queue
from .profiles import PROFILES, get_profile
from .search import find_similar, run_search, search_status
from .schemas import EventInput, ExceptionActionInput, RecommendationDecisionInput, TelemetryReadingInput
from .security import guard_write_request
from .services import entity_overview, kpi_snapshot, portfolio_overview
from .telemetry import add_reading, telemetry_view

UI_PATH = Path(__file__).parent / "static" / "index.html"


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or Settings.from_env(require_uri=False)
    app = FastAPI(title="Atlas Control Tower Demo (FastAPI)", version="0.1.0-python", lifespan=mongo_lifespan)
    app.state.settings = settings
    app.middleware("http")(guard_write_request)

    def sse_event(event: str, data: dict) -> str:
        return f"event: {event}\ndata: {json.dumps(jsonable_encoder(data), separators=(',', ':'))}\n\n"

    async def stream_events(request: Request):
        yield "retry: 3000\n\n"
        feed = request.app.state.change_feed
        if not feed:
            yield sse_event("stream-status", {"state": "unavailable", "error": "Not connected to Atlas"})
            return
        queue = feed.subscribe()
        try:
            yield sse_event("stream-status", feed.state)
            while not await request.is_disconnected():
                try:
                    event, data = await asyncio.wait_for(queue.get(), timeout=20)
                    yield sse_event(event, data)
                except TimeoutError:
                    yield ": heartbeat\n\n"
        finally:
            feed.unsubscribe(queue)

    @app.exception_handler(AppError)
    async def app_error_handler(_: Request, error: AppError):
        return JSONResponse(status_code=error.status_code, content={"detail": error.detail})

    @app.get("/health")
    async def health():
        return {"status": "ok", "implementation": "python-fastapi"}

    @app.get("/", include_in_schema=False)
    async def dashboard():
        return FileResponse(UI_PATH, media_type="text/html", headers={"Cache-Control": "no-store", "X-Content-Type-Options": "nosniff"})

    @app.get("/api/status")
    async def status(request: Request):
        return await connection_status(request.app)

    @app.get("/api/stream")
    async def stream(request: Request):
        return StreamingResponse(stream_events(request), media_type="text/event-stream", headers={"Cache-Control": "no-cache", "Connection": "keep-alive", "X-Accel-Buffering": "no"})

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

    @app.get("/api/search/status")
    async def get_search_status(request: Request):
        return await search_status(get_db(request.app))

    @app.get("/api/telemetry")
    async def telemetry(request: Request, profile: str | None = None, hours: float = 8, min: float | None = None, max: float | None = None):
        return await telemetry_view(get_db(request.app), profile_from_query(profile), hours=hours, minimum=min, maximum=max)

    @app.get("/api/search")
    async def search(request: Request, q: str | None = None, profile: str | None = None, kind: str = "all", source: str | None = None, event_type: str | None = None, status: str | None = None, limit: int = 10):
        if not 1 <= limit <= 25:
            raise HTTPException(status_code=400, detail="limit must be between 1 and 25")
        return await run_search(get_db(request.app), profile_from_query(profile), q=q, kind=kind, source=source, event_type=event_type, status=status, limit=limit)

    @app.get("/api/exceptions")
    async def exceptions(request: Request, profile: str | None = None):
        selected = profile_from_query(profile)
        return {"queue": await exception_queue(get_db(request.app), selected)}

    @app.get("/api/exceptions/{exception_id}/similar")
    async def similar_cases(request: Request, exception_id: str, limit: int = 3):
        if not 1 <= limit <= 10:
            raise HTTPException(status_code=400, detail="limit must be between 1 and 10")
        return await find_similar(get_db(request.app), exception_id, limit=limit)

    @app.post("/api/events")
    async def post_event(request: Request, body: EventInput, profile: str | None = None):
        selected = profile_from_query(profile)
        return await ingest_event(await write_ready(request, selected), selected, body)

    @app.post("/api/telemetry/reading")
    async def post_telemetry_reading(request: Request, body: TelemetryReadingInput, profile: str | None = None):
        selected = profile_from_query(profile)
        return await add_reading(await write_ready(request, selected), selected, body.kind)

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