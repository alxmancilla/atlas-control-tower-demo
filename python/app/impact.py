from __future__ import annotations

from datetime import datetime, timedelta, timezone
import math

from .db import COLLECTIONS
from .profiles import Profile
from .services import entity_overview

IMPACT_MAX_AGE_SECONDS = 5 * 60
_RANK = {"none": 0, "watch": 1, "high": 2, "critical": 3}


def _utc(value: datetime) -> datetime:
    return value.astimezone(timezone.utc) if value.tzinfo else value.replace(tzinfo=timezone.utc)


def _round1(value: float) -> float:
    return value if not math.isfinite(value) else round(value * 10) / 10


def assess_impact(assessment: dict, position: dict | None, now: datetime, config: dict, *, arrival_offset_min: int = 0, destination_name: str | None = None) -> dict:
    at = _utc(now)
    if not position:
        return {"level": "none", "reasons": ["No inventory position recorded for the destination"], "computedAt": at}
    final = assessment["milestones"][-1]
    cover_hours = position["onHand"] / position["consumptionPerHour"] if position["consumptionPerHour"] > 0 else math.inf
    run_out_at = at + timedelta(hours=cover_hours) if math.isfinite(cover_hours) else None
    base = {"onHand": position["onHand"], "unit": position["unit"], "coverHours": _round1(cover_hours), "runOutAt": run_out_at, "computedAt": at}
    if final.get("actualTime"):
        return {**base, "level": "none", "arrivalAt": _utc(final["actualTime"]), "etaHours": 0, "shortfallHours": 0, "unitsShort": 0, "reasons": [f"{final['label']} completed; supply restored"]}
    arrival_at = _utc(final.get("projectedTime") or final["plannedTime"]) + timedelta(minutes=arrival_offset_min)
    eta_hours = max(0, (arrival_at - at).total_seconds() / 3600)
    shortfall_hours = max(0, eta_hours - cover_hours)
    margin_hours = cover_hours - eta_hours
    level = "critical" if shortfall_hours >= config["criticalShortfallHours"] else "high" if shortfall_hours > 0 else "watch" if margin_hours < config["watchMarginHours"] else "none"
    destination = destination_name or "the destination"
    consequence = config["consequence"].replace("{destination}", destination)
    arrival = f"Arrival at {destination} projected in {_round1(eta_hours)} h" if destination_name else f"{final['label']} projected in {_round1(eta_hours)} h"
    reasons = [f"{_round1(cover_hours)} h of cover ({position['onHand']} {position['unit']} at {position['consumptionPerHour']}/h)", arrival]
    if shortfall_hours > 0:
        reasons.append(f"{_round1(shortfall_hours)} h short: {consequence}")
    elif level == "watch":
        reasons.append(f"Only {_round1(margin_hours)} h of margin (watch below {config['watchMarginHours']} h)")
    else:
        reasons.append(f"{_round1(margin_hours)} h of margin")
    return {**base, "level": level, "arrivalAt": arrival_at, "etaHours": _round1(eta_hours), "shortfallHours": _round1(shortfall_hours), "unitsShort": math.ceil(shortfall_hours * position["consumptionPerHour"]), "reasons": reasons}


