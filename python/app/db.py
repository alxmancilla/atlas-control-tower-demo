from __future__ import annotations

from contextlib import asynccontextmanager

from fastapi import HTTPException
from pymongo import AsyncMongoClient
from pymongo.asynchronous.database import AsyncDatabase

from .change_feed import ChangeFeed
from .config import Settings, redact


COLLECTIONS = {
    "entities": "tracked_entities",
    "events": "operational_events",
    "exceptions": "exceptions",
    "quality_issues": "data_quality_issues",
    "execution_outbox": "execution_outbox",
    "telemetry": "telemetry_readings",
    "telemetry_rollups": "telemetry_rollups",
    "positions": "inventory_positions",
    "kpis": "kpi_snapshots",
    "state": "demo_state",
}


@asynccontextmanager
async def mongo_lifespan(app):
    settings: Settings = app.state.settings
    app.state.mongo_client = None
    app.state.db = None
    app.state.change_feed = None
    if settings.mongodb_uri:
        app.state.mongo_client = AsyncMongoClient(
            settings.mongodb_uri,
            appname="atlas-control-tower-fastapi",
            # Keep the operator dashboard responsive when Atlas is resuming or
            # temporarily unavailable. Individual views can recover on refresh.
            serverSelectionTimeoutMS=5_000,
        )
        app.state.db = app.state.mongo_client[settings.db_name]
        app.state.change_feed = ChangeFeed(app.state.db).start()
    try:
        yield
    finally:
        if app.state.change_feed:
            await app.state.change_feed.stop()
        if app.state.mongo_client:
            await app.state.mongo_client.close()


def get_db(app) -> AsyncDatabase:
    if app.state.db is None:
        raise HTTPException(status_code=503, detail="MongoDB is not configured. Set MONGODB_URI to an Atlas cluster.")
    return app.state.db


async def connection_status(app) -> dict[str, object]:
    settings: Settings = app.state.settings
    db = app.state.db
    feed = app.state.change_feed
    base = {
        "implementation": "python-fastapi",
        "database": settings.db_name,
        "target": settings.target,
        "defaultProfile": settings.default_profile,
        "adminTokenRequired": bool(settings.admin_token),
        "changeStream": feed.state if feed is not None else {"state": "unavailable", "error": "MongoDB is not configured"},
    }
    if db is None:
        return {**base, "connected": False, "error": "MONGODB_URI is not configured"}
    try:
        await db.command("ping")
        return {**base, "connected": True}
    except Exception as error:
        return {**base, "connected": False, "error": redact(error)}