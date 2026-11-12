from __future__ import annotations

from datetime import datetime, timedelta, timezone

from bson import ObjectId

from .config import redact
from .db import COLLECTIONS
from .errors import BadRequest, NotFound
from .events import EVENT_ENVELOPE_SCHEMA, ingest_event
from .profiles import InvalidSample, Profile, get_profile
from .schemas import EventInput

STAGES = frozenset({"flexible", "observe", "enforce", "constraint"})
INVALID_FILTER = {"$nor": [{"$jsonSchema": EVENT_ENVELOPE_SCHEMA}]}


def detect_stage(options: dict) -> str:
    validator = options.get("validator") or {}
    level, action = options.get("validationLevel", "strict"), options.get("validationAction", "error")
    if not validator or level == "off":
        return "flexible"
    if level == "constraint":
        return "constraint"
    if level == "strict" and action == "warn":
        return "observe"
    if level == "strict" and action in {"error", "errorAndLog"}:
        return "enforce"
    return "custom"


def stage_command(stage: str) -> dict:
    if stage == "flexible":
        return {"collMod": COLLECTIONS["events"], "validator": {}, "validationLevel": "off", "validationAction": "error"}
    if stage == "observe":
        return {"collMod": COLLECTIONS["events"], "validator": {"$jsonSchema": EVENT_ENVELOPE_SCHEMA}, "validationLevel": "strict", "validationAction": "warn"}
    if stage == "enforce":
        return {"collMod": COLLECTIONS["events"], "validator": {"$jsonSchema": EVENT_ENVELOPE_SCHEMA}, "validationLevel": "strict", "validationAction": "error"}
    raise BadRequest(f'Unknown validation stage "{stage}"')


def transition_plan(options: dict, target: str) -> list[dict]:
    if target not in STAGES:
        raise BadRequest(f'Unknown validation stage "{target}"')
    current = detect_stage(options)
    if current == target:
        return []
    steps: list[dict] = []
    if current == "constraint":
        steps.append({"label": "Leave constraint: downgrade to strict", "command": {"collMod": COLLECTIONS["events"], "validationLevel": "strict", "validationAction": "error"}})
    if target == "constraint":
        if current != "enforce":
            steps.append({"label": "Apply strict validator with action error", "command": stage_command("enforce")})
        steps.extend([
            {"label": "Prepare collection for constraint", "command": {"collMod": COLLECTIONS["events"], "prepareConstraintValidationLevel": True}},
            {"label": "Set validationLevel constraint (MongoDB scans existing documents)", "command": {"collMod": COLLECTIONS["events"], "validationLevel": "constraint", "validationAction": "error"}},
        ])
        return steps
    if current in {"constraint", "enforce"}:
        steps.append({"label": "Clear constraint preparation flag if set", "command": {"collMod": COLLECTIONS["events"], "prepareConstraintValidationLevel": False}, "optional": True})
    steps.append({"label": f"Apply {target} stage", "command": stage_command(target)})
    return steps


def constraint_gate(version: object) -> dict[str, object]:
    try:
        major = int(str(version).split(".", 1)[0])
    except (TypeError, ValueError):
        return {"supported": False, "reason": "Server version could not be determined, so the constraint stage stays disabled."}
    if major < 9:
        return {"supported": False, "reason": f'Connected server reports MongoDB {version}. validationLevel "constraint" was added in MongoDB 9.0, so this stage is unavailable. Observe and Enforce still work. Use a cluster running 9.0 or later to try it.'}
    return {"supported": True, "reason": f'MongoDB {version} supports validationLevel "constraint".'}


async def event_collection_options(db) -> dict:
    cursor = await db.list_collections(filter={"name": COLLECTIONS["events"]})
    info = await anext(cursor, None)
    return (info or {}).get("options") or {}


async def validation_gate(db) -> dict[str, object]:
    try:
        return constraint_gate((await db.command({"buildInfo": 1})).get("version"))
    except Exception:
        return constraint_gate(None)


