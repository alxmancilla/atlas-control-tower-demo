from __future__ import annotations

from datetime import datetime, timedelta, timezone


def _minutes_between(later: datetime, earlier: datetime) -> int:
    return round((_as_utc(later) - _as_utc(earlier)).total_seconds() / 60)


def _as_utc(value: datetime) -> datetime:
    return value.astimezone(timezone.utc) if value.tzinfo else value.replace(tzinfo=timezone.utc)


def assess_milestones(plan: list[dict], now: datetime, risk: dict[str, int]) -> dict[str, object]:
    now = _as_utc(now)
    completed = [index for index, milestone in enumerate(plan) if milestone.get("actualTime")]
    last_completed_index = completed[-1] if completed else -1
    carry_late_min, previous_actual, previous_label, dwell_checked = 0, None, None, False
    milestones = []
    for index, milestone in enumerate(plan):
        result = {**milestone, "varianceMin": None, "projectedTime": None, "status": "pending", "reasons": []}
        planned = _as_utc(milestone["plannedTime"])
        actual = _as_utc(milestone["actualTime"]) if milestone.get("actualTime") else None
        result["plannedTime"] = planned
        result["actualTime"] = actual
        if actual:
            variance = _minutes_between(actual, planned)
            result["varianceMin"] = variance
            if variance > risk["onTimeToleranceMin"]:
                result.update(status="completed_late", reasons=[f"Completed {variance} min after plan (tolerance {risk['onTimeToleranceMin']} min)"])
            else:
                result.update(status="completed", reasons=[f"Completed {-variance} min ahead of plan" if variance < 0 else f"Completed within tolerance (+{variance} min)"])
            carry_late_min, previous_actual, previous_label = max(0, variance), actual, milestone["label"]
            milestones.append(result)
            continue
        overdue = _minutes_between(now, planned)
        result["projectedTime"] = planned + timedelta(minutes=carry_late_min)
        if index < last_completed_index:
            result.update(status="missed", reasons=["A later milestone is complete but no event was received for this one"])
        elif overdue > risk["missedGraceMin"]:
            result.update(status="missed", reasons=[f"No event {overdue} min after planned time (grace {risk['missedGraceMin']} min)"])
        else:
            if overdue >= 0:
                result.update(status="at_risk", reasons=[f"Planned time passed {overdue} min ago; inside {risk['missedGraceMin']} min grace"])
            elif -overdue <= risk["atRiskWindowMin"]:
                result.update(status="at_risk", reasons=[f"Due in {-overdue} min and no event received yet"])
            if carry_late_min > risk["onTimeToleranceMin"]:
                result["status"] = "at_risk"
                result["reasons"].append(f"Projected {carry_late_min} min late: upstream lateness carried forward")
        if not dwell_checked and previous_actual and milestone.get("maxDwellMin"):
            dwell_checked = True
            dwell = _minutes_between(now, previous_actual)
            if dwell > milestone["maxDwellMin"]:
                if result["status"] == "pending":
                    result["status"] = "at_risk"
                result["reasons"].append(f'Dwell since "{previous_label}" is {dwell} min (limit {milestone["maxDwellMin"]} min)')
        if overdue > 0:
            carry_late_min = max(carry_late_min, overdue)
        if result["status"] == "pending":
            result["reasons"].append("On plan")
        milestones.append(result)
    current = milestones[last_completed_index] if last_completed_index >= 0 else None
    next_milestone = next((item for index, item in enumerate(milestones) if not item.get("actualTime") and index > last_completed_index), None)
    final = milestones[-1]
    final_late = max(0, final["varianceMin"] or 0) if final.get("actualTime") else max(0, _minutes_between(final["projectedTime"], final["plannedTime"]))
    missed = sum(item["status"] == "missed" for item in milestones)
    at_risk = sum(item["status"] == "at_risk" for item in milestones)
    return {
        "milestones": milestones,
        "currentStatus": {"milestoneId": current["id"], "label": current["label"], "actualTime": current["actualTime"]} if current else None,
        "nextMilestone": {"milestoneId": next_milestone["id"], "label": next_milestone["label"], "plannedTime": next_milestone["plannedTime"], "status": next_milestone["status"]} if next_milestone else None,
        "completedCount": sum(bool(item.get("actualTime")) for item in milestones),
        "missedCount": missed,
        "atRiskCount": at_risk,
        "projectedFinalLateMin": final_late,
        "riskLevel": "high" if missed else "medium" if at_risk or final_late > risk["onTimeToleranceMin"] else "low",
    }