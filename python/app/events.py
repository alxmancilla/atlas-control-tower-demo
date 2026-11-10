from __future__ import annotations

from datetime import datetime, timedelta, timezone
from copy import deepcopy

from pymongo import ReturnDocument
from pymongo.errors import DuplicateKeyError

from .db import COLLECTIONS
from .errors import BadRequest
from .exceptions import open_or_update_exception
from .data_quality import upsert_quality_issues
from .profiles import Profile, Sample
from .schemas import EventInput

EVENT_ENVELOPE_SCHEMA = {
    "bsonType": "object",
    "required": ["trackedEntityId", "profile", "eventType", "eventTime", "receivedAt", "source", "sourceEventId", "idempotencyKey"],
    "properties": {
        "trackedEntityId": {"bsonType": "string", "minLength": 1},
        "profile": {"enum": ["retail", "healthcare", "manufacturing"]},
        "eventType": {"bsonType": "string", "minLength": 1},
        "eventTime": {"bsonType": "date"},
        "receivedAt": {"bsonType": "date"},
        "source": {"bsonType": "string", "minLength": 1},
        "sourceEventId": {"bsonType": "string", "minLength": 1},
        "idempotencyKey": {"bsonType": "string", "minLength": 1},
        "channel": {"enum": ["edi", "api", "wms_scan", "gps", "iot", "manual", "batch_file"]},
        "milestoneId": {"bsonType": "string"},
        "details": {"bsonType": "object"},
    },
}


def _utc(value: datetime) -> datetime:
    return value.astimezone(timezone.utc) if value.tzinfo else value.replace(tzinfo=timezone.utc)


def classify_arrival(event_time: datetime, received_at: datetime, latest: datetime | None, milestone_recorded: datetime | None, risk: dict[str, int]) -> dict:
    lag = round((_utc(received_at) - _utc(event_time)).total_seconds() / 60)
    flags = []
    if lag > risk["lateArrivalMin"]:
        flags.append("late")
    if lag < -5:
        flags.append("future_event_time")
    if latest and _utc(event_time) < _utc(latest):
        flags.append("out_of_order")
    if milestone_recorded:
        flags.append("milestone_earlier_report" if _utc(event_time) < _utc(milestone_recorded) else "milestone_already_recorded")
    confidence = "low" if any(flag in {"out_of_order", "future_event_time"} for flag in flags) else "medium" if flags else "high"
    return {"status": "out_of_order" if "out_of_order" in flags else "late" if "late" in flags else "accepted", "flags": flags, "lagMinutes": lag, "duplicateCount": 0, "confidence": confidence}


def build_event(profile: Profile, payload: EventInput, received_at: datetime) -> dict:
    if payload.source not in profile.sources:
        raise BadRequest(f'Unknown source "{payload.source}" for profile {profile.id}')
    if payload.milestone_id and payload.milestone_id not in profile.milestones:
        raise BadRequest(f'Unknown milestone "{payload.milestone_id}" for profile {profile.id}')
    if payload.exception_category and payload.exception_category not in profile.exception_categories:
        raise BadRequest(f'Unknown exception category "{payload.exception_category}" for profile {profile.id}')
    channel = payload.channel or profile.sources[payload.source]
    if channel not in {"edi", "api", "wms_scan", "gps", "iot", "manual", "batch_file"}:
        raise BadRequest(f'Unknown channel "{channel}"')
    event = {"trackedEntityId": profile.entity_id, "profile": profile.id, "eventType": payload.event_type, "eventTime": _utc(payload.event_time), "receivedAt": _utc(received_at), "source": payload.source, "channel": channel, "sourceEventId": payload.source_event_id, "idempotencyKey": f"{payload.source}:{payload.source_event_id}", "description": payload.description, "details": payload.details, "demo": {"python": True}}
    if payload.milestone_id:
        event["milestoneId"] = payload.milestone_id
    if payload.exception_category:
        event["exception"] = {"category": payload.exception_category}
    if payload.notes:
        event["notes"] = payload.notes
    return event


async def _within_transaction(db, callback):
    async with db.client.start_session() as session:
        return await session.with_transaction(callback)


async def ingest_event(db, profile: Profile, payload: EventInput, received_at: datetime | None = None) -> dict:
    base = build_event(profile, payload, received_at or datetime.now(timezone.utc))
    for attempt in range(3):
        try:
            return await _within_transaction(db, lambda session: _insert_new(db, profile, dict(base), session))
        except DuplicateKeyError as error:
            # A competing transaction may have opened this category work item first. Retrying
            # finds it and links the event; a duplicate source event uses the original event.
            if "exceptionKey" in str(error) and attempt < 2:
                continue
            if "idempotencyKey" in str(error):
                return await _within_transaction(db, lambda session: _record_duplicate(db, profile, base, session))
            raise
    raise RuntimeError("Event ingestion exhausted its transaction retry budget")


async def _insert_new(db, profile: Profile, event: dict, session) -> dict:
    events = db[COLLECTIONS["events"]]
    latest = await events.find_one({"trackedEntityId": event["trackedEntityId"], "eventTime": {"$type": "date"}}, {"eventTime": 1}, sort=[("eventTime", -1)], session=session)
    first = None
    if event.get("milestoneId"):
        first = await events.find_one({"trackedEntityId": event["trackedEntityId"], "milestoneId": event["milestoneId"], "eventTime": {"$type": "date"}}, {"eventTime": 1}, sort=[("eventTime", 1)], session=session)
    event["reliability"] = classify_arrival(event["eventTime"], event["receivedAt"], latest.get("eventTime") if latest else None, first.get("eventTime") if first else None, profile.risk)
    result = await events.insert_one(event, session=session)
    event["_id"] = result.inserted_id
    exception = await open_or_update_exception(db, profile, event, session) if event.get("exception") else None
    quality_issues = await upsert_quality_issues(db, profile, event, session)
    return {"outcome": event["reliability"]["status"], "eventId": str(result.inserted_id), "exception": exception, "qualityIssues": quality_issues, "transactional": True}


