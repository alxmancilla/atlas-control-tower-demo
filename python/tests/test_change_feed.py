import asyncio

from fastapi.testclient import TestClient

from app.change_feed import ChangeFeed, WATCHED_COLLECTIONS, summarize_change
from app.config import Settings
from app.main import create_app


class Timestamp:
    time = 1_767_225_600


def sample_change():
    return {
        "_id": {"token": 1},
        "ns": {"coll": "exceptions"},
        "operationType": "update",
        "documentKey": {"_id": "mongo-id"},
        "fullDocument": {"profile": "retail", "trackedEntityId": "RTL-SHP-24017", "exceptionId": "EXC-1", "status": "acknowledged", "summary": "Carrier delay", "reliability": {"status": "accepted", "duplicateCount": 2}},
        "updateDescription": {"updatedFields": {"status": "acknowledged", "audit": []}},
        "txnNumber": 9,
        "clusterTime": Timestamp(),
    }


def test_change_summary_and_pipeline_exclude_time_series_documents():
    summary = summarize_change(sample_change())
    assert summary == {"coll": "exceptions", "op": "update", "id": "mongo-id", "profile": "retail", "trackedEntityId": "RTL-SHP-24017", "eventType": None, "reliability": "accepted", "duplicateCount": 2, "exceptionId": "EXC-1", "exceptionStatus": "acknowledged", "description": "Carrier delay", "updatedFields": ["status", "audit"], "txn": "9", "clusterTime": summary["clusterTime"]}
    assert summary["clusterTime"].isoformat() == "2026-01-01T00:00:00+00:00"
    assert WATCHED_COLLECTIONS == ("operational_events", "exceptions", "data_quality_issues", "tracked_entities", "telemetry_rollups", "demo_state")
    assert ChangeFeed.pipeline() == [{"$match": {"ns.coll": {"$in": list(WATCHED_COLLECTIONS)}, "operationType": {"$in": ["insert", "update", "replace", "delete"]}}}]


def test_change_feed_broadcasts_changes_and_bounds_slow_subscribers():
    async def exercise():
        feed = ChangeFeed(object(), subscriber_queue_size=3)
        queue = feed.subscribe()
        await feed._handle_change(sample_change())
        status = await queue.get()
        change = await queue.get()
        assert status[0] == "stream-status"
        assert change[0] == "change"
        assert change[1]["exceptionId"] == "EXC-1"
        assert feed.resume_token == {"token": 1}

        slow_feed = ChangeFeed(object(), subscriber_queue_size=1)
        slow = slow_feed.subscribe()
        slow_feed._publish("change", {"n": 1})
        slow_feed._publish("change", {"n": 2})
        assert (await slow.get()) == ("resync", {"reason": "Live-update subscriber fell behind; reload current views."})

    asyncio.run(exercise())


def test_change_feed_uses_async_database_watch_and_stops_cleanly():
    class Stream:
        resume_token = {"resume": 1}

        async def __aenter__(self):
            return self

        async def __aexit__(self, *_):
            return False

        def __aiter__(self):
            return self

        async def __anext__(self):
            if not hasattr(self, "sent"):
                self.sent = True
                return sample_change()
            await asyncio.Event().wait()

    class Database:
        def __init__(self):
            self.calls = []

        async def watch(self, pipeline, **options):
            self.calls.append((pipeline, options))
            return Stream()

    async def exercise():
        db = Database()
        feed = ChangeFeed(db)
        queue = feed.subscribe()
        feed.start()
        while True:
            event, data = await asyncio.wait_for(queue.get(), timeout=1)
            if event == "change":
                assert data["coll"] == "exceptions"
                break
        assert db.calls == [(ChangeFeed.pipeline(), {"full_document": "updateLookup", "max_await_time_ms": 1_000})]
        await feed.stop()
        assert feed.state["state"] == "stopped"

    asyncio.run(exercise())


def test_sse_reports_unavailable_when_mongodb_is_not_configured():
    with TestClient(create_app(Settings(mongodb_uri=None, db_name="atlas_control_tower_demo")), base_url="http://localhost:8000") as client:
        response = client.get("/api/stream")
    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/event-stream")
    assert "retry: 3000" in response.text
    assert 'event: stream-status\ndata: {"state":"unavailable"' in response.text