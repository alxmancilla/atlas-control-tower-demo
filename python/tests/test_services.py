import asyncio
from datetime import datetime, timedelta, timezone

from app.profiles import get_profile
from app.risk import assess_milestones
from app.services import portfolio_overview


class AsyncCursor:
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


class AsyncCollection:
    async def aggregate(self, pipeline):
        assert pipeline[0] == {"$match": {"profile": "retail"}}
        return AsyncCursor([
            {"trackedEntityId": "A", "businessImpact": {"level": "critical"}, "openExceptionCount": 2},
            {"trackedEntityId": "B", "businessImpact": {"level": "none"}, "openExceptionCount": 0},
        ])


class AsyncDatabase:
    def __getitem__(self, name):
        assert name == "tracked_entities"
        return AsyncCollection()


def test_portfolio_awaits_async_pymongo_aggregate_cursor():
    result = asyncio.run(portfolio_overview(AsyncDatabase(), get_profile("retail")))
    assert result["summary"] == {"total": 2, "critical": 1, "high": 0, "watch": 0, "openExceptions": 2}


def test_milestone_assessment_accepts_pymongo_naive_utc_dates():
    now = datetime.now(timezone.utc)
    plan = [{"id": "arrived", "label": "Arrived", "plannedTime": (now - timedelta(minutes=5)).replace(tzinfo=None), "actualTime": None}]
    result = assess_milestones(plan, now, {"onTimeToleranceMin": 15, "missedGraceMin": 30, "atRiskWindowMin": 45})
    assert result["riskLevel"] == "medium"