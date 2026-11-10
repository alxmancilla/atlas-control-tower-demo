from __future__ import annotations

from datetime import datetime, timedelta, timezone
import re
from uuid import uuid4

from bson import ObjectId
from pymongo import ReturnDocument

from .db import COLLECTIONS
from .errors import BadRequest, Conflict, NotFound
from .execution_outbox import create_execution_request
from .profiles import Profile


AUDIT_MAX = 100
_ACTION_STATES = {"acknowledge": {"open"}, "assign": {"open", "acknowledged"}, "note": {"open", "acknowledged", "resolved"}, "resolve": {"open", "acknowledged"}}
_SAFE_ACTION_ID = re.compile(r"^[A-Za-z0-9_-]+$")


def exception_key_for(profile_id: str, entity_id: str, category: str) -> str:
    return f"{profile_id}:{entity_id}:{category}"


def _clean(value: str | None) -> str | None:
    return value.strip()[:500] if isinstance(value, str) and value.strip() else None


def _audit(entry: dict) -> dict:
    return {"audit": {"$each": [entry], "$slice": -AUDIT_MAX}}


async def open_or_update_exception(db, profile: Profile, event: dict, session=None) -> dict:
    category = event["exception"]["category"]
    config = profile.exception_categories.get(category)
    if not config:
        raise BadRequest(f'Unknown exception category "{category}" for profile {profile.id}')
    now = event["receivedAt"]
    key = exception_key_for(profile.id, event["trackedEntityId"], category)
    ref = {"eventId": event["_id"], "source": event["source"], "sourceEventId": event["sourceEventId"], "eventTime": event["eventTime"]}
    coll = db[COLLECTIONS["exceptions"]]
    result = await coll.update_one(
        {"exceptionKey": key},
        {"$setOnInsert": {
            "exceptionId": f"EXC-{uuid4().hex[-8:].upper()}", "exceptionKey": key,
            "trackedEntityId": event["trackedEntityId"], "profile": profile.id, "category": category,
            "categoryLabel": config.label, "severity": config.severity, "impact": config.impact,
            "status": "open", "owner": None, "summary": f"{config.label}: {event['description']}",
            "triggeringEvent": ref, "relatedEventIds": [event["_id"]],
            "slaDueAt": event["eventTime"] + timedelta(minutes=config.sla_min), "createdAt": now,
            "updatedAt": now, "resolution": None,
            "audit": [{"at": now, "action": "opened", "by": f"source:{event['source']}", "note": event["description"], "eventId": event["_id"]}],
        }},
        upsert=True,
        session=session,
    )
    if result.upserted_id:
        opened = await coll.find_one({"_id": result.upserted_id}, {"exceptionId": 1}, session=session)
        return {"outcome": "opened", "exceptionKey": key, "exceptionId": opened["exceptionId"]}
    current = await coll.find_one({"exceptionKey": key}, {"status": 1, "relatedEventIds": 1, "exceptionId": 1}, session=session)
    if any(item == event["_id"] for item in current.get("relatedEventIds", [])):
        return {"outcome": "already_linked", "exceptionKey": key, "exceptionId": current["exceptionId"]}
    reopened = current["status"] == "resolved"
    await coll.update_one(
        {"exceptionKey": key, "relatedEventIds": {"$ne": event["_id"]}},
        {"$addToSet": {"relatedEventIds": event["_id"]}, "$set": {"updatedAt": now, **({"status": "open", "resolution": None} if reopened else {})}, "$push": _audit({"at": now, "action": "reopened" if reopened else "linked_event", "by": f"source:{event['source']}", "note": event["description"], "eventId": event["_id"]})},
        session=session,
    )
    return {"outcome": "reopened" if reopened else "updated", "exceptionKey": key, "exceptionId": current["exceptionId"]}


