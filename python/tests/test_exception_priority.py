from datetime import datetime, timedelta, timezone

from app.exceptions import build_queue, prioritize_exception
from app.profiles import PROFILES


NOW = datetime(2026, 11, 12, 18, 0, tzinfo=timezone.utc)
RULES = PROFILES["retail"].priority


def exception(exception_id, *, status="open", severity="high", impact=3, level=None, sla_min=120):
    return {"exceptionId": exception_id, "status": status, "severity": severity, "impact": impact, "slaDueAt": NOW + timedelta(minutes=sla_min), **({"businessImpact": {"level": level}} if level else {})}


def test_every_profile_defines_priority_weights():
    for profile in PROFILES.values():
        assert {"severityPoints", "slaBreachedPoints", "slaSoonMin", "slaSoonPoints", "impactPointsPerLevel", "acknowledgedPoints", "businessImpactPoints"} <= profile.priority.keys()


def test_priority_explains_each_contribution():
    priority = prioritize_exception(exception("EXC-1", level="critical", sla_min=-15), NOW, RULES)
    assert priority["minutesToSla"] == -15
    assert priority["score"] == 35 + 40 + 15 + 30
    assert priority["reasons"] == ["high severity +35", "SLA breached 15 min ago +40", "impact level 3 +15", "business impact critical +30"]


def test_low_severity_acknowledged_item_ranks_below_high_severity_disruption():
    queue = build_queue([
        exception("LABEL", status="acknowledged", severity="low", impact=1, level="critical", sla_min=200),
        exception("DELAY", level="critical", sla_min=90),
        exception("PICKUP", level="critical", sla_min=-30),
    ], NOW, RULES)
    assert [item["exceptionId"] for item in queue] == ["PICKUP", "DELAY", "LABEL"]


def test_resolved_items_sink_to_the_bottom_and_ties_break_on_sla():
    queue = build_queue([
        exception("DONE", status="resolved", severity="critical", sla_min=-500),
        exception("LATER", sla_min=300),
        exception("SOONER", sla_min=200),
    ], NOW, RULES)
    assert [item["exceptionId"] for item in queue] == ["SOONER", "LATER", "DONE"]
    assert queue[-1]["priority"] == {"score": 0, "reasons": ["Resolved"], "minutesToSla": None}
