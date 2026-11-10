import asyncio
from datetime import datetime, timedelta, timezone

from app.impact import assess_load_impact, recompute_impact, recommend_actions
from app.profiles import get_profile


NOW = datetime(2026, 1, 1, 12, tzinfo=timezone.utc)
LINES = [
    {"orderRef": "RO-1", "stop": 1, "stopOffsetMin": 0, "destination": {"id": "STORE-1", "name": "Store 1"}, "itemRef": "SKU-1", "quantity": 20, "unit": "cases"},
    {"orderRef": "RO-2", "stop": 2, "stopOffsetMin": 30, "destination": {"id": "STORE-2", "name": "Store 2"}, "itemRef": "SKU-1", "quantity": 20, "unit": "cases"},
]


def assessment(*, received=False):
    final = {"id": "received", "label": "Received", "plannedTime": NOW + timedelta(hours=3), "projectedTime": NOW + timedelta(hours=3), "actualTime": NOW if received else None}
    return {"milestones": [{"id": "departed", "label": "Departed", "plannedTime": NOW, "projectedTime": NOW, "actualTime": NOW}, final]}


def test_per_stop_impact_and_recommendations_clear_when_final_milestone_completes():
    positions = [{"locationId": "STORE-1", "itemRef": "SKU-1", "onHand": 20, "consumptionPerHour": 10, "unit": "cases"}, {"locationId": "STORE-2", "itemRef": "SKU-1", "onHand": 100, "consumptionPerHour": 10, "unit": "cases"}]
    config = get_profile("retail").impact
    impact = assess_load_impact(assessment(), LINES, positions, NOW, config)
    assert impact["level"] == "high"
    assert impact["linesAtRisk"] == 1
    assert recommend_actions(impact)[0]["actionId"] == "stop_1"
    restored = assess_load_impact(assessment(received=True), LINES, positions, NOW, config)
    assert restored["level"] == "none"
    assert recommend_actions(restored) == []


class Cursor:
    def __init__(self, rows):
        self.rows = rows

    def __aiter__(self):
        self.iterator = iter(self.rows)
        return self

    async def __anext__(self):
        try:
            return next(self.iterator)
        except StopIteration as error:
            raise StopAsyncIteration from error


class Collection:
    def __init__(self, rows=()):
        self.rows, self.updates = list(rows), []

    def find(self, *_args):
        return Cursor(self.rows)

    async def update_one(self, query, update):
        self.updates.append((query, update))

    async def update_many(self, query, update):
        self.updates.append((query, update))


def test_recompute_persists_entity_impact_and_refreshes_open_exception_actions(monkeypatch):
    profile = get_profile("retail")
    positions = Collection([{"locationId": "STORE-1", "itemRef": "SKU-1", "onHand": 20, "consumptionPerHour": 10, "unit": "cases"}, {"locationId": "STORE-2", "itemRef": "SKU-1", "onHand": 100, "consumptionPerHour": 10, "unit": "cases"}])
    entities, exceptions = Collection(), Collection()

    async def overview(*_args, **_kwargs):
        return {"entity": {"orderLines": LINES}, "assessment": assessment()}

    class Database:
        collections = {"inventory_positions": positions, "tracked_entities": entities, "exceptions": exceptions}

        def __getitem__(self, name):
            return self.collections[name]

    monkeypatch.setattr("app.impact.entity_overview", overview)
    impact = asyncio.run(recompute_impact(Database(), profile, NOW))
    assert impact["level"] == "high"
    assert entities.updates[0][1]["$set"]["businessImpact"]["level"] == "high"
    updated = exceptions.updates[0][1]["$set"]
    assert updated["businessImpact"]["level"] == "high"
    assert updated["recommendedActions"][0]["actionId"] == "stop_1"