def assess_load_impact(assessment: dict, lines: list[dict], positions: list[dict], now: datetime, config: dict) -> dict:
    at = _utc(now)
    impacts = []
    for line in lines:
        position = next((item for item in positions if item["locationId"] == line["destination"]["id"] and item["itemRef"] == line["itemRef"]), None)
        impact = assess_impact(assessment, position, at, config, arrival_offset_min=line.get("stopOffsetMin", 0), destination_name=line["destination"]["name"])
        impacts.append({"orderRef": line["orderRef"], "stop": line["stop"], "destination": line["destination"], "itemRef": line["itemRef"], "quantity": line["quantity"], **impact, "unit": impact.get("unit", line["unit"])})
    if not impacts:
        return {"level": "none", "reasons": ["No order lines on this load"], "lines": [], "lineCount": 0, "linesAtRisk": 0, "computedAt": at}
    worst = max(impacts, key=lambda item: _RANK[item["level"]])
    at_risk = [item for item in impacts if item["level"] in {"high", "critical"}]
    watch = [item for item in impacts if item["level"] == "watch"]
    units = {item["unit"] for item in impacts}
    unit = next(iter(units)) if len(units) == 1 else "units"
    units_short = sum(item.get("unitsShort", 0) for item in at_risk)
    final = assessment["milestones"][-1]
    if final.get("actualTime"):
        reasons = [f"{final['label']} completed; supply restored at all {len(impacts)} stops"]
    elif at_risk:
        reasons = [f"{len(at_risk)} of {len(impacts)} stops short: {', '.join(item['destination']['name'] for item in at_risk)}", f"{units_short} {unit} short before the load arrives", *[f"Worst stop, {worst['destination']['name']}: {reason}" for reason in worst["reasons"][1:]]]
    elif watch:
        reasons = [f"{len(watch)} of {len(impacts)} stops with thin margin: {', '.join(item['destination']['name'] for item in watch)}"]
    else:
        reasons = [f"All {len(impacts)} stops have cover until the load arrives"]
    return {"level": worst["level"], "shortfallHours": max(item.get("shortfallHours", 0) for item in impacts), "unitsShort": units_short, "unit": unit, "lineCount": len(impacts), "linesAtRisk": len(at_risk), "lines": impacts, "reasons": reasons, "computedAt": at}


def impact_summary(impact: dict) -> dict:
    return {key: impact.get(key, 0 if key in {"shortfallHours", "unitsShort", "linesAtRisk", "lineCount"} else None) for key in ("level", "shortfallHours", "unitsShort", "unit", "linesAtRisk", "lineCount", "computedAt")}


def recommend_actions(impact: dict) -> list[dict]:
    actions = []
    for line in impact.get("lines", []):
        if line["level"] not in {"high", "critical"} or line.get("shortfallHours", 0) <= 0 or not line.get("runOutAt"):
            continue
        recovery = math.ceil(line["shortfallHours"] * 60 + 15)
        actions.append({"actionId": f"stop_{line['stop']}", "type": "expedite_delivery", "level": line["level"], "title": f"Expedite delivery to {line['destination']['name']}", "target": {"orderRef": line["orderRef"], "stop": line["stop"], "destination": line["destination"], "itemRef": line["itemRef"]}, "rationale": f"{line['destination']['name']} is projected to be short {line['unitsShort']} {line['unit']} for {_round1(line['shortfallHours'])} h before arrival.", "decisionDueAt": line["runOutAt"], "expectedOutcome": f"Avoid the projected {line['unitsShort']} {line['unit']} shortfall if arrival improves by at least {recovery} min.", "generatedAt": impact["computedAt"]})
    return actions


def is_stale(impact: dict | None, now: datetime | None = None) -> bool:
    computed_at = (impact or {}).get("computedAt")
    return not isinstance(computed_at, datetime) or (_utc(now or datetime.now(timezone.utc)) - _utc(computed_at)).total_seconds() > IMPACT_MAX_AGE_SECONDS


async def recompute_impact(db, profile: Profile, now: datetime | None = None) -> dict:
    at = _utc(now or datetime.now(timezone.utc))
    overview = await entity_overview(db, profile, now=at)
    lines = overview["entity"].get("orderLines") or []
    locations, items = [line["destination"]["id"] for line in lines], [line["itemRef"] for line in lines]
    cursor = db[COLLECTIONS["positions"]].find({"profile": profile.id, "locationId": {"$in": locations}, "itemRef": {"$in": items}}, {"_id": 0, "locationId": 1, "itemRef": 1, "onHand": 1, "unit": 1, "consumptionPerHour": 1})
    positions = [item async for item in cursor]
    impact = assess_load_impact(overview["assessment"], lines, positions, at, profile.impact)
    scope = {"profile": profile.id, "trackedEntityId": profile.entity_id}
    await db[COLLECTIONS["entities"]].update_one(scope, {"$set": {"businessImpact": impact}})
    await db[COLLECTIONS["exceptions"]].update_many({**scope, "status": {"$ne": "resolved"}}, {"$set": {"businessImpact": impact_summary(impact), "recommendedActions": recommend_actions(impact)}})
    return impact