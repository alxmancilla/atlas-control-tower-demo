import pytest

from app.config import ConfigError, Settings, assert_demo_db_name, assert_safe_binding, boolean_env, is_atlas_host, redact, target_label


URI = "mongodb+srv://user:secret@demo.abcde.mongodb.net/?retryWrites=true"


def test_configuration_requires_atlas_and_demo_database():
    settings = Settings.from_env({"MONGODB_URI": URI})
    assert settings.target == "mongodb+srv://demo.abcde.mongodb.net"
    assert settings.host == "0.0.0.0"
    assert settings.port == 8000
    assert is_atlas_host(URI)
    assert not is_atlas_host("mongodb://localhost:27017")
    assert "secret" not in redact(URI)
    assert target_label(URI) == "mongodb+srv://demo.abcde.mongodb.net"
    with pytest.raises(ConfigError, match="Atlas"):
        Settings.from_env({"MONGODB_URI": "mongodb://localhost:27017"})
    with pytest.raises(ConfigError, match="demo"):
        assert_demo_db_name("production")


def test_wildcard_default_requires_an_admin_token():
    with pytest.raises(ConfigError, match="ADMIN_TOKEN"):
        assert_safe_binding(Settings.from_env({"MONGODB_URI": URI}))
    assert_safe_binding(Settings.from_env({"MONGODB_URI": URI, "ADMIN_TOKEN": "test-token"}))
    assert_safe_binding(Settings.from_env({"MONGODB_URI": URI, "HOST": "127.0.0.1"}))


def test_stream_processing_flag_is_explicit_and_validated():
    assert Settings.from_env({"MONGODB_URI": URI, "ASP_TELEMETRY_ENABLED": "true"}).stream_processing_enabled is True
    assert boolean_env({}, "ASP_TELEMETRY_ENABLED") is False
    with pytest.raises(ConfigError, match="true or false"):
        Settings.from_env({"MONGODB_URI": URI, "ASP_TELEMETRY_ENABLED": "sometimes"})