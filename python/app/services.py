from __future__ import annotations

from datetime import datetime, timezone

from fastapi import HTTPException

from .db import COLLECTIONS
from .profiles import Profile
from .risk import assess_milestones

KPI_MAX_AGE_SECONDS = 15


async def portfolio_overview(db, profile: Profile) -> dict[str, object]:
    impact_rank = {"$switch": {"branches": [
        {"case": {"$eq": ["$businessImpact.level", "critical"]}, "then": 4},
        {"case": {"$eq": ["$businessImpact.level", "high"]}, "then": 3},
        {"case": {"$eq": ["$businessImpact.level", "watch"]}, "then": 2},
    ], "default": 1}}
    pipeline = [
        {"$match": {"profile": profile.id}},
        {
            "$lookup": {
                "from": COLLECTIONS["exceptions"],
                "let": {"entityId": "$trackedEntityId"},
                "pipeline": [
                    {"$match": {"$expr": {"$and": [
                        {"$eq": ["$trackedEntityId", "$$entityId"]},
                        {"$ne": ["$status", "resolved"]},
                    ]}}},
                    {"$sort": {"slaDueAt": 1}},
                    {"$project": {"_id": 0, "exceptionId": 1, "categoryLabel": 1, "severity": 1, "owner": 1, "slaDueAt": 1, "summary": 1}},
                ],
                "as": "openExceptions",
            }
        },
        {"$set": {"impactRank": impact_rank, "openExceptionCount": {"$size": "$openExceptions"}, "nearestException": {"$first": "$openExceptions"}}},
        {"$sort": {"impactRank": -1, "openExceptionCount": -1, "nearestException.slaDueAt": 1, "trackedEntityId": 1}},
        {"$project": {"_id": 0, "trackedEntityId": 1, "product": 1, "origin": 1, "destination": 1, "businessImpact": 1, "openExceptionCount": 1, "nearestException": 1, "state": {"$ifNull": ["$portfolio.state", "interactive_primary"]}, "lastTrustedSignal": "$portfolio.lastTrustedSignal", "interactive": {"$eq": ["$trackedEntityId", profile.entity_id]}}},
    ]
    cursor = await db[COLLECTIONS["entities"]].aggregate(pipeline)
    loads = [load async for load in cursor]
    return {"loads": loads, "summary": {"total": len(loads), "critical": sum(load.get("businessImpact", {}).get("level") == "critical" for load in loads), "high": sum(load.get("businessImpact", {}).get("level") == "high" for load in loads), "watch": sum(load.get("businessImpact", {}).get("level") == "watch" for load in loads), "openExceptions": sum(load["openExceptionCount"] for load in loads)}}


async def entity_overview(db, profile: Profile) -> dict[str, object]:
    pipeline = [
        {"$match": {"trackedEntityId": profile.entity_id, "profile": profile.id}},
        {"$lookup": {"from": COLLECTIONS["events"], "localField": "trackedEntityId", "foreignField": "trackedEntityId", "pipeline": [{"$match": {"milestoneId": {"$type": "string"}, "eventTime": {"$type": "date"}}}, {"$sort": {"eventTime": 1}}, {"$group": {"_id": "$milestoneId", "actualTime": {"$first": "$eventTime"}, "actualSource": {"$first": "$source"}, "reports": {"$sum": 1}}}], "as": "actuals"}},
        {"$lookup": {"from": COLLECTIONS["exceptions"], "localField": "trackedEntityId", "foreignField": "trackedEntityId", "pipeline": [{"$match": {"status": {"$ne": "resolved"}}}, {"$group": {"_id": "$severity", "n": {"$sum": 1}}}], "as": "openExceptions"}},
        {"$project": {"_id": 0, "trackedEntityId": 1, "entityKind": 1, "product": 1, "origin": 1, "destination": 1, "metadata": 1, "orderLines": 1, "anchorAt": 1, "plannedMilestones": 1, "actuals": 1, "openExceptions": 1, "businessImpact": 1}},
    ]
    cursor = await db[COLLECTIONS["entities"]].aggregate(pipeline)
    rows = [row async for row in cursor]
    if not rows:
        raise HTTPException(status_code=404, detail="Demo data not found for this profile. Initialize the demo database first.")
    doc = rows[0]
    actuals = {item["_id"]: item for item in doc["actuals"]}
    milestones = [{"id": planned["milestoneId"], "label": planned["label"], "sequence": planned["sequence"], "source": planned["source"], "plannedTime": planned["plannedTime"], "maxDwellMin": planned.get("maxDwellMin"), "actualTime": actuals.get(planned["milestoneId"], {}).get("actualTime"), "actualSource": actuals.get(planned["milestoneId"], {}).get("actualSource"), "reports": actuals.get(planned["milestoneId"], {}).get("reports", 0)} for planned in sorted(doc["plannedMilestones"], key=lambda item: item["sequence"])]
    latest = next((item for item in reversed(milestones) if item["actualTime"]), None)
    return {"entity": {key: doc.get(key) for key in ("trackedEntityId", "entityKind", "product", "origin", "destination", "metadata", "orderLines", "anchorAt")}, "latestReliableMilestone": latest, "openExceptionCount": sum(item["n"] for item in doc["openExceptions"]), "openExceptionsBySeverity": {item["_id"]: item["n"] for item in doc["openExceptions"]}, "businessImpact": doc.get("businessImpact"), "assessment": assess_milestones(milestones, datetime.now(timezone.utc), profile.risk), "evaluatedAt": datetime.now(timezone.utc)}


