from __future__ import annotations

import asyncio
from contextlib import suppress
from datetime import datetime, timezone

from .config import redact


WATCHED_COLLECTIONS = ("operational_events", "exceptions", "data_quality_issues", "execution_outbox", "tracked_entities", "telemetry_rollups", "demo_state")
NON_RESUMABLE_CODES = frozenset({260, 280, 286})


def summarize_change(change: dict) -> dict:
    document = change.get("fullDocument") or {}
    namespace = change.get("ns") or {}
    update = change.get("updateDescription") or {}
    cluster_time = change.get("clusterTime")
    timestamp = getattr(cluster_time, "time", None)
    return {
        "coll": namespace.get("coll"),
        "op": change.get("operationType"),
        "id": str((change.get("documentKey") or {}).get("_id")) if (change.get("documentKey") or {}).get("_id") is not None else None,
        "profile": document.get("profile"),
        "trackedEntityId": document.get("trackedEntityId"),
        "eventType": document.get("eventType"),
        "reliability": (document.get("reliability") or {}).get("status"),
        "duplicateCount": (document.get("reliability") or {}).get("duplicateCount"),
        "exceptionId": document.get("exceptionId"),
        "exceptionStatus": document.get("status") if namespace.get("coll") == "exceptions" else None,
        "description": document.get("description") or document.get("summary"),
        "updatedFields": list((update.get("updatedFields") or {}).keys()),
        "txn": str(change["txnNumber"]) if change.get("txnNumber") is not None else None,
        "clusterTime": datetime.fromtimestamp(timestamp, timezone.utc) if timestamp else None,
    }


class ChangeFeed:
    """One resumable database-level feed, shared by bounded SSE subscriber queues."""

    def __init__(self, db, *, max_backoff_seconds: float = 30, subscriber_queue_size: int = 100):
        self.db = db
        self.max_backoff_seconds = max_backoff_seconds
        self.subscriber_queue_size = subscriber_queue_size
        self.resume_token = None
        self.attempts = 0
        self.stream = None
        self.stopped = True
        self.task: asyncio.Task | None = None
        self.subscribers: set[asyncio.Queue] = set()
        self.state = self._state("stopped")

    def _state(self, name: str, error: str | None = None) -> dict:
        return {"state": name, "error": error, "since": datetime.now(timezone.utc), "attempts": self.attempts}

    def subscribe(self) -> asyncio.Queue:
        queue: asyncio.Queue = asyncio.Queue(maxsize=self.subscriber_queue_size)
        self.subscribers.add(queue)
        return queue

    def unsubscribe(self, queue: asyncio.Queue) -> None:
        self.subscribers.discard(queue)

    def start(self) -> "ChangeFeed":
        if self.task and not self.task.done():
            return self
        self.stopped = False
        self.task = asyncio.create_task(self._run())
        return self

    async def stop(self) -> None:
        self.stopped = True
        if self.task:
            self.task.cancel()
            with suppress(asyncio.CancelledError):
                await self.task
        self.task = None
        self.stream = None
        self._set_state("stopped")

    async def _run(self) -> None:
        while not self.stopped:
            self._set_state("resuming" if self.resume_token else "starting")
            try:
                options = {"full_document": "updateLookup", "max_await_time_ms": 1_000}
                if self.resume_token:
                    options["resume_after"] = self.resume_token
                async with await self.db.watch(self.pipeline(), **options) as stream:
                    self.stream = stream
                    self.attempts = 0
                    self._set_state("live")
                    async for change in stream:
                        await self._handle_change(change)
                if not self.stopped:
                    raise RuntimeError("Change stream ended unexpectedly")
            except asyncio.CancelledError:
                raise
            except Exception as error:
                self.stream = None
                if self.stopped:
                    return
                if getattr(error, "code", None) in NON_RESUMABLE_CODES:
                    self.resume_token = None
                    self._publish("resync", {"reason": redact(error)})
                self.attempts += 1
                delay = min(self.max_backoff_seconds, 2 ** min(self.attempts - 1, 5))
                self._set_state("reconnecting", f"{redact(error)} (retry in {round(delay)}s)")
                await asyncio.sleep(delay)

    @staticmethod
    def pipeline() -> list[dict]:
        return [{"$match": {"ns.coll": {"$in": list(WATCHED_COLLECTIONS)}, "operationType": {"$in": ["insert", "update", "replace", "delete"]}}}]

    async def _handle_change(self, change: dict) -> None:
        self.resume_token = change.get("_id") or getattr(self.stream, "resume_token", None)
        if self.state["state"] != "live":
            self.attempts = 0
            self._set_state("live")
        self._publish("change", summarize_change(change))

    def _set_state(self, name: str, error: str | None = None) -> None:
        self.state = self._state(name, error)
        self._publish("stream-status", self.state)

    def _publish(self, event: str, data: dict) -> None:
        for queue in tuple(self.subscribers):
            try:
                queue.put_nowait((event, data))
            except asyncio.QueueFull:
                with suppress(asyncio.QueueEmpty):
                    queue.get_nowait()
                queue.put_nowait(("resync", {"reason": "Live-update subscriber fell behind; reload current views."}))