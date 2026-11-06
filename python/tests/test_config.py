import pytest

from app.config import ConfigError, Settings, assert_demo_db_name, is_atlas_host, redact, target_label


URI = "mongodb+srv://user:secret@demo.abcde.mongodb.net/?retryWrites=true"


def test_configuration_requires_atlas_and_demo_database():
    settings = Settings.from_env({"MONGODB_URI": URI})
    assert settings.target == "mongodb+srv://demo.abcde.mongodb.net"
    assert is_atlas_host(URI)
    assert not is_atlas_host("mongodb://localhost:27017")
    assert "secret" not in redact(URI)
    assert target_label(URI) == "mongodb+srv://demo.abcde.mongodb.net"
    with pytest.raises(ConfigError, match="Atlas"):
        Settings.from_env({"MONGODB_URI": "mongodb://localhost:27017"})
    with pytest.raises(ConfigError, match="demo"):
        assert_demo_db_name("production")