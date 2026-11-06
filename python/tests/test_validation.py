import asyncio
from datetime import datetime, timezone

import pytest
from bson import ObjectId

from app.db import COLLECTIONS
from app.errors import BadRequest
from app.profiles import get_profile
from app.validation import build_invalid_event, constraint_gate, detect_stage, insert_invalid_sample, insert_valid_probe, repair_plan, stage_command, transition_plan


class Cursor:
    def __init__(self, rows):
        self.rows = iter(rows)

    def __aiter__(self):
        return self

    async def __anext__(self):
        try:
            return next(self.rows)
        except StopIteration as error:
            raise StopAsyncIteration from error


def test_stage_detection_planning_and_capability_gate_match_the_demo_contract():
    assert detect_stage({}) == "flexible"
    assert detect_stage({"validator": {"$jsonSchema": {}}, "validationLevel": "strict", "validationAction": "warn"}) == "observe"
    assert detect_stage({"validator": {"$jsonSchema": {}}, "validationLevel": "strict", "validationAction": "error"}) == "enforce"
    assert detect_stage({"validator": {"$jsonSchema": {}}, "validationLevel": "constraint"}) == "constraint"
    assert stage_command("flexible")["validationLevel"] == "off"
    assert [step["label"] for step in transition_plan({}, "constraint")] == ["Apply strict validator with action error", "Prepare collection for constraint", "Set validationLevel constraint (MongoDB scans existing documents)"]
    assert transition_plan({"validator": {"$jsonSchema": {}}, "validationLevel": "strict", "validationAction": "warn"}, "observe") == []
    with pytest.raises(BadRequest, match='Unknown validation stage "unknown"'):
        transition_plan({}, "unknown")
    assert constraint_gate("8.2.1")["supported"] is False
    assert constraint_gate("9.0.0")["supported"] is True
    assert constraint_gate(None)["supported"] is False


def test_invalid_sample_and_repair_plan_are_profile_owned_and_minimally_scoped():
    profile = get_profile("retail")
    sample = profile.invalid_samples[0]
    now = datetime(2026, 2, 3, 14, tzinfo=timezone.utc)
    invalid = build_invalid_event(profile, sample, now)
    assert "trackedEntityId" not in invalid
    assert invalid["demo"] == {"invalidSample": True, "issue": "missing_tracked_entity_id", "sampleId": sample.id}
    invalid["_id"] = ObjectId("0123456789abcdef01234567")
    plan = repair_plan(invalid, profile, now)
    assert plan["set"]["trackedEntityId"] == profile.entity_id
    assert plan["set"]["sourceEventId"].startswith("LEGACY-")
    assert plan["set"]["idempotencyKey"].startswith("legacy_edi:LEGACY-")

    unmapped = {"_id": ObjectId(), "details": {}, "eventTime": now}
    assert repair_plan(unmapped, profile, now)["error"].startswith("No reference maps")


class InsertResult:
    acknowledged = True
    inserted_id = ObjectId("0123456789abcdef01234567")


class Events:
    def __init__(self):
        self.document = None

    async def insert_one(self, document):
        self.document = document
        return InsertResult()


class ValidationDatabase:
    def __init__(self):
        self.events = Events()

    async def list_collections(self, *, filter):
        assert filter == {"name": COLLECTIONS["events"]}
        return Cursor([{"name": COLLECTIONS["events"], "options": {"validator": {"$jsonSchema": {}}, "validationLevel": "strict", "validationAction": "warn"}}])

    def __getitem__(self, name):
        assert name == COLLECTIONS["events"]
        return self.events


def test_invalid_insert_reports_observe_stage_and_valid_probe_uses_ingestion(monkeypatch):
    profile = get_profile("healthcare")
    db = ValidationDatabase()
    now = datetime(2026, 1, 1, tzinfo=timezone.utc)
    result = asyncio.run(insert_invalid_sample(db, profile, "legacy_string_time", now))
    assert result["ok"] is True
    assert result["stage"] == "observe"
    assert db.events.document["eventTime"] == "2025-12-31 22:20 UTC"

    captured = {}

    async def fake_ingest(database, selected, payload, received_at):
        captured.update(database=database, selected=selected, payload=payload, received_at=received_at)
        return {"outcome": "accepted", "eventId": "probe-id"}

    monkeypatch.setattr("app.validation.ingest_event", fake_ingest)
    valid = asyncio.run(insert_valid_probe(db, profile, now))
    assert valid == {"ok": True, "outcome": "accepted", "writeResult": {"acknowledged": True, "insertedId": "probe-id"}}
    assert captured["payload"].source in profile.sources
    assert captured["received_at"] == now