def _describe(command: dict) -> dict:
    if command.get("validator"):
        return {**command, "validator": {"$jsonSchema": "<operational_events schema>"}}
    return command


def _error(error: Exception) -> dict[str, object]:
    return {"name": error.__class__.__name__, "code": getattr(error, "code", None), "codeName": getattr(error, "code_name", None), "message": redact(error)}


async def find_invalid_events(db, limit: int = 50) -> dict[str, object]:
    events = db[COLLECTIONS["events"]]
    cursor = events.find(INVALID_FILTER).limit(limit)
    documents = [{**document, "_id": str(document["_id"])} async for document in cursor]
    return {"count": await events.count_documents(INVALID_FILTER), "docs": documents}


async def validation_state(db) -> dict[str, object]:
    options, persisted, invalid, gate = await event_collection_options(db), await db[COLLECTIONS["state"]].find_one({"_id": "validation"}), await find_invalid_events(db), await validation_gate(db)
    return {
        "stage": detect_stage(options), "persistedStage": (persisted or {}).get("stage"), "history": (persisted or {}).get("history", []),
        "collectionOptions": {"validationLevel": options.get("validationLevel"), "validationAction": options.get("validationAction"), "hasValidator": bool(options.get("validator"))},
        "constraintGate": gate, "schema": EVENT_ENVELOPE_SCHEMA, "invalidQuery": {"$nor": [{"$jsonSchema": "<operational_events schema>"}]}, "invalid": invalid,
    }


async def transition_stage(db, target: str) -> dict[str, object]:
    options, gate = await event_collection_options(db), await validation_gate(db)
    if target == "constraint" and not gate["supported"]:
        return {"ok": False, "target": target, "stage": detect_stage(options), "steps": [], "error": {"message": gate["reason"]}}
    results, failed = [], None
    for step in transition_plan(options, target):
        try:
            response = await db.command(step["command"])
            results.append({"label": step["label"], "command": _describe(step["command"]), "ok": True, "response": {"ok": response.get("ok")}})
        except Exception as error:
            result = {"label": step["label"], "command": _describe(step["command"]), "ok": False, "optional": bool(step.get("optional")), "error": _error(error)}
            results.append(result)
            if not step.get("optional"):
                failed = result
                break
    stage, now = detect_stage(await event_collection_options(db)), datetime.now(timezone.utc)
    await db[COLLECTIONS["state"]].update_one({"_id": "validation"}, {"$set": {"stage": stage, "updatedAt": now}, "$push": {"history": {"$each": [{"at": now, "target": target, "ok": not failed and stage == target, "stage": stage}], "$slice": -20}}}, upsert=True)
    result = {"ok": not failed and stage == target, "target": target, "stage": stage, "steps": results, "error": failed["error"] if failed else None}
    if failed and target == "constraint":
        result["violatingDocuments"] = (await find_invalid_events(db))["docs"]
    return result


