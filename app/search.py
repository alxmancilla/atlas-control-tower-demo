from __future__ import annotations

from datetime import datetime, timezone

from pymongo.operations import SearchIndexModel

from .config import redact
from .db import COLLECTIONS
from .errors import BadRequest, NotFound, ServiceUnavailable
from .profiles import Profile


SEARCH_INDEXES = (
    {"collection": COLLECTIONS["events"], "name": "ops_events_search", "type": "search", "feature": "search", "definition": {"mappings": {"dynamic": False, "fields": {"description": {"type": "string"}, "notes": {"type": "string"}, "profile": {"type": "token"}, "source": {"type": "token"}, "eventType": {"type": "token"}, "trackedEntityId": {"type": "token"}}}}},
    {"collection": COLLECTIONS["exceptions"], "name": "ops_exceptions_search", "type": "search", "feature": "search", "definition": {"mappings": {"dynamic": False, "fields": {"summary": {"type": "string"}, "categoryLabel": {"type": "string"}, "resolution": {"type": "document", "fields": {"note": {"type": "string"}}}, "profile": {"type": "token"}, "status": {"type": "token"}, "category": {"type": "token"}}}}},
    {"collection": COLLECTIONS["exceptions"], "name": "ops_exceptions_similar", "type": "vectorSearch", "feature": "similar", "definition": {"fields": [{"type": "autoEmbed", "modality": "text", "path": "summary", "model": "voyage-4"}, {"type": "filter", "path": "status"}, {"type": "filter", "path": "profile"}]}},
)


def _feature_status(indexes: list[dict], feature: str, label: str) -> dict:
    matching = [item for item in indexes if item["feature"] == feature]
    ready = bool(matching) and all(item["queryable"] for item in matching)
    if ready:
        message = f"{label} is ready."
    elif any(item["status"] == "UNAVAILABLE" for item in matching):
        message = f"{label} is unavailable on this connection. See the error per index."
    elif any(item["status"] == "MISSING" for item in matching):
        message = f'{label} indexes are missing. Run "control-tower search-create" or create them in the Atlas UI.'
    else:
        message = f"{label} indexes are building. It stays disabled until Atlas reports them queryable."
    return {"ready": ready, "message": message}


async def search_status(db) -> dict:
    indexes = []
    for spec in SEARCH_INDEXES:
        base = {key: spec[key] for key in ("collection", "name", "type", "feature")}
        try:
            cursor = await db[spec["collection"]].list_search_indexes(spec["name"])
            found = [item async for item in cursor]
            indexes.append({**base, "status": found[0].get("status"), "queryable": found[0].get("queryable") is True} if found else {**base, "status": "MISSING", "queryable": False})
        except Exception as error:
            indexes.append({**base, "status": "UNAVAILABLE", "queryable": False, "error": redact(error)})
    search = _feature_status(indexes, "search", "MongoDB Search")
    similar = _feature_status(indexes, "similar", "Similar-case lookup (Automated Embedding)")
    return {**search, "similar": similar, "indexes": indexes, "checkedAt": datetime.now(timezone.utc)}


async def create_search_indexes(db) -> list[dict]:
    """Request only missing Atlas Search indexes; never replace an existing index."""
    current = {item["name"]: item for item in (await search_status(db))["indexes"]}
    results = []
    for spec in SEARCH_INDEXES:
        existing = current[spec["name"]]
        if existing["status"] != "MISSING":
            results.append({"name": spec["name"], "action": "skipped", "status": existing["status"], **({"error": existing["error"]} if existing.get("error") else {})})
            continue
        try:
            model = SearchIndexModel(definition=spec["definition"], name=spec["name"], type=spec["type"])
            await db[spec["collection"]].create_search_index(model)
            results.append({"name": spec["name"], "action": "requested"})
        except Exception as error:
            results.append({"name": spec["name"], "action": "failed", "error": redact(error)})
    return results


def _branch_project(kind: str, title: str, text: str) -> dict:
    return {"$project": {"_id": 0, "kind": {"$literal": kind}, "id": {"$toString": "$_id"}, "trackedEntityId": 1, "title": title, "text": text, "source": 1, "eventType": 1, "status": 1, "severity": 1, "exceptionId": 1, "eventTime": 1, "score": {"$meta": "searchScore"}, "highlights": {"$meta": "searchHighlights"}}}


