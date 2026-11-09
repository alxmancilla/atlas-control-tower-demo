from __future__ import annotations

import asyncio
import json
from datetime import datetime, timezone
from pathlib import Path

from fastapi import FastAPI, HTTPException, Request
from fastapi.encoders import jsonable_encoder
from fastapi.responses import FileResponse, JSONResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles

from .config import Settings, redact
from .db import connection_status, get_db, mongo_lifespan
from .errors import AppError, Conflict, ServiceUnavailable
from .events import ingest_event, reliability_summary, timeline, trigger_sample
from .exceptions import apply_exception_action, decide_recommended_action, exception_queue
from .profiles import PROFILES, get_profile
from .maintenance import maintenance_state, with_maintenance
from .readiness import demo_clock_age, initialization_status
from .reanchor import reanchor_demo
from .search import find_similar, run_search, search_status
from .schemas import DocumentIdInput, EventInput, ExceptionActionInput, RecommendationDecisionInput, SampleEventInput, TelemetryReadingInput, ValidationSampleInput, ValidationStageInput
from .security import guard_write_request
from .services import entity_overview, kpi_snapshot, portfolio_overview
from .telemetry import add_reading, telemetry_view
from .validation import insert_invalid_sample, insert_valid_probe, remove_invalid, repair_invalid, transition_stage, validation_state

