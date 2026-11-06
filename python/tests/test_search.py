import asyncio

import pytest

from app.errors import ServiceUnavailable
from app.profiles import get_profile
from app.search import find_similar, search_pipeline, search_status


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
    def __init__(self, indexes=(), rows=()):
        self.indexes = indexes
        self.rows = rows
        self.pipeline = None

    async def list_search_indexes(self, _):
        return Cursor(self.indexes)

    async def find_one(self, _, __):
        return {"_id": "self", "summary": "Carrier delay", "profile": "retail"}

    async def aggregate(self, pipeline):
        self.pipeline = pipeline
        return Cursor(self.rows)


class Database:
    def __init__(self, events, exceptions):
        self.collections = {"operational_events": events, "exceptions": exceptions}

    def __getitem__(self, name):
        return self.collections[name]


def test_search_status_reports_missing_indexes_without_writing():
    db = Database(Collection(), Collection())
    status = asyncio.run(search_status(db))
    assert status["ready"] is False
    assert status["similar"]["ready"] is False
    assert [item["status"] for item in status["indexes"]] == ["MISSING", "MISSING", "MISSING"]
    assert "missing" in status["message"].lower()


def test_search_pipeline_uses_search_first_and_profile_token_filters():
    _, pipeline = search_pipeline(get_profile("retail"), query="traffic", kind="events", source="carrier", limit=4)
    search = pipeline[0]["$search"]
    assert search["index"] == "ops_events_search"
    assert search["compound"]["filter"] == [
        {"equals": {"path": "profile", "value": "retail"}},
        {"equals": {"path": "source", "value": "carrier"}},
    ]
    assert pipeline[1] == {"$limit": 4}
    with pytest.raises(Exception, match="kind"):
        search_pipeline(get_profile("retail"), query="traffic", kind="invalid")


def test_similar_cases_exclude_the_source_exception(monkeypatch):
    exceptions = Collection(rows=[{"_id": "self", "exceptionId": "EXC-1"}, {"_id": "past", "exceptionId": "HIST-1", "resolution": {"note": "Used a backup carrier"}}])
    db = Database(Collection(), exceptions)

    async def ready(_):
        return {"ready": True, "similar": {"ready": True}}

    monkeypatch.setattr("app.search.search_status", ready)
    result = asyncio.run(find_similar(db, "EXC-1", limit=3))
    assert result["query"] == "Carrier delay"
    assert result["results"] == [{"exceptionId": "HIST-1", "resolution": {"note": "Used a backup carrier"}}]
    assert exceptions.pipeline[0]["$vectorSearch"]["filter"] == {"$and": [{"status": {"$eq": "resolved"}}, {"profile": {"$eq": "retail"}}]}


def test_similar_cases_refuse_when_the_vector_index_is_not_ready(monkeypatch):
    async def unavailable(_):
        return {"ready": False, "similar": {"ready": False, "message": "Similar-case lookup indexes are missing."}}

    monkeypatch.setattr("app.search.search_status", unavailable)
    with pytest.raises(ServiceUnavailable, match="Similar-case lookup"):
        asyncio.run(find_similar(Database(Collection(), Collection()), "EXC-1"))