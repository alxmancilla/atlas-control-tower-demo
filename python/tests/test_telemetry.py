import asyncio
from dataclasses import replace
from datetime import datetime, timedelta, timezone

import pytest

from app.errors import BadRequest
from app.profiles import get_profile
from app.telemetry import add_reading, telemetry_view


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


class Collection:
    def __init__(self, rows=()):
        self.rows = rows
        self.pipeline = None
        self.inserted = None

    async def aggregate(self, pipeline):
        self.pipeline = pipeline
        return Cursor(self.rows)

    async def insert_one(self, document):
        self.inserted = document


class Database:
    def __init__(self, collection):
        self.collection = collection

    def __getitem__(self, name):
        assert name == "telemetry_readings"
        return self.collection


class RollupDatabase:
    def __init__(self, telemetry, rollups):
        self.telemetry = telemetry
        self.rollups = rollups

    def __getitem__(self, name):
        return self.telemetry if name == "telemetry_readings" else self.rollups


def test_telemetry_view_uses_time_series_window_and_facets():
    now = datetime(2026, 1, 1, 12, tzinfo=timezone.utc)
    collection = Collection([{"stats": [{"count": 2, "avg": 2.8, "outOfRange": 1}], "recent": [{"value": 6.8}], "excursions": [{"value": 6.8, "deviation": 2.8}]}])
    result = asyncio.run(telemetry_view(Database(collection), get_profile("retail"), hours=4, now=now))
    assert result["range"] == {"min": 1, "max": 4}
    assert result["stats"] == {"count": 2, "avg": 2.8, "outOfRange": 1}
    assert result["recent"] == [{"value": 6.8}]
    assert collection.pipeline[0] == {"$match": {"meta.trackedEntityId": "RTL-SHP-24017", "meta.metric": "temperature", "ts": {"$gte": now - timedelta(hours=4)}}}
    assert collection.pipeline[2]["$setWindowFields"]["output"]["movingAvg"]["window"] == {"documents": [-5, 0]}
    assert set(collection.pipeline[-1]["$facet"]) == {"recent", "stats", "excursions"}


def test_telemetry_view_handles_optional_capability_and_rejects_bad_ranges():
    profile = get_profile("retail")
    disabled = asyncio.run(telemetry_view(Database(Collection()), replace(profile, telemetry=None)))
    assert disabled["enabled"] is False
    with pytest.raises(BadRequest, match="min must be below max"):
        asyncio.run(telemetry_view(Database(Collection()), profile, minimum=4, maximum=4))
    with pytest.raises(BadRequest, match="window"):
        asyncio.run(telemetry_view(Database(Collection()), profile, hours=169))


def test_telemetry_view_exposes_opt_in_stream_processing_rollups():
    now = datetime(2026, 1, 1, 12, tzinfo=timezone.utc)
    telemetry = Collection([{"stats": [{"count": 1}], "recent": [], "excursions": []}])
    rollup = {"profile": "retail", "sensorId": "REEFER-TRL-8812", "metric": "temperature", "windowStart": now, "windowEnd": now + timedelta(minutes=1), "count": 3, "average": 2.8, "status": "normal"}
    result = asyncio.run(telemetry_view(RollupDatabase(telemetry, Collection([rollup])), get_profile("retail"), now=now, stream_processing_enabled=True))
    assert result["streamProcessing"] == {"configured": True, "available": True, "collection": "telemetry_rollups", "latest": rollup, "recent": [rollup]}
    assert result["streamProcessing"]["latest"]["status"] == "normal"


def test_telemetry_view_does_not_query_rollups_when_stream_processing_is_disabled():
    telemetry = Collection([{"stats": [{"count": 1}], "recent": [], "excursions": []}])
    result = asyncio.run(telemetry_view(Database(telemetry), get_profile("retail")))
    assert result["streamProcessing"]["configured"] is False
    assert telemetry.pipeline[-1]["$facet"]


def test_add_reading_inserts_profile_scoped_normal_and_excursion_values(monkeypatch):
    now = datetime(2026, 1, 1, 12, tzinfo=timezone.utc)
    collection = Collection()
    db = Database(collection)
    monkeypatch.setattr("app.telemetry.random.uniform", lambda *_: 0.5)
    normal = asyncio.run(add_reading(db, get_profile("retail"), "normal", now=now))
    assert normal == collection.inserted
    assert normal["value"] == 3.0
    assert normal["meta"] == {"trackedEntityId": "RTL-SHP-24017", "sensorId": "REEFER-TRL-8812", "profile": "retail", "metric": "temperature"}
    excursion = asyncio.run(add_reading(db, get_profile("retail"), "excursion", now=now))
    assert excursion["value"] == 6.8
    with pytest.raises(BadRequest, match="kind"):
        asyncio.run(add_reading(db, get_profile("retail"), "invalid", now=now))


def test_add_reading_excludes_pymongo_generated_object_id_from_the_api_payload():
    class InsertingIdCollection(Collection):
        async def insert_one(self, document):
            document["_id"] = object()
            self.inserted = document

    collection = InsertingIdCollection()
    result = asyncio.run(add_reading(Database(collection), get_profile("retail"), "normal", now=datetime(2026, 1, 1, 12, tzinfo=timezone.utc)))
    assert "_id" in collection.inserted
    assert "_id" not in result