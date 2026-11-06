from fastapi.testclient import TestClient

from app.config import Settings
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


def test_status_is_safe_without_a_connection_string():
    with client() as test_client:
        status = test_client.get("/api/status").json()
        assert status["connected"] is False
        assert status["target"] == "unparseable connection string"