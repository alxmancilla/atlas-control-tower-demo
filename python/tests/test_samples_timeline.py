import asyncio
from datetime import datetime, timezone

import pytest

from app.errors import BadRequest
from app.events import reliability_summary, sample_event_input, timeline, trigger_sample
from app.profiles import get_profile


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


def test_public_profile_catalog_exposes_only_safe_sample_metadata():
    samples = get_profile("retail").public()["samples"]
    assert samples[0] == {"id": "arrived_dc", "label": "Carrier: arrived at cross-dock", "eventType": "milestone"}
    assert samples[1] == {"id": "arrived_dc_resend", "label": "Carrier: resend arrival (duplicate)", "duplicateOf": "arrived_dc"}
    assert "details" not in samples[0]


def test_sample_payload_is_server_defined_and_fills_relative_time_templates():
    now = datetime(2026, 2, 3, 14, 5, tzinfo=timezone.utc)
    late = sample_event_input(get_profile("retail"), "late_departure_scan", now)
    assert late.source_event_id == "WMS-DEP-9001"
    assert late.event_time.isoformat() == "2026-02-03T10:55:00+00:00"
    assert late.details["raw"]["scannedAt"] == "2026-02-03T10:55:00Z"

    duplicate = sample_event_input(get_profile("retail"), "arrived_dc_resend", now)
    assert duplicate.source_event_id == "TMS-ARR-5531"
    assert duplicate.details["raw"].endswith("20260203*1405*UT~MS1*TRENTON*NJ*US~SE*6*0419~")
    with pytest.raises(BadRequest, match='Unknown sample "not-a-sample"'):
        sample_event_input(get_profile("retail"), "not-a-sample", now)


def test_trigger_sample_reuses_the_transactional_ingestion_path(monkeypatch):
    received = datetime(2026, 1, 1, tzinfo=timezone.utc)
    captured = {}

    async def fake_ingest(db, profile, payload, received_at):
        captured.update(db=db, profile=profile, payload=payload, received_at=received_at)
        return {"outcome": "accepted", "transactional": True}

    monkeypatch.setattr("app.events.ingest_event", fake_ingest)
    result = asyncio.run(trigger_sample("database", get_profile("healthcare"), "excursion_alert", received))
    assert result == {"outcome": "accepted", "transactional": True}
    assert captured["payload"].channel == "iot"
    assert captured["payload"].exception_category == "handling_excursion"
    assert captured["received_at"] == received


class Events:
    def __init__(self):
        self.pipelines = []

    async def aggregate(self, pipeline):
        self.pipelines.append(pipeline)
        if "$facet" in pipeline[-1]:
            return Cursor([{"byStatus": [{"_id": "accepted", "events": 3, "duplicatesSuppressed": 2}, {"_id": "late", "events": 1, "duplicatesSuppressed": 0}], "unlinked": [{"n": 1}], "stringTimes": [{"n": 2}]}])
        return Cursor([{"_id": 42, "profile": "retail", "trackedEntityId": "RTL-SHP-24017", "eventTimeType": "date", "linked": True}])

    def find(self, query, projection):
        assert "$jsonSchema" in query["$nor"][0]
        assert projection == {"_id": 1}
        return Cursor([])


class Database:
    def __init__(self):
        self.events = Events()

    def __getitem__(self, name):
        assert name == "operational_events"
        return self.events


def test_timeline_and_reliability_use_node_compatible_read_aggregations():
    db = Database()
    profile = get_profile("retail")
    events = asyncio.run(timeline(db, profile))
    summary = asyncio.run(reliability_summary(db, profile))
    assert events == [{"_id": "42", "profile": "retail", "trackedEntityId": profile.entity_id, "eventTimeType": "date", "linked": True, "schemaValid": True}]
    assert summary == {"byStatus": {"accepted": 3, "late": 1}, "duplicatesSuppressed": 2, "unlinked": 1, "stringEventTimes": 2}
    timeline_pipeline, summary_pipeline = db.events.pipelines
    assert timeline_pipeline[0]["$match"]["profile"] == "retail"
    assert timeline_pipeline[-1] == {"$limit": 300}
    assert "$facet" in summary_pipeline[-1]