def _legacy_time(value: datetime) -> str:
    return value.astimezone(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")


def build_invalid_event(profile: Profile, sample: InvalidSample, now: datetime) -> dict:
    event_time = now + timedelta(minutes=sample.event_time_offset_min)
    document = {"profile": profile.id, "eventType": sample.event_type, "eventTime": _legacy_time(event_time) if sample.event_time_string else event_time, "receivedAt": now, "source": sample.source, "description": sample.description, "details": sample.details, "demo": {"invalidSample": True, "issue": sample.issue, "sampleId": sample.id}}
    if sample.tracked_entity_id:
        document["trackedEntityId"] = sample.tracked_entity_id
    if sample.milestone_id:
        document["milestoneId"] = sample.milestone_id
    return document


async def insert_invalid_sample(db, profile: Profile, sample_id: str, now: datetime | None = None) -> dict[str, object]:
    sample = next((item for item in profile.invalid_samples if item.id == sample_id), None)
    if not sample:
        raise BadRequest(f'Unknown invalid sample "{sample_id}"')
    stage = detect_stage(await event_collection_options(db))
    try:
        inserted = await db[COLLECTIONS["events"]].insert_one(build_invalid_event(profile, sample, now or datetime.now(timezone.utc)))
        note = 'MongoDB accepted the write. With validationAction "warn" the server records a warning; the invalid-documents query surfaces the record.' if stage == "observe" else "MongoDB accepted the write because no validator is enforced at this stage."
        return {"ok": True, "stage": stage, "writeResult": {"acknowledged": inserted.acknowledged, "insertedId": str(inserted.inserted_id)}, "note": note}
    except Exception as error:
        return {"ok": False, "stage": stage, "error": _error(error)}


async def insert_valid_probe(db, profile: Profile, now: datetime | None = None) -> dict[str, object]:
    now = now or datetime.now(timezone.utc)
    payload = EventInput(source=next(iter(profile.sources)), sourceEventId=f"VAL-{int(now.timestamp() * 1000)}", eventType="status_note", eventTime=now, description="Validation check: well-formed status note", details={"probe": True})
    try:
        result = await ingest_event(db, profile, payload, received_at=now)
        return {"ok": True, "outcome": result["outcome"], "writeResult": {"acknowledged": True, "insertedId": result["eventId"]}}
    except Exception as error:
        return {"ok": False, "error": _error(error)}


def _object_id(value: str) -> ObjectId:
    if not ObjectId.is_valid(value):
        raise BadRequest("Invalid document id")
    return ObjectId(value)


def repair_plan(document: dict, profile: Profile, now: datetime) -> dict:
    updates: dict[str, object] = {}
    if not isinstance(document.get("trackedEntityId"), str) or not document["trackedEntityId"]:
        if document.get("details", {}).get("legacyRef") in profile.legacy_references:
            updates["trackedEntityId"] = profile.entity_id
        else:
            return {"error": "No reference maps this event to a tracked entity. Remove it instead."}
    if not isinstance(document.get("eventTime"), datetime):
        try:
            updates["eventTime"] = datetime.strptime(str(document.get("eventTime")), "%Y-%m-%d %H:%M UTC").replace(tzinfo=timezone.utc)
        except ValueError:
            return {"error": f'eventTime "{document.get("eventTime")}" cannot be parsed. Remove it instead.'}
    if not isinstance(document.get("receivedAt"), datetime):
        updates["receivedAt"] = now
    source_event_id = document.get("sourceEventId")
    if not isinstance(source_event_id, str):
        source_event_id = f"LEGACY-{document['_id']}"
        updates["sourceEventId"] = source_event_id
    if not isinstance(document.get("idempotencyKey"), str):
        updates["idempotencyKey"] = f"{document.get('source')}:{source_event_id}"
    updates["demo.repairedAt"] = now
    return {"set": updates}


async def repair_invalid(db, document_id: str) -> dict[str, object]:
    events, object_id = db[COLLECTIONS["events"]], _object_id(document_id)
    document = await events.find_one({"_id": object_id, "demo.invalidSample": True})
    if not document:
        raise NotFound("Only seeded or sample invalid demo records can be repaired here")
    plan = repair_plan(document, get_profile(document["profile"]), datetime.now(timezone.utc))
    if plan.get("error"):
        return {"ok": False, "error": {"message": plan["error"]}}
    try:
        result = await events.update_one({"_id": object_id}, {"$set": plan["set"]})
        return {"ok": True, "modified": result.modified_count, "applied": list(plan["set"])}
    except Exception as error:
        return {"ok": False, "error": _error(error)}


async def remove_invalid(db, document_id: str) -> dict[str, object]:
    result = await db[COLLECTIONS["events"]].delete_one({"_id": _object_id(document_id), "demo.invalidSample": True})
    if not result.deleted_count:
        raise NotFound("Only seeded or sample invalid demo records can be removed here")
    return {"ok": True, "deleted": result.deleted_count}