def _rate_expr(part, whole) -> dict:
    return {"$cond": [{"$gt": [whole, 0]}, {"$round": [{"$multiply": [{"$divide": [part, whole]}, 100]}, 1]}, None]}


def _count_if(condition) -> dict:
    return {"$sum": {"$cond": [condition, 1, 0]}}


def kpi_pipeline(profile: Profile) -> list[dict]:
    """Materialize one profile's control-tower KPIs, replacing its prior snapshot by `_id`."""
    late = {"$in": ["late", {"$ifNull": ["$reliability.flags", []]}]}
    out_of_order = {"$eq": ["$reliability.status", "out_of_order"]}
    open_item = {"$ne": ["$status", "resolved"]}
    duplicates = {"$sum": {"$ifNull": ["$reliability.duplicateCount", 0]}}
    tolerance_ms = profile.risk["onTimeToleranceMin"] * 60_000
    return [
        {"$match": {"trackedEntityId": profile.entity_id, "profile": profile.id}},
        {"$lookup": {"from": COLLECTIONS["events"], "localField": "trackedEntityId", "foreignField": "trackedEntityId", "pipeline": [{"$match": {"milestoneId": {"$type": "string"}, "eventTime": {"$type": "date"}}}, {"$group": {"_id": "$milestoneId", "actualTime": {"$min": "$eventTime"}}}], "as": "actuals"}},
        {"$lookup": {"from": COLLECTIONS["events"], "localField": "profile", "foreignField": "profile", "pipeline": [{"$facet": {
            "totals": [{"$group": {"_id": None, "events": {"$sum": 1}, "late": _count_if(late), "outOfOrder": _count_if(out_of_order), "duplicates": duplicates}}],
            "bySource": [{"$group": {"_id": "$source", "events": {"$sum": 1}, "late": _count_if(late), "outOfOrder": _count_if(out_of_order), "clean": _count_if({"$not": [{"$or": [late, out_of_order]}]}), "duplicates": duplicates}}, {"$sort": {"_id": 1}}],
        }}], "as": "ev"}},
        {"$lookup": {"from": COLLECTIONS["exceptions"], "localField": "profile", "foreignField": "profile", "pipeline": [{"$facet": {
            "current": [{"$match": {"trackedEntityId": profile.entity_id}}, {"$group": {"_id": None, "open": _count_if(open_item), "slaBreached": _count_if({"$and": [open_item, {"$lt": ["$slaDueAt", "$$NOW"]}]}), "oldestOpenAt": {"$min": {"$cond": [open_item, "$createdAt", None]}}}}],
            "resolved": [{"$match": {"status": "resolved", "resolution.at": {"$type": "date"}}}, {"$group": {"_id": None, "n": {"$sum": 1}, "avgMs": {"$avg": {"$subtract": ["$resolution.at", "$createdAt"]}}, "withinSla": _count_if({"$lte": ["$resolution.at", "$slaDueAt"]})}}],
            "actions": [
                {"$match": {"trackedEntityId": profile.entity_id}},
                {"$group": {
                    "_id": None,
                    "recommended": {"$sum": {"$size": {"$ifNull": ["$recommendedActions", []]}}},
                    "decisionArrays": {"$push": {"$objectToArray": {"$ifNull": ["$actionDecisions", {}]}}},
                }},
                {"$project": {"recommended": 1, "decisions": {"$reduce": {"input": "$decisionArrays", "initialValue": [], "in": {"$concatArrays": ["$$value", "$$this"]}}}}},
                {"$project": {
                    "recommended": 1,
                    "decided": {"$size": "$decisions"},
                    "accepted": {"$size": {"$filter": {"input": "$decisions", "cond": {"$eq": ["$$this.v.decision", "accepted"]}}}},
                    "declined": {"$size": {"$filter": {"input": "$decisions", "cond": {"$eq": ["$$this.v.decision", "declined"]}}}},
                }},
            ],
        }}], "as": "ex"}},
        {"$set": {
            "mm": {
                "$map": {
                    "input": "$plannedMilestones",
                    "as": "planned",
                    "in": {"$let": {
                        "vars": {"actual": {"$first": {"$filter": {
                            "input": "$actuals",
                            "cond": {"$eq": ["$$this._id", "$$planned.milestoneId"]},
                        }}}},
                        "in": {
                            "done": {"$eq": [{"$type": "$$actual.actualTime"}, "date"]},
                            "onTime": {"$and": [
                                {"$eq": [{"$type": "$$actual.actualTime"}, "date"]},
                                {"$lte": [{"$subtract": ["$$actual.actualTime", "$$planned.plannedTime"]}, tolerance_ms]},
                            ]},
                        },
                    }},
                },
            },
            "totals": {"$ifNull": [{"$first": {"$first": "$ev.totals"}}, {"events": 0, "late": 0, "outOfOrder": 0, "duplicates": 0}]},
            "current": {"$ifNull": [{"$first": {"$first": "$ex.current"}}, {"open": 0, "slaBreached": 0, "oldestOpenAt": None}]},
            "resolved": {"$ifNull": [{"$first": {"$first": "$ex.resolved"}}, {"n": 0, "avgMs": None, "withinSla": 0}]},
            "actions": {"$ifNull": [{"$first": {"$first": "$ex.actions"}}, {"recommended": 0, "decided": 0, "accepted": 0, "declined": 0}]},
        }},
        {"$project": {
            "_id": "$profile", "profile": 1, "trackedEntityId": 1,
            "milestoneAdherence": {"completed": {"$size": {"$filter": {"input": "$mm", "cond": "$$this.done"}}}, "onTime": {"$size": {"$filter": {"input": "$mm", "cond": "$$this.onTime"}}}, "onTimePct": _rate_expr({"$size": {"$filter": {"input": "$mm", "cond": "$$this.onTime"}}}, {"$size": {"$filter": {"input": "$mm", "cond": "$$this.done"}}})},
            "eventQuality": {"events": "$totals.events", "lateRatePct": _rate_expr("$totals.late", "$totals.events"), "outOfOrderRatePct": _rate_expr("$totals.outOfOrder", "$totals.events"), "duplicateRatePct": _rate_expr("$totals.duplicates", {"$add": ["$totals.events", "$totals.duplicates"]}), "invalidRecords": {"$literal": 0}},
            "sourceReliability": {"$map": {"input": {"$ifNull": [{"$first": "$ev.bySource"}, []]}, "as": "source", "in": {"source": "$$source._id", "events": "$$source.events", "late": "$$source.late", "outOfOrder": "$$source.outOfOrder", "duplicates": "$$source.duplicates", "reliablePct": _rate_expr("$$source.clean", {"$add": ["$$source.events", "$$source.duplicates"]})}}},
            "exceptions": {"open": "$current.open", "slaBreached": "$current.slaBreached", "oldestOpenAgeMin": {"$cond": ["$current.oldestOpenAt", {"$round": [{"$divide": [{"$subtract": ["$$NOW", "$current.oldestOpenAt"]}, 60_000]}, 0]}, None]}, "resolvedCount": "$resolved.n", "mttrHours": {"$cond": ["$resolved.avgMs", {"$round": [{"$divide": ["$resolved.avgMs", 3_600_000]}, 1]}, None]}, "resolvedWithinSlaPct": _rate_expr("$resolved.withinSla", "$resolved.n")},
            "inventoryAtRisk": {"level": {"$ifNull": ["$businessImpact.level", "none"]}, "linesAtRisk": {"$ifNull": ["$businessImpact.linesAtRisk", 0]}, "lineCount": {"$ifNull": ["$businessImpact.lineCount", 0]}, "unitsShort": {"$ifNull": ["$businessImpact.unitsShort", 0]}, "unit": "$businessImpact.unit"},
            "actionAdoption": {"recommended": "$actions.recommended", "decided": "$actions.decided", "accepted": "$actions.accepted", "declined": "$actions.declined", "decisionRatePct": _rate_expr("$actions.decided", "$actions.recommended"), "acceptanceRatePct": _rate_expr("$actions.accepted", "$actions.decided")},
            "computedAt": "$$NOW",
        }},
        {"$merge": {"into": COLLECTIONS["kpis"], "on": "_id", "whenMatched": "replace", "whenNotMatched": "insert"}},
    ]


