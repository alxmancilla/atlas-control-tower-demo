import asyncio
from datetime import datetime, timezone

from bson import ObjectId

from app.data_quality import data_quality_queue, quality_issue_types, upsert_quality_issues
from app.profiles import get_profile
from app.source_contract import assess_source_contract
from app.trace import disruption_trace


def test_quality_issue_types_cover_reliability_flags_and_duplicate_suppression():
    assert quality_issue_types({"reliability": {"flags": ["late", "out_of_order"], "duplicateCount": 2}}) == ["late", "out_of_order", "duplicate_suppressed"]
    assert quality_issue_types({"reliability": {"flags": []}, "sourceContract": {"mappingStatus": "incomplete"}}) == ["source_contract_incomplete"]
    assert quality_issue_types({"reliability": {"flags": [], "duplicateCount": 0}}) == []


def test_cross_domain_profile_sources_are_available_to_fastapi_event_validation():
    assert get_profile("retail").sources["berry_supplier"] == "api"
    assert get_profile("healthcare").sources["reagent_supplier"] == "api"
    assert get_profile("manufacturing").sources["supplier_portal"] == "edi"


def test_source_contract_evidence_separates_mapping_completeness_from_freshness():
    scenarios = (
        ("retail", "berry_supplier", {"purchaseOrder": "PO-45018", "supplierId": "SUP-BERRY-44"}),
        ("healthcare", "reagent_supplier", {"transferOrder": "TO-55021", "supplierId": "SUP-REAGENT-17"}),
        ("manufacturing", "supplier_portal", {"purchaseOrder": "PO-44190", "asnId": "ASN-30018", "supplierId": "SUP-PCL"}),
    )
    for profile_id, source, details in scenarios:
        profile = get_profile(profile_id)
        mapped = assess_source_contract(profile, {"source": source, "details": details, "reliability": {"lagMinutes": profile.source_contracts[source]["freshnessTargetMin"] + 1}})
        assert mapped["mappingStatus"] == "mapped"
        assert mapped["freshness"]["status"] == "stale"
        gap = next(sample for sample in profile.samples if sample.id == "supplier_contract_gap")
        incomplete = assess_source_contract(profile, {"source": gap.source, "details": gap.details, "reliability": {"lagMinutes": 0}})
        assert incomplete["mappingStatus"] == "incomplete"
        assert incomplete["freshness"]["status"] == "fresh"


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
    event = {"_id": event_id, "trackedEntityId": "RTL-SHP-24017", "source": "berry_supplier", "eventTime": datetime(2026, 1, 1, 8, tzinfo=timezone.utc), "receivedAt": datetime(2026, 1, 1, 12, tzinfo=timezone.utc), "reliability": {"status": "late", "flags": ["late"], "lagMinutes": 240, "duplicateCount": 0, "confidence": "medium"}, "sourceContract": {"contractId": "retail:berry_supplier:v1", "mappingStatus": "mapped"}}
    db = IssueDatabase()
    created = asyncio.run(upsert_quality_issues(db, get_profile("retail"), event))
    assert created == [{"issueKey": f"retail:{event_id}:late", "issueType": "late", "outcome": "opened"}]
    query, update, options = db.issues.calls[0]
    assert query == {"issueKey": f"retail:{event_id}:late"}
    assert update["$set"]["reliability"]["confidence"] == "medium"
    assert update["$set"]["sourceContract"]["contractId"] == "retail:berry_supplier:v1"
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


class QualityIssueCollection:
    def __init__(self, rows):
        self.rows = rows

    def find(self, query):
        assert query == {"profile": "retail", "trackedEntityId": "RTL-SHP-24017"}
        return Cursor(self.rows)


class QualityIssueQueueDatabase:
    def __init__(self, rows):
        self.collection = QualityIssueCollection(rows)

    def __getitem__(self, name):
        assert name == "data_quality_issues"
        return self.collection


def test_data_quality_queue_converts_nested_object_ids_for_json_responses():
    document_id, event_id = ObjectId(), ObjectId()
    queue = asyncio.run(data_quality_queue(QualityIssueQueueDatabase([{"_id": document_id, "eventId": event_id, "audit": [{"eventId": event_id}]}]), get_profile("retail")))
    assert queue == [{"_id": str(document_id), "eventId": str(event_id), "audit": [{"eventId": str(event_id)}]}]


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