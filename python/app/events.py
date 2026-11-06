from __future__ import annotations

from datetime import datetime, timezone

from pymongo import ReturnDocument
from pymongo.errors import DuplicateKeyError

from .db import COLLECTIONS
from .errors import BadRequest
from .exceptions import open_or_update_exception
from .profiles import Profile
from .schemas import EventInput


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
    return {"status": "out_of_order" if "out_of_order" in flags else "late" if "late" in flags else "accepted", "flags": flags, "lagMinutes": lag, "duplicateCount": 0}


def build_event(profile: Profile, payload: EventInput, received_at: datetime) -> dict:
    if payload.source not in profile.sources:
        raise BadRequest(f'Unknown source "{payload.source}" for profile {profile.id}')
    if payload.milestone_id and payload.milestone_id not in profile.milestones:
        raise BadRequest(f'Unknown milestone "{payload.milestone_id}" for profile {profile.id}')
    if payload.exception_category and payload.exception_category not in profile.exception_categories:
        raise BadRequest(f'Unknown exception category "{payload.exception_category}" for profile {profile.id}')
    event = {"trackedEntityId": profile.entity_id, "profile": profile.id, "eventType": payload.event_type, "eventTime": _utc(payload.event_time), "receivedAt": _utc(received_at), "source": payload.source, "channel": profile.sources[payload.source], "sourceEventId": payload.source_event_id, "idempotencyKey": f"{payload.source}:{payload.source_event_id}", "description": payload.description, "details": payload.details, "demo": {"python": True}}
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
    return {"outcome": event["reliability"]["status"], "eventId": str(result.inserted_id), "exception": exception, "transactional": True}


async def _record_duplicate(db, profile: Profile, event: dict, session) -> dict:
    original = await db[COLLECTIONS["events"]].find_one_and_update({"idempotencyKey": {"$eq": event["idempotencyKey"], "$type": "string"}}, {"$inc": {"reliability.duplicateCount": 1}, "$set": {"reliability.lastDuplicateAt": event["receivedAt"]}}, return_document=ReturnDocument.AFTER, session=session)
    if not original:
        raise BadRequest("Duplicate event could not be located after its idempotency conflict")
    exception = await open_or_update_exception(db, profile, original, session) if original.get("exception") else None
    return {"outcome": "deduplicated", "eventId": str(original["_id"]), "exception": exception, "transactional": True}