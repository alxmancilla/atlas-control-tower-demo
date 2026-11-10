import asyncio
from datetime import datetime, timezone

import pytest
from bson import ObjectId

from app.errors import BadRequest, Conflict
from app.execution_outbox import apply_execution_action, build_execution_request, check_execution_action, execution_key_for
from app.exceptions import decide_recommended_action
from app.profiles import get_profile


NOW = datetime(2026, 1, 1, 12, tzinfo=timezone.utc)
EXCEPTION = {"exceptionId": "EXC-123", "trackedEntityId": "RTL-SHP-24017"}
ACTION = {"actionId": "expedite_store_1187", "title": "Expedite delivery", "rationale": "Shelf cover is low"}
DECISION = {"decision": "accepted", "by": "alex", "at": NOW}


def test_accepted_recommendation_builds_simulated_idempotent_handoff():
    request = build_execution_request(profile="retail", exception=EXCEPTION, action=ACTION, decision=DECISION, now=NOW)
    assert request["executionId"].startswith("OUT-")
    assert request["executionKey"] == execution_key_for("retail", "EXC-123", "expedite_store_1187")
    assert request["status"] == "pending"
    assert request["connector"] == {"type": "simulated", "label": "Simulation only — no external system write", "externalWrite": False}
    assert request["handoff"]["acceptedDecision"] == DECISION
    assert "no external system was called" in request["audit"][0]["note"]


def test_execution_lifecycle_requires_order_and_reconciliation_evidence():
    assert check_execution_action("pending", "submit", None) == ("submitted", None)
    assert check_execution_action("submitted", "acknowledge", None) == ("acknowledged", None)
    assert check_execution_action("submitted", "fail", "adapter timeout") == ("failed", "adapter timeout")
    assert check_execution_action("failed", "reconcile", "operator confirmed manual handoff") == ("reconciled", "operator confirmed manual handoff")
    with pytest.raises(BadRequest, match="Failure requires a note"):
        check_execution_action("submitted", "fail", None)
    with pytest.raises(BadRequest, match="Reconciliation requires a note"):
        check_execution_action("acknowledged", "reconcile", "")
    with pytest.raises(Conflict, match="reconciled"):
        check_execution_action("reconciled", "submit", None)


class OutboxCollection:
    def __init__(self, document):
        self.document = document
        self.update = None

    async def find_one(self, query):
        return self.document if query["executionId"] == self.document["executionId"] and query["profile"] == self.document["profile"] else None

    async def find_one_and_update(self, query, update, **_options):
        assert self.document["status"] in query["status"]["$in"]
        self.update = update
        self.document.update(update["$set"])
        self.document["audit"].extend(update["$push"]["audit"]["$each"])
        return self.document


class OutboxDatabase:
    def __init__(self, document):
        self.collection = OutboxCollection(document)

    def __getitem__(self, name):
        assert name == "execution_outbox"
        return self.collection


def test_simulated_submit_updates_audited_state_without_external_connector():
    request = build_execution_request(profile="retail", exception=EXCEPTION, action=ACTION, decision=DECISION, now=NOW)
    request["_id"] = ObjectId()
    db = OutboxDatabase(request)
    updated = asyncio.run(apply_execution_action(db, get_profile("retail"), request["executionId"], {"action": "submit", "operator": "alex"}))
    assert updated["status"] == "submitted"
    assert updated["_id"] == str(request["_id"])
    assert updated["submissionCount"] == 1
    assert updated["audit"][-1]["action"] == "submit"
    assert "no external system was called" in updated["audit"][-1]["note"]


def test_acceptance_records_decision_and_simulated_handoff_in_one_transaction():
    stored = {**EXCEPTION, "profile": "retail", "status": "open", "recommendedActions": [ACTION], "actionDecisions": {}}

    class Session:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *_):
            return False

        async def with_transaction(self, work):
            return await work(self)

    class Exceptions:
        async def find_one(self, _query, *, session):
            assert isinstance(session, Session)
            return stored

        async def find_one_and_update(self, _query, update, *, session, **_options):
            assert isinstance(session, Session)
            assert update["$set"]["actionDecisions.expedite_store_1187"]["decision"] == "accepted"
            return {**stored, "actionDecisions": {"expedite_store_1187": update["$set"]["actionDecisions.expedite_store_1187"]}}

    class Outbox:
        request = None

        async def insert_one(self, request, *, session):
            assert isinstance(session, Session)
            self.request = request

    class Database:
        client = type("Client", (), {"start_session": staticmethod(Session)})()

        def __init__(self):
            self.exceptions, self.outbox = Exceptions(), Outbox()

        def __getitem__(self, name):
            return self.exceptions if name == "exceptions" else self.outbox

    db = Database()
    result = asyncio.run(decide_recommended_action(db, "EXC-123", {"action_id": "expedite_store_1187", "decision": "accepted", "operator": "alex"}))
    assert result["execution"]["status"] == "pending"
    assert db.outbox.request["executionKey"] == "retail:EXC-123:expedite_store_1187"