async def _record_duplicate(db, profile: Profile, event: dict, session) -> dict:
    original = await db[COLLECTIONS["events"]].find_one_and_update({"idempotencyKey": {"$eq": event["idempotencyKey"], "$type": "string"}}, {"$inc": {"reliability.duplicateCount": 1}, "$set": {"reliability.lastDuplicateAt": event["receivedAt"], "reliability.confidence": "medium"}}, return_document=ReturnDocument.AFTER, session=session)
    if not original:
        raise BadRequest("Duplicate event could not be located after its idempotency conflict")
    exception = await open_or_update_exception(db, profile, original, session) if original.get("exception") else None
    quality_issues = await upsert_quality_issues(db, profile, original, session)
    return {"outcome": "deduplicated", "eventId": str(original["_id"]), "exception": exception, "qualityIssues": quality_issues, "transactional": True}


def _fill_templates(value: object, when: datetime) -> object:
    if isinstance(value, str):
        return value.replace("{{date}}", when.strftime("%Y%m%d")).replace("{{time}}", when.strftime("%H%M")).replace("{{iso}}", _utc(when).isoformat().replace("+00:00", "Z"))
    if isinstance(value, list):
        return [_fill_templates(item, when) for item in value]
    if isinstance(value, dict):
        return {key: _fill_templates(item, when) for key, item in value.items()}
    return value


def sample_event_input(profile: Profile, sample_id: str, now: datetime | None = None) -> EventInput:
    """Build a server-owned payload from one profile catalog entry; clients supply only its ID."""
    sample = next((item for item in profile.samples if item.id == sample_id), None)
    if not sample:
        raise BadRequest(f'Unknown sample "{sample_id}" for profile {profile.id}')
    base: Sample = next((item for item in profile.samples if item.id == sample.duplicate_of), sample)
    if not base.source or not base.event_type or not base.source_event_id or not base.description:
        raise RuntimeError(f'Sample "{base.id}" is incomplete')
    event_time = _utc(now or datetime.now(timezone.utc)) + timedelta(minutes=base.event_time_offset_min)
    return EventInput(source=base.source, channel=base.channel, sourceEventId=base.source_event_id, eventType=base.event_type, eventTime=event_time, description=base.description, milestoneId=base.milestone_id, exceptionCategory=base.exception_category, notes=base.notes, details=_fill_templates(deepcopy(base.details), event_time))


async def trigger_sample(db, profile: Profile, sample_id: str, now: datetime | None = None) -> dict:
    now = _utc(now or datetime.now(timezone.utc))
    return await ingest_event(db, profile, sample_event_input(profile, sample_id, now), received_at=now)


async def timeline(db, profile: Profile) -> list[dict]:
    events = db[COLLECTIONS["events"]]
    pipeline = [
        {"$match": {"profile": profile.id, "$or": [{"trackedEntityId": profile.entity_id}, {"trackedEntityId": {"$not": {"$type": "string"}}}]}},
        {"$set": {"eventTimeType": {"$type": "$eventTime"}, "linked": {"$eq": [{"$type": "$trackedEntityId"}, "string"]}}},
        {"$sort": {"eventTime": 1, "receivedAt": 1}},
        {"$limit": 300},
    ]
    cursor = await events.aggregate(pipeline)
    documents = [document async for document in cursor]
    invalid_ids = {
        str(document["_id"])
        async for document in events.find({"_id": {"$in": [item["_id"] for item in documents]}, "$nor": [{"$jsonSchema": EVENT_ENVELOPE_SCHEMA}]}, {"_id": 1})
    }
    return [{**document, "_id": str(document["_id"]), "schemaValid": str(document["_id"]) not in invalid_ids} for document in documents]


async def reliability_summary(db, profile: Profile) -> dict[str, object]:
    pipeline = [
        {"$match": {"profile": profile.id}},
        {"$facet": {
            "byStatus": [{"$group": {"_id": {"$ifNull": ["$reliability.status", "unclassified"]}, "events": {"$sum": 1}, "duplicatesSuppressed": {"$sum": {"$ifNull": ["$reliability.duplicateCount", 0]}}}}],
            "unlinked": [{"$match": {"trackedEntityId": {"$not": {"$type": "string"}}}}, {"$count": "n"}],
            "stringTimes": [{"$match": {"eventTime": {"$type": "string"}}}, {"$count": "n"}],
        }},
    ]
    cursor = await db[COLLECTIONS["events"]].aggregate(pipeline)
    result = await anext(cursor, {"byStatus": [], "unlinked": [], "stringTimes": []})
    statuses = result["byStatus"]
    return {"byStatus": {item["_id"]: item["events"] for item in statuses}, "duplicatesSuppressed": sum(item["duplicatesSuppressed"] for item in statuses), "unlinked": next(iter(result["unlinked"]), {}).get("n", 0), "stringEventTimes": next(iter(result["stringTimes"]), {}).get("n", 0)}