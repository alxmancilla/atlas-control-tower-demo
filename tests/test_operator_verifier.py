import pytest

from verify_operator_journey import VERIFY_SUFFIX, arguments, recommendation_to_accept


def test_operator_verifier_requires_an_explicit_disposable_database_confirmation():
    database = f"atlas_control_tower_demo{VERIFY_SUFFIX}"
    parsed = arguments(["--database", database, "--confirm-cleanup", database])
    assert parsed.database == database
    with pytest.raises(SystemExit):
        arguments(["--database", "atlas_control_tower_demo", "--confirm-cleanup", "atlas_control_tower_demo"])
    with pytest.raises(SystemExit):
        arguments(["--database", database, "--confirm-cleanup", "different_demo_operator_verify"])


def test_operator_verifier_selects_only_an_undecided_recommendation():
    queue = [
        {"exceptionId": "EXC-OLD", "recommendedActions": [{"actionId": "stop_1"}], "actionDecisions": {"stop_1": {"decision": "accepted"}}},
        {"exceptionId": "EXC-OPEN", "recommendedActions": [{"actionId": "stop_2"}]},
    ]
    assert recommendation_to_accept(queue) == ("EXC-OPEN", "stop_2")
    with pytest.raises(RuntimeError, match="actionable recommendation"):
        recommendation_to_accept([])