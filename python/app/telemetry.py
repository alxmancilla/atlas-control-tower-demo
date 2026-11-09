from __future__ import annotations

import math
import random
from datetime import datetime, timedelta, timezone

from .db import COLLECTIONS
from .errors import BadRequest
from .profiles import Profile


def reading(profile: Profile, timestamp: datetime, value: float) -> dict:
    telemetry = _configured(profile)
    return {
        "ts": timestamp,
        "meta": {
            "trackedEntityId": profile.entity_id,
            "sensorId": telemetry.sensor_id,
            "profile": profile.id,
            "metric": telemetry.metric,
        },
        "value": round(value, 1),
        "unit": telemetry.unit,
    }


def synthetic_series(profile: Profile, now: datetime | None = None, *, points: int = 36, step_min: int = 10) -> list[dict]:
    telemetry = _configured(profile)
    now = now or datetime.now(timezone.utc)
    span = (telemetry.maximum - telemetry.minimum) / 6
    return [
        reading(profile, now - timedelta(minutes=(points - index) * step_min), telemetry.excursion_value if index in {20, 21} else telemetry.baseline + math.sin(index / 3) * span)
        for index in range(points)
    ]


def telemetry_pipeline(profile: Profile, *, since: datetime, minimum: float, maximum: float) -> list[dict]:
    telemetry = _configured(profile)
    out_of_range = {"$or": [{"$lt": ["$value", minimum]}, {"$gt": ["$value", maximum]}]}
    return [
        {"$match": {"meta.trackedEntityId": profile.entity_id, "meta.metric": telemetry.metric, "ts": {"$gte": since}}},
        {"$sort": {"ts": 1}},
        {"$setWindowFields": {"partitionBy": "$meta.sensorId", "sortBy": {"ts": 1}, "output": {"movingAvg": {"$avg": "$value", "window": {"documents": [-5, 0]}}}}},
        {"$set": {"outOfRange": out_of_range}},
        {"$facet": {
            "recent": [{"$sort": {"ts": -1}}, {"$limit": 48}, {"$project": {"_id": 0, "ts": 1, "value": 1, "unit": 1, "sensorId": "$meta.sensorId", "outOfRange": 1, "movingAvg": {"$round": ["$movingAvg", 2]}}}],
            "stats": [{"$group": {"_id": None, "count": {"$sum": 1}, "min": {"$min": "$value"}, "max": {"$max": "$value"}, "avg": {"$avg": "$value"}, "last": {"$last": "$value"}, "outOfRange": {"$sum": {"$cond": ["$outOfRange", 1, 0]}}}}, {"$project": {"_id": 0, "count": 1, "min": 1, "max": 1, "last": 1, "outOfRange": 1, "avg": {"$round": ["$avg", 2]}}}],
            "excursions": [{"$match": {"outOfRange": True}}, {"$sort": {"ts": -1}}, {"$limit": 20}, {"$project": {"_id": 0, "ts": 1, "value": 1, "deviation": {"$round": [{"$cond": [{"$gt": ["$value", maximum]}, {"$subtract": ["$value", maximum]}, {"$subtract": ["$value", minimum]}]}, 2]}}}],
        }},
    ]


def rollup_pipeline(profile: Profile, *, since: datetime) -> list[dict]:
    telemetry = _configured(profile)
    return [
        {"$match": {"profile": profile.id, "trackedEntityId": profile.entity_id, "sensorId": telemetry.sensor_id, "metric": telemetry.metric, "windowEnd": {"$gte": since}}},
        {"$sort": {"windowStart": -1}},
        {"$limit": 8},
        {"$project": {"_id": 0, "profile": 1, "sensorId": 1, "metric": 1, "unit": 1, "windowStart": 1, "windowEnd": 1, "count": 1, "average": 1, "minimum": 1, "maximum": 1, "outOfRangeCount": 1, "status": 1, "processedAt": 1}},
    ]


async def stream_processing_status(db, profile: Profile, *, since: datetime, enabled: bool) -> dict:
    if not enabled:
        return {"configured": False, "available": False, "collection": COLLECTIONS["telemetry_rollups"], "note": "Atlas Stream Processing is not enabled; displaying direct time-series telemetry."}
    cursor = await db[COLLECTIONS["telemetry_rollups"]].aggregate(rollup_pipeline(profile, since=since))
    rollups = [row async for row in cursor]
    if not rollups:
        return {"configured": True, "available": False, "collection": COLLECTIONS["telemetry_rollups"], "note": "Waiting for Atlas Stream Processing rollups for this telemetry window.", "recent": []}
    return {"configured": True, "available": True, "collection": COLLECTIONS["telemetry_rollups"], "latest": rollups[0], "recent": rollups}


async def telemetry_view(db, profile: Profile, *, hours: float = 8, minimum: float | None = None, maximum: float | None = None, now: datetime | None = None, stream_processing_enabled: bool = False) -> dict:
    telemetry = profile.telemetry
    if not telemetry:
        return {"enabled": False, "note": "This profile does not use telemetry; the capability is optional here."}
    low = telemetry.minimum if minimum is None else minimum
    high = telemetry.maximum if maximum is None else maximum
    if not low < high:
        raise BadRequest("Telemetry range min must be below max")
    if not 0 < hours <= 168:
        raise BadRequest("Telemetry window must be between 0 and 168 hours")
    now = now or datetime.now(timezone.utc)
    since = now - timedelta(hours=hours)
    cursor = await db[COLLECTIONS["telemetry"]].aggregate(telemetry_pipeline(profile, since=since, minimum=low, maximum=high))
    rows = [row async for row in cursor]
    result = rows[0] if rows else {}
    stream_processing = await stream_processing_status(db, profile, since=since, enabled=stream_processing_enabled)
    return {"enabled": True, "metric": telemetry.metric, "label": telemetry.label, "unit": telemetry.unit, "sensorId": telemetry.sensor_id, "range": {"min": low, "max": high}, "configuredRange": {"min": telemetry.minimum, "max": telemetry.maximum}, "since": since, "stats": (result.get("stats") or [{"count": 0}])[0], "recent": result.get("recent", []), "excursions": result.get("excursions", []), "streamProcessing": stream_processing, "queriedAt": now}


async def add_reading(db, profile: Profile, kind: str, *, now: datetime | None = None) -> dict:
    telemetry = _configured(profile)
    if kind not in {"normal", "excursion"}:
        raise BadRequest('kind must be "normal" or "excursion"')
    value = telemetry.excursion_value if kind == "excursion" else telemetry.baseline + random.uniform(-0.5, 0.5) * ((telemetry.maximum - telemetry.minimum) / 4)
    document = reading(profile, now or datetime.now(timezone.utc), value)
    await db[COLLECTIONS["telemetry"]].insert_one(document)
    return {key: value for key, value in document.items() if key != "_id"}


def _configured(profile: Profile):
    if not profile.telemetry:
        raise BadRequest(f"Profile {profile.id} does not use telemetry")
    return profile.telemetry