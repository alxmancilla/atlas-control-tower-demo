from __future__ import annotations

from datetime import datetime, timezone
from uuid import uuid4

from pymongo import ReturnDocument

from .db import COLLECTIONS
from .errors import BadRequest, Conflict, NotFound

AUDIT_MAX = 100
_TRANSITIONS = {
    "submit": ({"pending"}, "submitted"),
    "acknowledge": ({"submitted"}, "acknowledged"),
    "fail": ({"submitted"}, "failed"),
    "reconcile": ({"acknowledged", "failed"}, "reconciled"),
}


def _clean(value: str | None) -> str | None:
    return value.strip()[:500] if isinstance(value, str) and value.strip() else None


def execution_key_for(profile: str, exception_id: str, action_id: str) -> str:
    return f"{profile}:{exception_id}:{action_id}"


def check_execution_action(status: str, action: str | None, note: str | None) -> tuple[str, str | None]:
    transition = _TRANSITIONS.get(action or "")
    if not transition:
        raise BadRequest(f'Unknown execution action "{action}"')
    allowed, next_status = transition
    if status not in allowed:
        raise Conflict(f"Cannot {action} an execution request that is {status}")
    cleaned = _clean(note)
    if action in {"fail", "reconcile"} and not cleaned:
        raise BadRequest(f"{'Failure' if action == 'fail' else 'Reconciliation'} requires a note")
    return next_status, cleaned


def build_execution_request(*, profile: str, exception: dict, action: dict, decision: dict, now: datetime | None = None) -> dict:
    now = now or datetime.now(timezone.utc)
    action_id = action["actionId"]
    return {
        "executionId": f"OUT-{uuid4().hex[-8:].upper()}",
        "executionKey": execution_key_for(profile, exception["exceptionId"], action_id),
        "profile": profile,
        "trackedEntityId": exception["trackedEntityId"],
        "exceptionId": exception["exceptionId"],
        "actionId": action_id,
        "status": "pending",
        "connector": {"type": "simulated", "label": "Simulation only — no external system write", "externalWrite": False},
        "handoff": {"recommendation": {"actionId": action_id, "title": action.get("title", action_id), "rationale": action.get("rationale"), "expectedOutcome": action.get("expectedOutcome")}, "acceptedDecision": decision},
        "submissionCount": 0,
        "createdAt": now,
        "updatedAt": now,
        "audit": [{"at": now, "action": "created", "by": decision["by"], "note": "Accepted recommendation queued for simulated handoff; no external system was called."}],
    }


async def create_execution_request(db, *, profile: str, exception: dict, action: dict, decision: dict, now: datetime, session=None) -> dict:
    request = build_execution_request(profile=profile, exception=exception, action=action, decision=decision, now=now)
    await db[COLLECTIONS["execution_outbox"]].insert_one(request, session=session)
    return request


async def execution_queue(db, profile) -> list[dict]:
    cursor = db[COLLECTIONS["execution_outbox"]].find({"profile": profile.id, "trackedEntityId": profile.entity_id}).sort([("updatedAt", -1)])
    return [item async for item in cursor]


async def apply_execution_action(db, profile, execution_id: str, payload: dict) -> dict:
    coll = db[COLLECTIONS["execution_outbox"]]
    current = await coll.find_one({"executionId": execution_id, "profile": profile.id})
    if not current:
        raise NotFound(f"Execution request {execution_id} not found")
    action = payload.get("action")
    next_status, note = check_execution_action(current["status"], action, payload.get("note"))
    now = datetime.now(timezone.utc)
    by = _clean(payload.get("operator")) or "demo.operator"
    set_doc = {"status": next_status, "updatedAt": now}
    if action == "submit":
        set_doc["submissionCount"] = current.get("submissionCount", 0) + 1
    result = await coll.find_one_and_update(
        {"executionId": execution_id, "profile": profile.id, "status": {"$in": list(_TRANSITIONS[action][0])}},
        {"$set": set_doc, "$push": {"audit": {"$each": [{"at": now, "action": action, "by": by, "note": note or "Simulated lifecycle transition; no external system was called."}], "$slice": -AUDIT_MAX}}},
        return_document=ReturnDocument.AFTER,
    )
    if not result:
        raise Conflict("The execution request changed while you were working on it. Refresh and retry.")
    return result