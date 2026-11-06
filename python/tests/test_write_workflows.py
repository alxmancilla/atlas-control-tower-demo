import asyncio
from datetime import datetime, timezone

import pytest
from fastapi.testclient import TestClient
from pymongo.errors import DuplicateKeyError

from app.config import Settings
from app.events import _within_transaction, build_event, classify_arrival, ingest_event
from app.main import create_app
from app.profiles import get_profile
from app.schemas import EventInput


def payload(**overrides):
    body = {
        "source": "carrier",
        "sourceEventId": "TMS-TEST-1",
        "eventType": "exception",
        "eventTime": "2026-01-01T12:00:00Z",
        "description": "Synthetic test event",
        "exceptionCategory": "delivery_delay",
    }
    body.update(overrides)
    return body


def test_event_builder_validates_profile_contract_and_reliability():
    profile = get_profile("retail")
    received = datetime(2026, 1, 1, 12, 45, tzinfo=timezone.utc)
    event = build_event(profile, EventInput.model_validate(payload()), received)
    assert event["idempotencyKey"] == "carrier:TMS-TEST-1"
    assert event["channel"] == "edi"
    reliability = classify_arrival(event["eventTime"], received, None, None, profile.risk)
    assert reliability == {"status": "late", "flags": ["late"], "lagMinutes": 45, "duplicateCount": 0}
    with pytest.raises(ValueError, match="validation error"):
        EventInput.model_validate({"source": "carrier"})


def test_ingestion_retries_exception_races_and_suppresses_duplicate(monkeypatch):
    profile = get_profile("retail")
    event = EventInput.model_validate(payload())
    calls = {"insert": 0, "duplicate": 0}

    async def fake_transaction(_, callback):
        return await callback(object())

    async def race_then_insert(_, __, ___, ____):
        calls["insert"] += 1
        if calls["insert"] == 1:
            raise DuplicateKeyError("exceptionKey_unique")
        return {"outcome": "accepted"}

    monkeypatch.setattr("app.events._within_transaction", fake_transaction)
    monkeypatch.setattr("app.events._insert_new", race_then_insert)
    assert asyncio.run(ingest_event(object(), profile, event)) == {"outcome": "accepted"}
    assert calls["insert"] == 2

    async def duplicate(_, __, ___, ____):
        calls["duplicate"] += 1
        return {"outcome": "deduplicated"}

    monkeypatch.setattr("app.events._insert_new", lambda *args: (_ for _ in ()).throw(DuplicateKeyError("idempotencyKey_unique")))
    monkeypatch.setattr("app.events._record_duplicate", duplicate)
    assert asyncio.run(ingest_event(object(), profile, event)) == {"outcome": "deduplicated"}
    assert calls["duplicate"] == 1


def test_transaction_uses_pymongo_async_session_context_manager():
    class Session:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *_):
            return False

        async def with_transaction(self, callback):
            return await callback(self)

    class Client:
        def start_session(self):
            return Session()

    class Database:
        client = Client()

    assert asyncio.run(_within_transaction(Database(), lambda _: asyncio.sleep(0, result="committed"))) == "committed"


def test_write_guard_requires_json_local_host_and_configured_database():
    app = create_app(Settings(mongodb_uri=None, db_name="atlas_control_tower_demo"))
    with TestClient(app, base_url="http://localhost:8000") as client:
        assert client.post("/api/events", data="not-json").status_code == 415
        assert client.post("/api/events", json=payload()).status_code == 503
        assert client.post("/api/reanchor", data="not-json").status_code == 415
        assert client.post("/api/reanchor", json={}).status_code == 503
    with TestClient(app, base_url="http://example.test") as client:
        assert client.get("/health").status_code == 421