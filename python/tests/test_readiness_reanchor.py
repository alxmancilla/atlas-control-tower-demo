import asyncio
from datetime import datetime, timedelta, timezone

import pytest
from pymongo.errors import DuplicateKeyError

from app.db import COLLECTIONS
from app.errors import Conflict
from app.maintenance import maintenance_state, with_maintenance
from app.readiness import INDEXES, demo_clock_age, initialization_status
from app.reanchor import event_shift_pipeline, reanchor_demo


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


class Result:
    def __init__(self, *, modified_count=0, deleted_count=0, inserted_ids=()):
        self.modified_count = modified_count
        self.deleted_count = deleted_count
        self.inserted_ids = list(inserted_ids)


class ReadyCollection:
    def __init__(self, name, profiles):
        self.name = name
        self.profiles = profiles

    async def list_indexes(self):
        key = next(key for key, value in COLLECTIONS.items() if value == self.name)
        return Cursor({"name": name} for name in INDEXES.get(key, set()))

    async def aggregate(self, _):
        return Cursor({"_id": profile_id} for profile_id in self.profiles)

    async def find_one(self, *_args, **_kwargs):
        return {"anchorAt": datetime(2026, 1, 1, 10, tzinfo=timezone.utc)}


class ReadyDatabase:
    def __init__(self):
        profiles = {"retail", "healthcare", "manufacturing"}
        self.collections = {name: ReadyCollection(name, profiles) for name in COLLECTIONS.values()}

    async def list_collections(self):
        return Cursor({"name": name, "type": "timeseries" if name == COLLECTIONS["telemetry"] else "collection"} for name in COLLECTIONS.values())

    def __getitem__(self, name):
        return self.collections[name]


def test_readiness_checks_all_collections_indexes_profiles_and_clock_age():
    db = ReadyDatabase()
    result = asyncio.run(initialization_status(db))
    assert result == {"initialized": True, "missing": [], "problems": [], "profiles": 3}
    assert asyncio.run(demo_clock_age(db, now=datetime(2026, 1, 1, 12, tzinfo=timezone.utc))) == 120


class StateCollection:
    def __init__(self):
        self.document = None

    async def update_one(self, query, update, *, upsert=False):
        if upsert:
            if self.document and self.document.get("active"):
                raise DuplicateKeyError("maintenance lock held")
            self.document = {"_id": "maintenance", **update["$set"]}
        elif self.document and self.document.get("token") == query.get("token"):
            self.document.update(update["$set"])

    async def find_one(self, query, *_args, **_kwargs):
        if query.get("active") is True and not (self.document or {}).get("active"):
            return None
        return self.document


class StateDatabase:
    def __init__(self):
        self.state = StateCollection()

    def __getitem__(self, name):
        assert name == COLLECTIONS["state"]
        return self.state


def test_maintenance_lease_excludes_concurrent_work_and_releases():
    async def exercise():
        db = StateDatabase()

        async def work():
            assert (await maintenance_state(db))["active"] is True
            with pytest.raises(Conflict, match="Another maintenance operation"):
                await with_maintenance(db, "setup", lambda: asyncio.sleep(0))
            return "reanchored"

        assert await with_maintenance(db, "reanchor", work) == "reanchored"
        assert await maintenance_state(db) == {"active": False}

    asyncio.run(exercise())


class ReanchorCollection:
    def __init__(self, name, anchors):
        self.name = name
        self.anchors = anchors
        self.updates = []
        self.deleted = []
        self.inserted = []

    async def find_one(self, query, *_args, **_kwargs):
        if self.name == COLLECTIONS["entities"]:
            return {"anchorAt": self.anchors.get(query.get("profile")), "businessImpact": {"level": "high"}}
        return None

    async def update_many(self, query, pipeline, **options):
        self.updates.append((query, pipeline, options))
        return Result(modified_count=1 if self.name == COLLECTIONS["events"] else 2)

    async def delete_many(self, query):
        self.deleted.append(query)
        return Result(deleted_count=2)

    async def insert_many(self, documents):
        self.inserted.extend(documents)
        return Result(inserted_ids=range(len(documents)))


class ReanchorDatabase:
    def __init__(self, anchor):
        anchors = {profile: anchor for profile in ("retail", "healthcare", "manufacturing")}
        self.collections = {name: ReanchorCollection(name, anchors) for name in COLLECTIONS.values()}

    def __getitem__(self, name):
        return self.collections[name]


def test_reanchor_shifts_each_profile_and_regenerates_time_series_readings():
    now = datetime(2026, 1, 1, 12, tzinfo=timezone.utc)
    db = ReanchorDatabase(now - timedelta(hours=2))
    report = asyncio.run(reanchor_demo(db, now=now))
    assert [item["shiftedMin"] for item in report] == [120, 120, 120]
    assert all(item["events"] == 1 and item["exceptions"] == 2 for item in report)
    assert all(item["telemetry"] == {"removed": 2, "inserted": 36} for item in report)
    events = db[COLLECTIONS["events"]]
    assert events.updates[0][2] == {"bypass_document_validation": True}
    assert events.updates[0][1] == event_shift_pipeline(7_200_000)
    telemetry = db[COLLECTIONS["telemetry"]]
    assert len(telemetry.inserted) == 108
    assert sum(item["value"] == 6.8 for item in telemetry.inserted) == 2