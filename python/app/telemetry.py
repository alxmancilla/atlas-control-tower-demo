from __future__ import annotations

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


async def telemetry_view(db, profile: Profile, *, hours: float = 8, minimum: float | None = None, maximum: float | None = None, now: datetime | None = None) -> dict:
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
    return {"enabled": True, "metric": telemetry.metric, "label": telemetry.label, "unit": telemetry.unit, "sensorId": telemetry.sensor_id, "range": {"min": low, "max": high}, "configuredRange": {"min": telemetry.minimum, "max": telemetry.maximum}, "since": since, "stats": (result.get("stats") or [{"count": 0}])[0], "recent": result.get("recent", []), "excursions": result.get("excursions", []), "queriedAt": now}


async def add_reading(db, profile: Profile, kind: str, *, now: datetime | None = None) -> dict:
    telemetry = _configured(profile)
    if kind not in {"normal", "excursion"}:
        raise BadRequest('kind must be "normal" or "excursion"')
    value = telemetry.excursion_value if kind == "excursion" else telemetry.baseline + random.uniform(-0.5, 0.5) * ((telemetry.maximum - telemetry.minimum) / 4)
    document = reading(profile, now or datetime.now(timezone.utc), value)
    await db[COLLECTIONS["telemetry"]].insert_one(document)
    return document


def _configured(profile: Profile):
    if not profile.telemetry:
        raise BadRequest(f"Profile {profile.id} does not use telemetry")
    return profile.telemetry