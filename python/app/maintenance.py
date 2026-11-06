from __future__ import annotations

from collections.abc import Awaitable, Callable
from datetime import datetime, timedelta, timezone
from uuid import uuid4

from pymongo.errors import DuplicateKeyError

from .db import COLLECTIONS
from .errors import Conflict


LOCK_ID = "maintenance"
LOCK_TTL = timedelta(minutes=10)


async def maintenance_state(db, *, now: datetime | None = None) -> dict[str, object]:
    now = now or datetime.now(timezone.utc)
    doc = await db[COLLECTIONS["state"]].find_one(
        {"_id": LOCK_ID, "active": True, "until": {"$gt": now}},
        {"op": 1, "startedAt": 1},
    )
    return {"active": True, "op": doc.get("op"), "startedAt": doc.get("startedAt")} if doc else {"active": False}


async def with_maintenance(db, operation: str, work: Callable[[], Awaitable[object]]) -> object:
    now = datetime.now(timezone.utc)
    token = f"{operation}:{uuid4().hex}"
    state = db[COLLECTIONS["state"]]
    try:
        await state.update_one(
            {"_id": LOCK_ID, "$or": [{"active": False}, {"until": {"$lt": now}}]},
            {"$set": {"active": True, "op": operation, "token": token, "startedAt": now, "until": now + LOCK_TTL}},
            upsert=True,
        )
    except DuplicateKeyError as error:
        held = await state.find_one({"_id": LOCK_ID}, {"op": 1})
        raise Conflict(f"Another maintenance operation ({(held or {}).get('op', 'unknown')}) is running. Try again when it finishes.") from error
    try:
        return await work()
    finally:
        try:
            await state.update_one({"_id": LOCK_ID, "token": token}, {"$set": {"active": False, "finishedAt": datetime.now(timezone.utc)}})
        except Exception:
            # The lease expires if Atlas becomes unavailable during cleanup.
            pass