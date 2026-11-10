from __future__ import annotations

from datetime import datetime, timezone
from uuid import uuid4

from pymongo import ReturnDocument

from .db import COLLECTIONS
from .errors import BadRequest, Conflict, NotFound
from .profiles import Profile

AUDIT_MAX = 100
ISSUE_TYPES = {
    "late": ("Late source event", "medium"),
    "out_of_order": ("Out-of-order event", "high"),
    "future_event_time": ("Future event timestamp", "high"),
    "milestone_earlier_report": ("Earlier milestone report", "medium"),
    "milestone_already_recorded": ("Repeated milestone report", "low"),
    "duplicate_suppressed": ("Duplicate source event suppressed", "low"),
    "source_contract_incomplete": ("Source contract incomplete", "high"),
}


def quality_issue_types(event: dict) -> list[str]:
    types = [flag for flag in event.get("reliability", {}).get("flags", []) if flag in ISSUE_TYPES]
    if event.get("reliability", {}).get("duplicateCount", 0) > 0:
        types.append("duplicate_suppressed")
    if event.get("sourceContract", {}).get("mappingStatus") == "incomplete":
        types.append("source_contract_incomplete")
    return list(dict.fromkeys(types))


async def upsert_quality_issues(db, profile: Profile, event: dict, session=None) -> list[dict]:
    now = event.get("receivedAt") or datetime.now(timezone.utc)
    reliability = event.get("reliability") or {}
    issues = []
    for issue_type in quality_issue_types(event):
        label, severity = ISSUE_TYPES[issue_type]
        issue_key = f"{profile.id}:{event['_id']}:{issue_type}"
        issue_reliability = {"status": reliability.get("status"), "flags": reliability.get("flags", []), "lagMinutes": reliability.get("lagMinutes"), "duplicateCount": reliability.get("duplicateCount", 0), "confidence": "medium" if issue_type == "duplicate_suppressed" else reliability.get("confidence", "medium")}
        result = await db[COLLECTIONS["quality_issues"]].update_one(
            {"issueKey": issue_key},
            {"$setOnInsert": {"issueId": f"DQ-{uuid4().hex[-8:].upper()}", "issueKey": issue_key, "profile": profile.id, "trackedEntityId": event["trackedEntityId"], "eventId": event["_id"], "source": event["source"], "issueType": issue_type, "issueLabel": label, "severity": severity, "eventTime": event["eventTime"], "receivedAt": event["receivedAt"], "createdAt": now, "audit": [{"at": now, "action": "opened", "by": f"source:{event['source']}", "note": label, "eventId": event["_id"]}]},
             "$set": {"status": "open", "resolution": None, "reliability": issue_reliability, "sourceContract": event.get("sourceContract"), "updatedAt": now, "observedCount": issue_reliability["duplicateCount"] or 1}},
            upsert=True,
            session=session,
        )
        issues.append({"issueKey": issue_key, "issueType": issue_type, "outcome": "opened" if result.upserted_id else "updated"})
    return issues


async def data_quality_queue(db, profile: Profile) -> list[dict]:
    cursor = db[COLLECTIONS["quality_issues"]].find({"profile": profile.id, "trackedEntityId": profile.entity_id}).sort([("status", 1), ("severity", -1), ("updatedAt", -1)])
    return [item async for item in cursor]


async def apply_quality_issue_action(db, issue_id: str, payload: dict) -> dict:
    action = payload.get("action")
    if action not in {"acknowledge", "resolve"}:
        raise BadRequest(f'Unknown data-quality action "{action}"')
    coll = db[COLLECTIONS["quality_issues"]]
    issue = await coll.find_one({"issueId": issue_id}, {"status": 1})
    if not issue:
        raise NotFound(f"Data-quality issue {issue_id} not found")
    if issue["status"] == "resolved":
        raise Conflict(f"Cannot {action} a data-quality issue that is resolved")
    note = (payload.get("note") or "").strip()[:500]
    if action == "resolve" and not note:
        raise BadRequest("Resolve requires a resolution note")
    by = (payload.get("operator") or "").strip()[:500] or "demo.operator"
    now = datetime.now(timezone.utc)
    status = "resolved" if action == "resolve" else "acknowledged"
    set_doc = {"status": status, "updatedAt": now}
    if action == "acknowledge":
        set_doc["acknowledgedAt"] = now
    else:
        set_doc["resolution"] = {"note": note, "by": by, "at": now}
    result = await coll.find_one_and_update({"issueId": issue_id, "status": {"$ne": "resolved"}}, {"$set": set_doc, "$push": {"audit": {"$each": [{"at": now, "action": action, "by": by, "note": note or None}], "$slice": -AUDIT_MAX}}}, return_document=ReturnDocument.AFTER)
    if not result:
        raise Conflict("The data-quality issue changed while you were working on it. Refresh and retry.")
    return result