async def apply_exception_action(db, exception_id: str, payload: dict) -> dict:
    coll = db[COLLECTIONS["exceptions"]]
    current = await coll.find_one({"exceptionId": exception_id}, {"status": 1})
    if not current:
        raise NotFound(f"Exception {exception_id} not found")
    action = payload.get("action")
    if action not in _ACTION_STATES:
        raise BadRequest(f'Unknown action "{action}"')
    if current["status"] not in _ACTION_STATES[action]:
        raise Conflict(f"Cannot {action} an exception that is {current['status']}")
    owner, note, by = _clean(payload.get("owner")), _clean(payload.get("note")), _clean(payload.get("operator")) or "demo.operator"
    if action == "assign" and not owner:
        raise BadRequest("Assign requires an owner")
    if action in {"note", "resolve"} and not note:
        raise BadRequest("Resolve requires a resolution note" if action == "resolve" else "Note text is required")
    now = datetime.now(timezone.utc)
    next_status = "acknowledged" if action == "acknowledge" else "resolved" if action == "resolve" else current["status"]
    set_doc = {"status": next_status, "updatedAt": now}
    if action == "acknowledge":
        set_doc["acknowledgedAt"] = now
    if action == "assign":
        set_doc["owner"] = owner
    if action == "resolve":
        set_doc["resolution"] = {"note": note, "by": by, "at": now}
    result = await coll.find_one_and_update({"exceptionId": exception_id, "status": {"$in": list(_ACTION_STATES[action])}}, {"$set": set_doc, "$push": _audit({"at": now, "action": action, "by": by, "note": note, "owner": owner})}, return_document=ReturnDocument.AFTER)
    if not result:
        raise Conflict("The exception changed while you were working on it. Refresh and retry.")
    return _json_safe(result)


async def decide_recommended_action(db, exception_id: str, payload: dict) -> dict:
    action_id, decision = payload.get("action_id"), payload.get("decision")
    if not action_id or not _SAFE_ACTION_ID.fullmatch(action_id):
        raise BadRequest("actionId is invalid")
    if decision not in {"accepted", "declined"}:
        raise BadRequest('Decision must be "accepted" or "declined"')
    coll = db[COLLECTIONS["exceptions"]]

    async def apply(session=None):
        current = await coll.find_one({"exceptionId": exception_id}, session=session)
        if not current:
            raise NotFound(f"Exception {exception_id} not found")
        if current["status"] == "resolved":
            raise Conflict("Cannot decide an action for a resolved exception")
        action = next((item for item in current.get("recommendedActions", []) if item.get("actionId") == action_id), None)
        if not action:
            raise Conflict("This recommended action is no longer available. Refresh and retry.")
        if current.get("actionDecisions", {}).get(action_id):
            raise Conflict("This recommended action has already been decided")
        note, by = _clean(payload.get("note")), _clean(payload.get("operator")) or "demo.operator"
        if decision == "declined" and not note:
            raise BadRequest("Declining a recommendation requires a reason")
        now = datetime.now(timezone.utc)
        record = {"decision": decision, "by": by, "at": now, **({"note": note} if note else {})}
        result = await coll.find_one_and_update({"exceptionId": exception_id, "status": {"$ne": "resolved"}, "recommendedActions.actionId": action_id, f"actionDecisions.{action_id}": {"$exists": False}}, {"$set": {f"actionDecisions.{action_id}": record, "updatedAt": now}, "$push": _audit({"at": now, "action": f"recommendation_{decision}", "by": by, "note": f"{action.get('title', action_id)}{f' — {note}' if note else ''}"})}, return_document=ReturnDocument.AFTER, session=session)
        if not result:
            raise Conflict("The recommendation changed while you were working on it. Refresh and retry.")
        execution = await create_execution_request(db, profile=current["profile"], exception=current, action=action, decision=record, now=now, session=session) if decision == "accepted" else None
        return _json_safe({**result, "execution": execution} if execution else result)

    if decision != "accepted":
        return await apply()
    async with db.client.start_session() as session:
        return await session.with_transaction(lambda inner_session: apply(inner_session))


def _json_safe(value):
    if isinstance(value, ObjectId):
        return str(value)
    if isinstance(value, dict):
        return {key: _json_safe(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_json_safe(item) for item in value]
    return value


async def exception_queue(db, profile: Profile) -> list[dict]:
    cursor = db[COLLECTIONS["exceptions"]].find({"profile": profile.id, "trackedEntityId": profile.entity_id}).sort([("status", 1), ("slaDueAt", 1)])
    return [_json_safe(item) async for item in cursor]