UI_PATH = Path(__file__).parent / "static" / "index.html"
STATIC_PATH = UI_PATH.parent


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or Settings.from_env(require_uri=False)
    app = FastAPI(title="Atlas Control Tower Demo (FastAPI)", version="0.1.0-python", lifespan=mongo_lifespan)
    app.state.settings = settings
    app.middleware("http")(guard_write_request)
    app.mount("/static", StaticFiles(directory=STATIC_PATH), name="static")

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
        base = await connection_status(request.app)
        if not base["connected"]:
            return base
        try:
            db = get_db(request.app)
            maintenance = await maintenance_state(db)
            if maintenance["active"]:
                return {**base, "maintenance": maintenance}
            readiness, clock_age_min = await initialization_status(db), await demo_clock_age(db)
            return {**base, "maintenance": maintenance, "init": readiness, "clockAgeMin": clock_age_min}
        except Exception as error:
            return {**base, "error": f"Status check failed: {redact(error)}"}

    @app.get("/api/stream")
    async def stream(request: Request):
        return StreamingResponse(stream_events(request), media_type="text/event-stream", headers={"Cache-Control": "no-cache", "Connection": "keep-alive", "X-Accel-Buffering": "no"})

    @app.get("/api/profiles")
    async def profiles():
        return {"defaultProfile": settings.default_profile, "profiles": [profile.public() for profile in PROFILES.values()]}

    @app.get("/api/readiness")
    async def readiness(request: Request):
        db = get_db(request.app)
        return {"init": await initialization_status(db), "maintenance": await maintenance_state(db), "clockAgeMin": await demo_clock_age(db)}

    def profile_from_query(profile: str | None) -> object:
        try:
            return get_profile(profile or settings.default_profile)
        except ValueError as error:
            raise HTTPException(status_code=400, detail=str(error)) from error

    async def view_ready(request: Request) -> object:
        db = get_db(request.app)
        active = await maintenance_state(db)
        if active["active"]:
            raise ServiceUnavailable(f"Maintenance in progress ({active.get('op', 'unknown')}). Views resume automatically when it finishes.")
        return db

    async def write_ready(request: Request, profile) -> object:
        db = await view_ready(request)
        entity = await db["tracked_entities"].find_one({"profile": profile.id, "trackedEntityId": profile.entity_id}, {"_id": 1})
        if not entity:
            raise Conflict("The demo database is not initialized. Initialize the Node reference demo before submitting events.")
        return db

    @app.get("/api/overview")
    async def overview(request: Request, profile: str | None = None):
        db, selected = await view_ready(request), profile_from_query(profile)
        result, reliability = await asyncio.gather(entity_overview(db, selected), reliability_summary(db, selected))
        return {**result, "reliability": reliability}

    @app.get("/api/portfolio")
    async def portfolio(request: Request, profile: str | None = None):
        return await portfolio_overview(await view_ready(request), profile_from_query(profile))

    @app.get("/api/kpis")
    async def kpis(request: Request, profile: str | None = None, fresh: bool = False):
        return await kpi_snapshot(await view_ready(request), profile_from_query(profile), max_age_seconds=0 if fresh else 15)

    @app.get("/api/search/status")
    async def get_search_status(request: Request):
        return await search_status(get_db(request.app))

    @app.get("/api/telemetry")
    async def telemetry(request: Request, profile: str | None = None, hours: float = 8, min: float | None = None, max: float | None = None):
        return await telemetry_view(await view_ready(request), profile_from_query(profile), hours=hours, minimum=min, maximum=max)

    @app.get("/api/search")
    async def search(request: Request, q: str | None = None, profile: str | None = None, kind: str = "all", source: str | None = None, event_type: str | None = None, status: str | None = None, limit: int = 10):
        if not 1 <= limit <= 25:
            raise HTTPException(status_code=400, detail="limit must be between 1 and 25")
        return await run_search(await view_ready(request), profile_from_query(profile), q=q, kind=kind, source=source, event_type=event_type, status=status, limit=limit)

    @app.get("/api/exceptions")
    async def exceptions(request: Request, profile: str | None = None):
        selected = profile_from_query(profile)
        return {"queue": await exception_queue(await view_ready(request), selected)}

    @app.get("/api/timeline")
    async def get_timeline(request: Request, profile: str | None = None):
        return {"events": await timeline(await view_ready(request), profile_from_query(profile))}

    @app.get("/api/validation")
    async def get_validation(request: Request):
        return await validation_state(await view_ready(request))

    @app.get("/api/exceptions/{exception_id}/similar")
    async def similar_cases(request: Request, exception_id: str, limit: int = 3):
        if not 1 <= limit <= 10:
            raise HTTPException(status_code=400, detail="limit must be between 1 and 10")
        return await find_similar(await view_ready(request), exception_id, limit=limit)

    @app.post("/api/reanchor")
    async def post_reanchor(request: Request):
        db = get_db(request.app)
        readiness = await initialization_status(db)
        if not readiness["initialized"]:
            raise Conflict("The demo database is not initialized. Initialize the Node reference demo before reanchoring.")
        return {"profiles": await with_maintenance(db, "reanchor", lambda: reanchor_demo(db))}

    @app.post("/api/events")
    async def post_event(request: Request, body: EventInput, profile: str | None = None):
        selected = profile_from_query(profile)
        return await ingest_event(await write_ready(request, selected), selected, body)

    @app.post("/api/events/sample")
    async def post_sample_event(request: Request, body: SampleEventInput, profile: str | None = None):
        selected = profile_from_query(profile)
        return await trigger_sample(await write_ready(request, selected), selected, body.sample_id)

    @app.post("/api/validation/stage")
    async def post_validation_stage(request: Request, body: ValidationStageInput, profile: str | None = None):
        db = await write_ready(request, profile_from_query(profile))
        return await with_maintenance(db, "validation", lambda: transition_stage(db, body.stage))

    @app.post("/api/validation/invalid")
    async def post_invalid_sample(request: Request, body: ValidationSampleInput, profile: str | None = None):
        selected = profile_from_query(profile)
        db = await write_ready(request, selected)
        return await with_maintenance(db, "validation", lambda: insert_invalid_sample(db, selected, body.sample_id))

    @app.post("/api/validation/valid")
    async def post_valid_probe(request: Request, profile: str | None = None):
        selected = profile_from_query(profile)
        db = await write_ready(request, selected)
        return await with_maintenance(db, "validation", lambda: insert_valid_probe(db, selected))

    @app.post("/api/validation/repair")
    async def post_repair_invalid(request: Request, body: DocumentIdInput, profile: str | None = None):
        db = await write_ready(request, profile_from_query(profile))
        return await with_maintenance(db, "validation", lambda: repair_invalid(db, body.id))

    @app.post("/api/validation/remove")
    async def post_remove_invalid(request: Request, body: DocumentIdInput, profile: str | None = None):
        db = await write_ready(request, profile_from_query(profile))
        return await with_maintenance(db, "validation", lambda: remove_invalid(db, body.id))

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