def search_pipeline(profile: Profile, *, query: str, kind: str = "all", source: str | None = None, event_type: str | None = None, status: str | None = None, limit: int = 10) -> tuple[str, list[dict]]:
    if kind not in {"all", "events", "exceptions"}:
        raise BadRequest('kind must be "all", "events", or "exceptions"')
    event_filter = [{"equals": {"path": "profile", "value": profile.id}}]
    if source:
        event_filter.append({"equals": {"path": "source", "value": source}})
    if event_type:
        event_filter.append({"equals": {"path": "eventType", "value": event_type}})
    exception_filter = [{"equals": {"path": "profile", "value": profile.id}}]
    if status:
        exception_filter.append({"equals": {"path": "status", "value": status}})
    events = [{"$search": {"index": "ops_events_search", "compound": {"must": [{"text": {"query": query, "path": ["description", "notes"], "fuzzy": {"maxEdits": 1}}}], "filter": event_filter}, "highlight": {"path": ["description", "notes"]}}}, {"$limit": limit}, _branch_project("event", "$description", "$notes")]
    exceptions = [{"$search": {"index": "ops_exceptions_search", "compound": {"must": [{"text": {"query": query, "path": ["summary", "categoryLabel", "resolution.note"], "fuzzy": {"maxEdits": 1}}}], "filter": exception_filter}, "highlight": {"path": ["summary", "categoryLabel", "resolution.note"]}}}, {"$limit": limit}, _branch_project("exception", "$summary", "$resolution.note")]
    if kind == "events":
        return COLLECTIONS["events"], events
    if kind == "exceptions":
        return COLLECTIONS["exceptions"], exceptions
    return COLLECTIONS["events"], [*events, {"$unionWith": {"coll": COLLECTIONS["exceptions"], "pipeline": exceptions}}, {"$sort": {"score": -1}}, {"$limit": limit * 2}]


async def run_search(db, profile: Profile, *, q: str | None, kind: str = "all", source: str | None = None, event_type: str | None = None, status: str | None = None, limit: int = 10) -> dict:
    query = (q or "").strip()[:200]
    if not query:
        raise BadRequest("Enter search text")
    readiness = await search_status(db)
    if not readiness["ready"]:
        raise ServiceUnavailable(readiness["message"])
    collection, pipeline = search_pipeline(profile, query=query, kind=kind, source=source, event_type=event_type, status=status, limit=limit)
    try:
        cursor = await db[collection].aggregate(pipeline)
        results = [item async for item in cursor]
    except Exception as error:
        raise ServiceUnavailable(f"Search failed: {redact(error)}") from error
    return {"q": query, "results": results, "status": readiness}


def similar_pipeline(exception: dict, *, limit: int = 3) -> list[dict]:
    return [{"$vectorSearch": {"index": "ops_exceptions_similar", "path": "summary", "query": exception["summary"], "filter": {"$and": [{"status": {"$eq": "resolved"}}, {"profile": {"$eq": exception["profile"]}}]}, "numCandidates": (limit + 1) * 20, "limit": limit + 1}}, {"$project": {"_id": 1, "exceptionId": 1, "trackedEntityId": 1, "category": 1, "categoryLabel": 1, "summary": 1, "resolution": 1, "score": {"$meta": "vectorSearchScore"}}}]


async def find_similar(db, exception_id: str, *, limit: int = 3) -> dict:
    exception = await db[COLLECTIONS["exceptions"]].find_one({"exceptionId": exception_id}, {"summary": 1, "profile": 1})
    if not exception:
        raise NotFound(f"Exception {exception_id} not found")
    readiness = await search_status(db)
    if not readiness["similar"]["ready"]:
        raise ServiceUnavailable(readiness["similar"]["message"])
    try:
        cursor = await db[COLLECTIONS["exceptions"]].aggregate(similar_pipeline(exception, limit=limit))
        rows = [item async for item in cursor]
    except Exception as error:
        raise ServiceUnavailable(f"Similar-case lookup failed: {redact(error)}") from error
    results = []
    for row in rows:
        if row["_id"] == exception["_id"]:
            continue
        row.pop("_id", None)
        results.append(row)
        if len(results) == limit:
            break
    return {"exceptionId": exception_id, "query": exception["summary"], "results": results}