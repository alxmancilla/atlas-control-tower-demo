import asyncio
from datetime import datetime, timedelta, timezone

from app.profiles import get_profile
from app.risk import assess_milestones
from app.services import KPI_MAX_AGE_SECONDS, _eta_revision, entity_overview, kpi_pipeline, kpi_snapshot, portfolio_overview


class AsyncCursor:
    def __init__(self, rows):
        self.rows = rows

    def __aiter__(self):
        self.iterator = iter(self.rows)
        return self

    async def __anext__(self):
        try:
            return next(self.iterator)
        except StopIteration as error:
            raise StopAsyncIteration from error


class AsyncCollection:
    async def aggregate(self, pipeline):
        assert pipeline[0] == {"$match": {"profile": "retail"}}
        return AsyncCursor([
            {"trackedEntityId": "A", "businessImpact": {"level": "critical"}, "openExceptionCount": 2},
            {"trackedEntityId": "B", "businessImpact": {"level": "none"}, "openExceptionCount": 0},
        ])


class AsyncDatabase:
    def __getitem__(self, name):
        assert name == "tracked_entities"
        return AsyncCollection()


def test_portfolio_awaits_async_pymongo_aggregate_cursor():
    result = asyncio.run(portfolio_overview(AsyncDatabase(), get_profile("retail")))
    assert result["summary"] == {"total": 2, "critical": 1, "high": 0, "watch": 0, "openExceptions": 2}


def test_milestone_assessment_accepts_pymongo_naive_utc_dates():
    now = datetime.now(timezone.utc)
    plan = [{"id": "arrived", "label": "Arrived", "plannedTime": (now - timedelta(minutes=5)).replace(tzinfo=None), "actualTime": None}]
    result = assess_milestones(plan, now, {"onTimeToleranceMin": 15, "missedGraceMin": 30, "atRiskWindowMin": 45})
    assert result["riskLevel"] == "medium"


def test_eta_revision_accepts_only_bounded_positive_numeric_values():
    assert _eta_revision({"details": {"etaRevisionMin": 120}}) == 120
    assert _eta_revision({"details": {"etaRevisionMin": 0}}) == 0
    assert _eta_revision({"details": {"etaRevisionMin": 1441}}) == 0
    assert _eta_revision({"details": {"etaRevisionMin": True}}) == 0


class OverviewCollection:
    def __init__(self, row):
        self.row = row
        self.pipeline = None

    async def aggregate(self, pipeline):
        self.pipeline = pipeline
        return AsyncCursor([self.row])


class OverviewDatabase:
    def __init__(self, row):
        self.collection = OverviewCollection(row)

    def __getitem__(self, name):
        assert name == "tracked_entities"
        return self.collection


def test_entity_overview_applies_latest_eta_revision_to_final_forecast():
    now = datetime(2026, 1, 1, 12, tzinfo=timezone.utc)
    db = OverviewDatabase({
        "trackedEntityId": "RTL-SHP-24017",
        "entityKind": "replenishment_shipment",
        "product": {}, "origin": {}, "destination": {}, "metadata": {}, "orderLines": [], "anchorAt": now,
        "plannedMilestones": [
            {"milestoneId": "departed", "label": "Departed", "sequence": 1, "source": "carrier", "plannedTime": now, "maxDwellMin": None},
            {"milestoneId": "received", "label": "Received", "sequence": 2, "source": "store_receiving", "plannedTime": now + timedelta(hours=2), "maxDwellMin": None},
        ],
        "actuals": [{"_id": "departed", "actualTime": now, "actualSource": "carrier", "reports": 1}],
        "openExceptions": [],
        "etaRevisionEvents": [{"details": {"etaRevisionMin": 120}, "source": "carrier", "eventTime": now}],
        "businessImpact": None,
    })
    result = asyncio.run(entity_overview(db, get_profile("retail"), now=now))
    final = result["assessment"]["milestones"][-1]
    assert final["projectedTime"] == now + timedelta(hours=4)
    assert any("etaRevisionMin" in str(stage) for stage in db.collection.pipeline)


class SnapshotCollection:
    def __init__(self, snapshot):
        self.snapshot = snapshot

    async def find_one(self, query):
        assert query == {"_id": "retail"}
        return self.snapshot


class SnapshotEntityCollection:
    def __init__(self):
        self.pipeline = None

    async def aggregate(self, pipeline):
        self.pipeline = pipeline
        return AsyncCursor([])


class SnapshotDatabase:
    def __init__(self, snapshot):
        self.entities = SnapshotEntityCollection()
        self.snapshots = SnapshotCollection(snapshot)

    def __getitem__(self, name):
        return self.entities if name == "tracked_entities" else self.snapshots


def test_kpi_snapshot_honors_age_and_materialization_pipeline_contract():
    now = datetime.now(timezone.utc)
    fresh_db = SnapshotDatabase({"_id": "retail", "computedAt": now})
    fresh = asyncio.run(kpi_snapshot(fresh_db, get_profile("retail"), now=now))
    assert fresh["cached"] is True
    assert fresh_db.entities.pipeline is None

    stale_db = SnapshotDatabase({"_id": "retail", "computedAt": now - timedelta(seconds=KPI_MAX_AGE_SECONDS + 1)})
    refreshed = asyncio.run(kpi_snapshot(stale_db, get_profile("retail"), now=now))
    assert refreshed["cached"] is False
    assert stale_db.entities.pipeline[-1] == {"$merge": {"into": "kpi_snapshots", "on": "_id", "whenMatched": "replace", "whenNotMatched": "insert"}}
    assert stale_db.entities.pipeline[0] == {"$match": {"trackedEntityId": "RTL-SHP-24017", "profile": "retail"}}
    assert kpi_pipeline(get_profile("retail"))[-1] == stale_db.entities.pipeline[-1]