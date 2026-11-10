from __future__ import annotations

from datetime import datetime, timezone

from .db import COLLECTIONS
from .profiles import PROFILES


INDEXES = {
    "entities": {"trackedEntityId_unique", "profile"},
    "events": {"entity_eventTime", "entity_milestone_eventTime", "idempotencyKey_unique", "profile_receivedAt"},
    "exceptions": {"exceptionId_unique", "exceptionKey_unique", "profile_status_sla", "entity_status"},
    "quality_issues": {"issueKey_unique", "profile_entity_status_priority"},
    "positions": {"location_item_unique"},
    "telemetry": {"meta_entity_metric_ts"},
    "telemetry_rollups": {"profile_sensor_metric_window_unique"},
}


async def initialization_status(db) -> dict[str, object]:
    cursor = await db.list_collections()
    collections = {item["name"]: item for item in [row async for row in cursor]}
    missing = [name for name in COLLECTIONS.values() if name not in collections]
    if missing:
        return {"initialized": False, "missing": missing, "problems": [f"Missing collections: {', '.join(missing)}"]}

    problems: list[str] = []
    if collections[COLLECTIONS["telemetry"]].get("type") != "timeseries":
        problems.append(f"{COLLECTIONS['telemetry']} is not a time-series collection; reset the demo database")
    for key, expected in INDEXES.items():
        index_cursor = await db[COLLECTIONS[key]].list_indexes()
        actual = {item["name"] async for item in index_cursor}
        absent = sorted(expected - actual)
        if absent:
            problems.append(f"Missing indexes on {COLLECTIONS[key]}: {', '.join(absent)}")

    async def profile_ids(collection: str, match: dict | None = None) -> set[str]:
        pipeline = ([{"$match": match}] if match else []) + [{"$group": {"_id": "$profile"}}]
        grouped = await db[collection].aggregate(pipeline)
        return {row["_id"] async for row in grouped if row.get("_id")}

    entities, seeded, positions = await profile_ids(COLLECTIONS["entities"]), await profile_ids(COLLECTIONS["events"], {"demo.seeded": True}), await profile_ids(COLLECTIONS["positions"])
    for profile_id in PROFILES:
        if profile_id not in entities:
            problems.append(f"Profile {profile_id} has no tracked entity")
        elif profile_id not in seeded:
            problems.append(f"Profile {profile_id} has no seed events")
        if profile_id not in positions:
            problems.append(f"Profile {profile_id} has no inventory position")
    return {"initialized": not problems, "missing": [], "problems": problems, "profiles": len(entities)}


async def demo_clock_age(db, *, now: datetime | None = None) -> int | None:
    now = now or datetime.now(timezone.utc)
    entity = await db[COLLECTIONS["entities"]].find_one({}, {"anchorAt": 1}, sort=[("anchorAt", 1)])
    anchor = entity.get("anchorAt") if entity else None
    if not isinstance(anchor, datetime):
        return None
    anchor = anchor if anchor.tzinfo else anchor.replace(tzinfo=timezone.utc)
    return round((now - anchor).total_seconds() / 60)