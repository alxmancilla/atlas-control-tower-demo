from __future__ import annotations

from datetime import datetime, timezone

from .db import COLLECTIONS
from .profiles import PROFILES, Profile
from .telemetry import synthetic_series


def _shift(path: str, milliseconds: int) -> dict:
    return {"$cond": [{"$eq": [{"$type": path}, "date"]}, {"$add": [path, milliseconds]}, path]}


def _shift_embedded(parent: str, field: str, milliseconds: int) -> dict:
    return {"$cond": [{"$eq": [{"$type": f"${parent}"}, "object"]}, {"$mergeObjects": [f"${parent}", {field: _shift(f"${parent}.{field}", milliseconds)}]}, f"${parent}"]}


def event_shift_pipeline(milliseconds: int) -> list[dict]:
    return [{"$set": {"eventTime": _shift("$eventTime", milliseconds), "receivedAt": _shift("$receivedAt", milliseconds), "reliability": _shift_embedded("reliability", "lastDuplicateAt", milliseconds)}}]


def exception_shift_pipeline(milliseconds: int) -> list[dict]:
    audit = {"$map": {"input": "$audit", "as": "entry", "in": {"$mergeObjects": ["$$entry", {"at": _shift("$$entry.at", milliseconds)}]}}}
    return [{"$set": {
        "slaDueAt": _shift("$slaDueAt", milliseconds), "createdAt": _shift("$createdAt", milliseconds), "updatedAt": _shift("$updatedAt", milliseconds), "acknowledgedAt": _shift("$acknowledgedAt", milliseconds),
        "triggeringEvent": _shift_embedded("triggeringEvent", "eventTime", milliseconds), "resolution": _shift_embedded("resolution", "at", milliseconds),
        "audit": {"$cond": [{"$isArray": "$audit"}, audit, "$audit"]},
    }}]


def quality_issue_shift_pipeline(milliseconds: int) -> list[dict]:
    audit = {"$map": {"input": "$audit", "as": "entry", "in": {"$mergeObjects": ["$$entry", {"at": _shift("$$entry.at", milliseconds)}]}}}
    return [{"$set": {
        "eventTime": _shift("$eventTime", milliseconds), "receivedAt": _shift("$receivedAt", milliseconds), "createdAt": _shift("$createdAt", milliseconds), "updatedAt": _shift("$updatedAt", milliseconds), "acknowledgedAt": _shift("$acknowledgedAt", milliseconds),
        "resolution": _shift_embedded("resolution", "at", milliseconds), "audit": {"$cond": [{"$isArray": "$audit"}, audit, "$audit"]},
    }}]


def entity_shift_pipeline(milliseconds: int) -> list[dict]:
    milestones = {"$map": {"input": "$plannedMilestones", "as": "milestone", "in": {"$mergeObjects": ["$$milestone", {"plannedTime": _shift("$$milestone.plannedTime", milliseconds)}]}}}
    trusted_signal = {"$mergeObjects": ["$portfolio", {"lastTrustedSignal": {"$mergeObjects": ["$portfolio.lastTrustedSignal", {"at": _shift("$portfolio.lastTrustedSignal.at", milliseconds)}]}}]}
    return [{"$set": {
        "anchorAt": _shift("$anchorAt", milliseconds),
        "plannedMilestones": {"$cond": [{"$isArray": "$plannedMilestones"}, milestones, "$plannedMilestones"]},
        "businessImpact": _shift_embedded("businessImpact", "computedAt", milliseconds),
        "portfolio": {"$cond": [{"$eq": [{"$type": "$portfolio.lastTrustedSignal.at"}, "date"]}, trusted_signal, "$portfolio"]},
    }}]


def _utc(value: datetime) -> datetime:
    return value if value.tzinfo else value.replace(tzinfo=timezone.utc)


async def _reanchor_profile(db, profile: Profile, now: datetime) -> dict[str, object]:
    entities = db[COLLECTIONS["entities"]]
    entity = await entities.find_one({"profile": profile.id, "trackedEntityId": profile.entity_id}, {"anchorAt": 1, "businessImpact.level": 1})
    anchor = entity.get("anchorAt") if entity else None
    if not isinstance(anchor, datetime):
        return {"profile": profile.id, "skipped": "not seeded"}
    milliseconds = round((now - _utc(anchor)).total_seconds() * 1000)
    if abs(milliseconds) < 60_000:
        return {"profile": profile.id, "skipped": "already current", "shiftedMin": 0}
    scope = {"profile": profile.id}
    events = await db[COLLECTIONS["events"]].update_many(scope, event_shift_pipeline(milliseconds), bypass_document_validation=True)
    exceptions = await db[COLLECTIONS["exceptions"]].update_many(scope, exception_shift_pipeline(milliseconds))
    quality_issues = await db[COLLECTIONS["quality_issues"]].update_many(scope, quality_issue_shift_pipeline(milliseconds))
    await entities.update_many(scope, entity_shift_pipeline(milliseconds))
    telemetry = db[COLLECTIONS["telemetry"]]
    removed = await telemetry.delete_many({"meta.trackedEntityId": profile.entity_id})
    readings = synthetic_series(profile, now)
    inserted = 0
    if readings:
        result = await telemetry.insert_many(readings)
        inserted = len(result.inserted_ids)
    return {
        "profile": profile.id, "shiftedMin": round(milliseconds / 60_000), "events": events.modified_count,
        "exceptions": exceptions.modified_count, "qualityIssues": quality_issues.modified_count, "telemetry": {"removed": removed.deleted_count, "inserted": inserted},
        "businessImpact": ((entity.get("businessImpact") or {}).get("level")),
    }


async def reanchor_demo(db, *, now: datetime | None = None) -> list[dict[str, object]]:
    now = now or datetime.now(timezone.utc)
    return [await _reanchor_profile(db, profile, now) for profile in PROFILES.values()]