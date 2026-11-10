from __future__ import annotations

from .db import COLLECTIONS
from .profiles import Profile


async def disruption_trace(db, profile: Profile) -> dict | None:
    """Return the bounded cross-domain topology plus the source signals that support it."""
    entity = await db[COLLECTIONS["entities"]].find_one(
        {"profile": profile.id, "trackedEntityId": profile.entity_id},
        {"_id": 0, "trackedEntityId": 1, "product": 1, "businessImpact": 1, "crossDomain": 1},
    )
    if not entity:
        return None
    trace_id = (entity.get("crossDomain") or {}).get("traceId")
    signals = []
    if trace_id:
        cursor = db[COLLECTIONS["events"]].find(
            {"profile": profile.id, "trackedEntityId": profile.entity_id, "details.crossDomainTrace": trace_id},
            {"_id": 0, "source": 1, "description": 1, "eventTime": 1, "receivedAt": 1, "reliability": 1, "sourceContract": 1, "details": 1},
        ).sort([("eventTime", 1)])
        signals = [item async for item in cursor]
    return {"entity": entity, "trace": entity.get("crossDomain"), "signals": signals}