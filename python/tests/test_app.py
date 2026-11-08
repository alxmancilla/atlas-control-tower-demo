import asyncio
from types import SimpleNamespace

from fastapi.testclient import TestClient

from app.config import Settings
from app.db import connection_status, get_db
from app.main import create_app


def client():
    settings = Settings(mongodb_uri=None, db_name="atlas_control_tower_demo")
    return TestClient(create_app(settings), base_url="http://localhost:8000")


def test_health_and_profile_discovery_do_not_need_a_database():
    with client() as test_client:
        assert test_client.get("/health").json() == {"status": "ok", "implementation": "python-fastapi"}
        dashboard = test_client.get("/")
        assert dashboard.status_code == 200
        assert dashboard.headers["content-type"].startswith("text/html")
        assert "Atlas Control Tower" in dashboard.text
        profiles = test_client.get("/api/profiles").json()
        assert profiles["defaultProfile"] == "retail"
        assert [profile["id"] for profile in profiles["profiles"]] == ["retail", "healthcare", "manufacturing"]
        assert profiles["profiles"][0]["samples"][0]["id"] == "arrived_dc"


def test_dashboard_exposes_structured_operational_panels_and_guarded_actions():
    with client() as test_client:
        page = test_client.get("/").text
    for label in ("Operational overview", "Milestone risk", "Event reliability", "Exception workflow", "Active load portfolio", "Control tower KPIs", "Schema validation"):
        assert label in page
    assert "data-exception-action" in page
    assert "data-recommendation" in page
    assert "data-repair" in page
    assert "const esc=" in page


def test_status_is_safe_without_a_connection_string():
    with client() as test_client:
        status = test_client.get("/api/status").json()
        assert status["connected"] is False
        assert status["target"] == "unparseable connection string"


def test_connection_helpers_do_not_truth_test_an_async_database():
    class Database:
        def __bool__(self):
            raise AssertionError("AsyncDatabase does not support truth-value testing")

        async def command(self, command):
            assert command == "ping"
            return {"ok": 1}

    database = Database()
    app = SimpleNamespace(state=SimpleNamespace(
        settings=Settings(mongodb_uri="mongodb+srv://example.mongodb.net", db_name="atlas_control_tower_demo"),
        db=database,
        change_feed=None,
    ))
    assert get_db(app) is database
    assert asyncio.run(connection_status(app))["connected"] is True