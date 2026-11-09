import pytest

from verify_operator_journey import VERIFY_SUFFIX, arguments


def test_operator_verifier_requires_an_explicit_disposable_database_confirmation():
    database = f"atlas_control_tower_demo{VERIFY_SUFFIX}"
    parsed = arguments(["--database", database, "--confirm-cleanup", database])
    assert parsed.database == database
    with pytest.raises(SystemExit):
        arguments(["--database", "atlas_control_tower_demo", "--confirm-cleanup", "atlas_control_tower_demo"])
    with pytest.raises(SystemExit):
        arguments(["--database", database, "--confirm-cleanup", "different_demo_operator_verify"])