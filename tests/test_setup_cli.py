import asyncio

import pytest

from app.cli import arguments
from app.db import COLLECTIONS
from app.profiles import get_profile
from app.search import create_search_indexes
from app.setup import _backfill_cross_domain_trace, _backfill_quality_issues, ensure_setup, reset_demo


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
    def __init__(self, upserted_id=None, modified_count=0):
        self.upserted_id = upserted_id
        self.modified_count = modified_count


class Collection:
    def __init__(self, search_indexes=(), events=()):
        self.search_indexes = search_indexes
        self.events = events
        self.created_indexes = []
        self.updates = []

    async def create_index(self, fields, **options):
        self.created_indexes.append((fields, options))

    async def update_one(self, query, update, **options):
        self.updates.append((query, update, options))
        return Result()

    async def list_search_indexes(self, name):
        return Cursor(self.search_indexes.get(name, ()) if isinstance(self.search_indexes, dict) else self.search_indexes)

    async def create_search_index(self, model):
        self.created_indexes.append(model)

    def find(self, _query):
        return Cursor(self.events)


class SetupDatabase:
    name = "atlas_control_tower_demo"

    def __init__(self, collections=()):
        self.metadata = list(collections)
        self.created = []
        self.collections = {name: Collection() for name in COLLECTIONS.values()}

    async def list_collections(self):
        return Cursor(self.metadata)

    async def create_collection(self, name, **options):
        self.created.append((name, options))
        self.metadata.append({"name": name, "type": "timeseries" if "timeseries" in options else "collection"})

    def __getitem__(self, name):
        return self.collections[name]


def test_setup_creates_every_demo_collection_and_expected_indexes():
    db = SetupDatabase()
    result = asyncio.run(ensure_setup(db))
    assert result["created"] == list(COLLECTIONS.values())
    assert result["indexes"] == 16
    telemetry = next(options for name, options in db.created if name == COLLECTIONS["telemetry"])
    assert telemetry["timeseries"] == {"timeField": "ts", "metaField": "meta", "granularity": "minutes"}
    assert db[COLLECTIONS["state"]].updates[0][0] == {"_id": "validation"}


def test_setup_refuses_an_existing_non_time_series_telemetry_collection():
    db = SetupDatabase([{"name": COLLECTIONS["telemetry"], "type": "collection"}])
    with pytest.raises(ValueError, match="not a time-series"):
        asyncio.run(ensure_setup(db))


def test_reset_requires_the_exact_configured_database_name_before_any_write():
    with pytest.raises(ValueError, match="Reset not confirmed"):
        asyncio.run(reset_demo(SetupDatabase(), "other_demo"))


def test_search_creation_requests_only_missing_indexes():
    events = Collection(search_indexes={})
    exceptions = Collection(search_indexes={"ops_exceptions_search": [{"status": "READY", "queryable": True}]})

    class SearchDatabase:
        def __getitem__(self, name):
            return events if name == COLLECTIONS["events"] else exceptions

    result = asyncio.run(create_search_indexes(SearchDatabase()))
    assert result == [
        {"name": "ops_events_search", "action": "requested"},
        {"name": "ops_exceptions_search", "action": "skipped", "status": "READY"},
        {"name": "ops_exceptions_similar", "action": "requested"},
    ]
    assert len(events.created_indexes) == 1
    assert len(exceptions.created_indexes) == 1


def test_quality_backfill_opens_only_new_work_items(monkeypatch):
    db = SetupDatabase()
    opened = []

    async def fake_upsert(_db, _profile, event):
        opened.append(event["_id"])
        return [{"outcome": "opened"}, {"outcome": "updated"}]

    monkeypatch.setattr("app.setup.upsert_quality_issues", fake_upsert)
    db[COLLECTIONS["events"]].events = [{"_id": "one"}, {"_id": "two"}]
    assert asyncio.run(_backfill_quality_issues(db, get_profile("retail"))) == 2
    assert opened == ["one", "two"]


def test_setup_backfills_only_a_missing_cross_domain_trace_fixture():
    class EntityCollection:
        async def update_one(self, query, update):
            assert query == {"trackedEntityId": "RTL-SHP-24017", "crossDomain": {"$exists": False}}
            assert update == {"$set": {"crossDomain": {"traceId": "RTL-BERRIES-ETA-01"}}}
            return Result(modified_count=1)

    entity = {"trackedEntityId": "RTL-SHP-24017", "crossDomain": {"traceId": "RTL-BERRIES-ETA-01"}}
    assert asyncio.run(_backfill_cross_domain_trace(EntityCollection(), entity)) is True


def test_python_admin_cli_parses_reset_confirmation_and_search_opt_out():
    args = arguments(["reset", "--confirm", "atlas_control_tower_demo", "--without-search"])
    assert args.command == "reset"
    assert args.confirm == "atlas_control_tower_demo"
    assert args.without_search is True