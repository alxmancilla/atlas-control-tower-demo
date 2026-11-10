import asyncio
from datetime import datetime, timezone

from bson import ObjectId

from app.data_quality import quality_issue_types, upsert_quality_issues
from app.profiles import get_profile
from app.trace import disruption_trace


def test_quality_issue_types_cover_reliability_flags_and_duplicate_suppression():
    assert quality_issue_types({"reliability": {"flags": ["late", "out_of_order"], "duplicateCount": 2}}) == ["late", "out_of_order", "duplicate_suppressed"]
    assert quality_issue_types({"reliability": {"flags": [], "duplicateCount": 0}}) == []


def test_cross_domain_profile_sources_are_available_to_fastapi_event_validation():
    assert get_profile("retail").sources["berry_supplier"] == "api"
    assert get_profile("healthcare").sources["reagent_supplier"] == "api"
    assert get_profile("manufacturing").sources["supplier_portal"] == "edi"


class Result:
    upserted_id = ObjectId()


class Issues:
    def __init__(self):
        self.calls = []

    async def update_one(self, query, update, **options):
        self.calls.append((query, update, options))
        return Result()


class IssueDatabase:
    def __init__(self):
        self.issues = Issues()

    def __getitem__(self, name):
        assert name == "data_quality_issues"
        return self.issues


def test_quality_issue_upsert_is_keyed_by_event_and_preserves_confidence():
    event_id = ObjectId()
    event = {"_id": event_id, "trackedEntityId": "RTL-SHP-24017", "source": "berry_supplier", "eventTime": datetime(2026, 1, 1, 8, tzinfo=timezone.utc), "receivedAt": datetime(2026, 1, 1, 12, tzinfo=timezone.utc), "reliability": {"status": "late", "flags": ["late"], "lagMinutes": 240, "duplicateCount": 0, "confidence": "medium"}}
    db = IssueDatabase()
    created = asyncio.run(upsert_quality_issues(db, get_profile("retail"), event))
    assert created == [{"issueKey": f"retail:{event_id}:late", "issueType": "late", "outcome": "opened"}]
    query, update, options = db.issues.calls[0]
    assert query == {"issueKey": f"retail:{event_id}:late"}
    assert update["$set"]["reliability"]["confidence"] == "medium"
    assert options["upsert"] is True


class Cursor:
    def __init__(self, rows):
        self.rows = iter(rows)

    def sort(self, _):
        return self

    def __aiter__(self):
        return self

    async def __anext__(self):
        try:
            return next(self.rows)
        except StopIteration as error:
            raise StopAsyncIteration from error


class TraceDatabase:
    def __init__(self, profile_id, trace_id, source):
        self.profile_id = profile_id
        self.trace_id = trace_id
        self.source = source

    def __getitem__(self, name):
        if name == "tracked_entities":
            return type("Entities", (), {"find_one": staticmethod(self.entity)})()
        assert name == "operational_events"
        return type("Events", (), {"find": staticmethod(self.events)})()

    async def entity(self, *_args, **_kwargs):
        return {"trackedEntityId": get_profile(self.profile_id).entity_id, "crossDomain": {"traceId": self.trace_id, "nodes": [{"domain": "supplier"}]}}

    def events(self, query, _projection):
        assert query["details.crossDomainTrace"] == self.trace_id
        return Cursor([{"source": self.source, "description": "Late upstream source signal"}])


def test_disruption_trace_reads_each_profile_topology_and_linked_source_signal():
    scenarios = (
        ("retail", "RTL-BERRIES-ETA-01", "berry_supplier"),
        ("healthcare", "HC-REAGENT-RELEASE-01", "reagent_supplier"),
        ("manufacturing", "MFG-HX220-ASN-01", "supplier_portal"),
    )
    for profile_id, trace_id, source in scenarios:
        result = asyncio.run(disruption_trace(TraceDatabase(profile_id, trace_id, source), get_profile(profile_id)))
        assert result["trace"]["nodes"][0]["domain"] == "supplier"
        assert result["signals"] == [{"source": source, "description": "Late upstream source signal"}]