async def refresh_kpis(db, profile: Profile) -> dict[str, object]:
    cursor = await db[COLLECTIONS["entities"]].aggregate(kpi_pipeline(profile))
    async for _ in cursor:
        pass
    snapshot = await db[COLLECTIONS["kpis"]].find_one({"_id": profile.id})
    if not snapshot:
        raise HTTPException(status_code=404, detail="Demo data not found for this profile. Initialize the demo database first.")
    return snapshot


async def kpi_snapshot(db, profile: Profile, *, max_age_seconds: float = KPI_MAX_AGE_SECONDS, now: datetime | None = None) -> dict[str, object]:
    snapshot = await db[COLLECTIONS["kpis"]].find_one({"_id": profile.id})
    now = now or datetime.now(timezone.utc)
    computed_at = snapshot.get("computedAt") if snapshot else None
    if snapshot and isinstance(computed_at, datetime) and (now - _utc(computed_at)).total_seconds() <= max_age_seconds:
        return {**snapshot, "cached": True}
    try:
        return {**(await refresh_kpis(db, profile)), "cached": False}
    except HTTPException as error:
        if error.status_code != 404 or snapshot:
            raise
        return await _live_kpis(db, profile)


def _utc(value: datetime) -> datetime:
    return value.astimezone(timezone.utc) if value.tzinfo else value.replace(tzinfo=timezone.utc)


