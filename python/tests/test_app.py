import asyncio
import re
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
        csp = dashboard.headers["content-security-policy"]
        nonce = re.search(r"nonce-([^']+)", csp)
        assert nonce is not None
        assert f'<script nonce="{nonce.group(1)}">' in dashboard.text
        assert "object-src 'none'" in csp
        assert dashboard.headers["referrer-policy"] == "no-referrer"
        profiles = test_client.get("/api/profiles").json()
        assert profiles["defaultProfile"] == "retail"
        assert [profile["id"] for profile in profiles["profiles"]] == ["retail", "healthcare", "manufacturing"]
        assert profiles["profiles"][0]["samples"][0]["id"] == "arrived_dc"
        assert profiles["profiles"][0]["sourceContracts"]["berry_supplier"]["version"] == 1


def test_dashboard_exposes_structured_operational_panels_and_guarded_actions():
    with client() as test_client:
        page = test_client.get("/").text
        stylesheet = test_client.get("/static/dashboard.css")
    for label in ("Operational overview", "Milestone risk", "Event reliability", "Data-quality queue", "Cross-domain disruption trace", "Exception workflow", "Governed execution handoff", "Active load portfolio", "Control tower KPIs", "Schema validation"):
        assert label in page
    assert "data-exception-action" in page
    assert "data-recommendation" in page
    assert "data-quality-action" in page
    assert "data-repair" in page
    assert 'href="/static/dashboard.css"' in page
    assert stylesheet.headers["content-type"].startswith("text/css")
    assert "const esc=" in page
    assert "AbortController" in page
    assert "Promise.allSettled" in page
    assert "X-Admin-Token" in page
    assert "sessionStorage" in page
    assert "Some dashboard sections are unavailable" in page
    assert "Atlas Stream Processing rollups" in page
    assert "sourceContractSummary" in page
    assert "bounded linked operational context" in page
    assert "data-execution-action" in page
    assert "/api/execution-outbox" in page


def test_status_is_safe_without_a_connection_string():
    with client() as test_client:
        status = test_client.get("/api/status").json()
        assert status["connected"] is False
        assert status["target"] == "unparseable connection string"


def test_wildcard_bind_rejects_unknown_hosts_and_allows_configured_lan_host():
    settings = Settings(mongodb_uri=None, db_name="atlas_control_tower_demo", allowed_hosts=frozenset({"localhost", "127.0.0.1", "[::1]", "tower.example"}))
    app = create_app(settings)
    with TestClient(app, base_url="http://example.test:8000") as test_client:
        assert test_client.get("/health").status_code == 421
    with TestClient(app, base_url="http://tower.example:8000") as test_client:
        assert test_client.get("/health").status_code == 200


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