def _rate(part: int, whole: int) -> float | None:
    return round(part * 100 / whole, 1) if whole else None


async def _rows(collection, query: dict, projection: dict) -> list[dict]:
    cursor = collection.find(query, projection)
    return [row async for row in cursor]


async def _live_kpis(db, profile: Profile) -> dict[str, object]:
    """Read-only KPI fallback while the Python $merge materialization is being ported."""
    entity = await db[COLLECTIONS["entities"]].find_one(
        {"trackedEntityId": profile.entity_id, "profile": profile.id},
        {"_id": 0, "trackedEntityId": 1, "plannedMilestones": 1, "businessImpact": 1},
    )
    if not entity:
        raise HTTPException(status_code=404, detail="Demo data not found for this profile. Initialize the demo database first.")
    events = await _rows(db[COLLECTIONS["events"]], {"profile": profile.id}, {"_id": 0, "trackedEntityId": 1, "milestoneId": 1, "eventTime": 1, "source": 1, "reliability": 1})
    exceptions = await _rows(db[COLLECTIONS["exceptions"]], {"profile": profile.id}, {"_id": 0, "trackedEntityId": 1, "status": 1, "slaDueAt": 1, "createdAt": 1, "resolution": 1, "recommendedActions": 1, "actionDecisions": 1})
    now = datetime.now(timezone.utc)
    late = [event for event in events if "late" in event.get("reliability", {}).get("flags", [])]
    unordered = [event for event in events if event.get("reliability", {}).get("status") == "out_of_order"]
    duplicates = sum(event.get("reliability", {}).get("duplicateCount", 0) for event in events)
    source_reliability = []
    for source in sorted({event.get("source") for event in events if event.get("source")}):
        rows = [event for event in events if event.get("source") == source]
        source_late = sum("late" in event.get("reliability", {}).get("flags", []) for event in rows)
        source_unordered = sum(event.get("reliability", {}).get("status") == "out_of_order" for event in rows)
        source_duplicates = sum(event.get("reliability", {}).get("duplicateCount", 0) for event in rows)
        clean = len(rows) - sum("late" in event.get("reliability", {}).get("flags", []) or event.get("reliability", {}).get("status") == "out_of_order" for event in rows)
        source_reliability.append({"source": source, "events": len(rows), "late": source_late, "outOfOrder": source_unordered, "duplicates": source_duplicates, "reliablePct": _rate(clean, len(rows) + source_duplicates)})
    actuals = {}
    for event in events:
        if event.get("trackedEntityId") != profile.entity_id or not event.get("milestoneId") or not isinstance(event.get("eventTime"), datetime):
            continue
        actuals[event["milestoneId"]] = min(actuals.get(event["milestoneId"], event["eventTime"]), event["eventTime"])
    completed = [milestone for milestone in entity.get("plannedMilestones", []) if milestone["milestoneId"] in actuals]
    on_time = sum((_utc(actuals[milestone["milestoneId"]]) - _utc(milestone["plannedTime"])).total_seconds() <= profile.risk["onTimeToleranceMin"] * 60 for milestone in completed)
    current = [item for item in exceptions if item.get("trackedEntityId") == profile.entity_id]
    open_items = [item for item in current if item.get("status") != "resolved"]
    resolved = [item for item in exceptions if item.get("status") == "resolved" and isinstance(item.get("resolution", {}).get("at"), datetime)]
    resolution_ms = [(_utc(item["resolution"]["at"]) - _utc(item["createdAt"])).total_seconds() * 1000 for item in resolved if isinstance(item.get("createdAt"), datetime)]
    within_sla = sum(_utc(item["resolution"]["at"]) <= _utc(item["slaDueAt"]) for item in resolved if isinstance(item.get("slaDueAt"), datetime))
    recommended = sum(len(item.get("recommendedActions") or []) for item in current)
    decisions = [decision for item in current for decision in (item.get("actionDecisions") or {}).values()]
    accepted = sum(decision.get("decision") == "accepted" for decision in decisions)
    declined = sum(decision.get("decision") == "declined" for decision in decisions)
    impact = entity.get("businessImpact") or {}
    return {
        "profile": profile.id,
        "trackedEntityId": profile.entity_id,
        "milestoneAdherence": {"completed": len(completed), "onTime": on_time, "onTimePct": _rate(on_time, len(completed))},
        "eventQuality": {"events": len(events), "lateRatePct": _rate(len(late), len(events)), "outOfOrderRatePct": _rate(len(unordered), len(events)), "duplicateRatePct": _rate(duplicates, len(events) + duplicates), "invalidRecords": 0},
        "sourceReliability": source_reliability,
        "exceptions": {"open": len(open_items), "slaBreached": sum(isinstance(item.get("slaDueAt"), datetime) and _utc(item["slaDueAt"]) < now for item in open_items), "oldestOpenAgeMin": round((now - min(_utc(item["createdAt"]) for item in open_items if isinstance(item.get("createdAt"), datetime))).total_seconds() / 60) if any(isinstance(item.get("createdAt"), datetime) for item in open_items) else None, "resolvedCount": len(resolved), "mttrHours": round(sum(resolution_ms) / len(resolution_ms) / 3_600_000, 1) if resolution_ms else None, "resolvedWithinSlaPct": _rate(within_sla, len(resolved))},
        "inventoryAtRisk": {"level": impact.get("level", "none"), "linesAtRisk": impact.get("linesAtRisk", 0), "lineCount": impact.get("lineCount", 0), "unitsShort": impact.get("unitsShort", 0), "unit": impact.get("unit")},
        "actionAdoption": {"recommended": recommended, "decided": len(decisions), "accepted": accepted, "declined": declined, "decisionRatePct": _rate(len(decisions), recommended), "acceptanceRatePct": _rate(accepted, len(decisions))},
        "computedAt": now,
        "cached": False,
    }