"""Opt-in live verification against a disposable Atlas demo database.

The caller supplies Atlas configuration through the environment (usually uv's
--env-file option). Credentials are never accepted as command-line arguments.
"""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import subprocess
import sys
import time
from urllib.error import HTTPError
from urllib.request import Request, urlopen

from pymongo import AsyncMongoClient

from app.config import Settings, assert_demo_db_name
from app.db import COLLECTIONS


ROOT = Path(__file__).resolve().parent.parent
VERIFY_SUFFIX = "_operator_verify"


def arguments(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run the destructive, isolated FastAPI operator journey.")
    parser.add_argument("--database", required=True, help=f"Disposable demo database ending in {VERIFY_SUFFIX}")
    parser.add_argument("--confirm-cleanup", required=True, metavar="DATABASE", help="Repeat the database name to permit setup and cleanup")
    parser.add_argument("--port", type=int, default=8011)
    parsed = parser.parse_args(argv)
    database = assert_demo_db_name(parsed.database)
    if not database.endswith(VERIFY_SUFFIX):
        parser.error(f"--database must end in {VERIFY_SUFFIX}")
    if parsed.confirm_cleanup != database:
        parser.error("--confirm-cleanup must exactly match --database")
    return parsed


def call(base_url: str, method: str, path: str, body: dict | None = None, admin_token: str | None = None) -> tuple[int, dict]:
    headers = {"Content-Type": "application/json"}
    if admin_token:
        headers["X-Admin-Token"] = admin_token
    request = Request(f"{base_url}{path}", data=json.dumps(body).encode() if body is not None else None, method=method, headers=headers)
    try:
        with urlopen(request, timeout=20) as response:
            return response.status, json.loads(response.read())
    except HTTPError as error:
        return error.code, json.loads(error.read())


def require(status: int, payload: dict, expected: int = 200) -> dict:
    if status != expected:
        raise RuntimeError(f"Expected HTTP {expected}, got {status}: {payload.get('detail', payload)}")
    return payload


def recommendation_to_accept(queue: list[dict]) -> tuple[str, str]:
    for exception in queue:
        for action in exception.get("recommendedActions", []):
            if not exception.get("actionDecisions", {}).get(action.get("actionId")):
                return exception["exceptionId"], action["actionId"]
    raise RuntimeError("The delivery-delay exception did not receive an actionable recommendation")


def wait_for_server(base_url: str, admin_token: str | None) -> dict:
    last_error: Exception | None = None
    for _ in range(40):
        try:
            status, payload = call(base_url, "GET", "/api/status", admin_token=admin_token)
            if status == 200:
                return payload
        except Exception as error:
            last_error = error
        time.sleep(0.25)
    raise RuntimeError("FastAPI server did not become ready") from last_error


def verify(base_url: str, admin_token: str | None, before_reanchor=None) -> None:
    status = require(*call(base_url, "GET", "/api/status", admin_token=admin_token))
    if not status.get("connected") or not status.get("init", {}).get("initialized"):
        raise RuntimeError("Temporary database did not initialize")
    profiles = require(*call(base_url, "GET", "/api/profiles", admin_token=admin_token))
    if not any(item["id"] == "retail" for item in profiles["profiles"]):
        raise RuntimeError("Retail profile is required for the deterministic operator verification")
    profile = "retail"
    query = f"?profile={profile}"
    require(*call(base_url, "GET", f"/api/overview{query}", admin_token=admin_token))
    accepted = require(*call(base_url, "POST", f"/api/events/sample{query}", {"sampleId": "arrived_dc"}, admin_token))
    if accepted.get("outcome") not in {"accepted", "deduplicated"}:
        raise RuntimeError(f"Unexpected sample outcome: {accepted}")
    duplicate = require(*call(base_url, "POST", f"/api/events/sample{query}", {"sampleId": "arrived_dc"}, admin_token))
    if duplicate.get("outcome") != "deduplicated":
        raise RuntimeError("Duplicate source event was not suppressed")
    require(*call(base_url, "POST", f"/api/events/sample{query}", {"sampleId": "delay_alert"}, admin_token))
    exceptions = require(*call(base_url, "GET", f"/api/exceptions{query}", admin_token=admin_token))
    exception_id, action_id = recommendation_to_accept(exceptions["queue"])
    decision = require(*call(base_url, "POST", f"/api/exceptions/{exception_id}/recommendation{query}", {"actionId": action_id, "decision": "accepted", "operator": "operator.verify"}, admin_token))
    execution = decision.get("execution")
    if not execution or execution.get("status") != "pending" or execution.get("connector", {}).get("externalWrite") is not False:
        raise RuntimeError("Accepted recommendation did not create the expected simulated pending handoff")
    execution_id = execution["executionId"]
    for action, body in (("submit", {}), ("acknowledge", {}), ("reconcile", {"note": "Operator verification completed simulated reconciliation"})):
        result = require(*call(base_url, "POST", f"/api/execution-outbox/{execution_id}/action{query}", {"action": action, "operator": "operator.verify", **body}, admin_token))
    if result.get("status") != "reconciled":
        raise RuntimeError("Simulated outbox handoff did not reach its terminal reconciled state")
    outbox = require(*call(base_url, "GET", f"/api/execution-outbox{query}", admin_token=admin_token))
    if not any(item.get("executionId") == execution_id and item.get("status") == "reconciled" for item in outbox["queue"]):
        raise RuntimeError("Reconciled handoff was not returned by the execution queue")
    require(*call(base_url, "POST", f"/api/events/sample{query}", {"sampleId": "received"}, admin_token))
    overview = require(*call(base_url, "GET", f"/api/overview{query}", admin_token=admin_token))
    if overview.get("businessImpact", {}).get("level") != "none":
        raise RuntimeError("Receipt did not refresh business impact to none")
    if before_reanchor:
        before_reanchor()
    reanchored = require(*call(base_url, "POST", "/api/reanchor", {}, admin_token))
    if not any(item.get("profile") == profile and "businessImpact" in item for item in reanchored.get("profiles", [])):
        raise RuntimeError("Re-anchor did not report refreshed retail business impact")
    require(*call(base_url, "POST", f"/api/telemetry/reading{query}", {"kind": "normal"}, admin_token))
    require(*call(base_url, "POST", f"/api/telemetry/reading{query}", {"kind": "excursion"}, admin_token))
    require(*call(base_url, "POST", f"/api/validation/stage{query}", {"stage": "observe"}, admin_token))
    invalid = require(*call(base_url, "POST", f"/api/validation/invalid{query}", {"sampleId": "legacy_missing_entity"}, admin_token))
    if not invalid.get("ok"):
        raise RuntimeError(f"Observe-stage invalid sample was rejected: {invalid}")
    validation = require(*call(base_url, "GET", "/api/validation", admin_token=admin_token))
    document_id = validation["invalid"]["docs"][0]["_id"]
    require(*call(base_url, "POST", f"/api/validation/repair{query}", {"id": document_id}, admin_token))
    require(*call(base_url, "POST", f"/api/validation/stage{query}", {"stage": "enforce"}, admin_token))
    probe = require(*call(base_url, "POST", f"/api/validation/valid{query}", {}, admin_token))
    if not probe.get("ok"):
        raise RuntimeError(f"Valid probe failed under enforcement: {probe}")


async def drop_database(settings: Settings) -> None:
    client = AsyncMongoClient(settings.mongodb_uri, serverSelectionTimeoutMS=15_000)
    try:
        await client.drop_database(settings.db_name)
    finally:
        await client.close()


async def age_demo_anchor(settings: Settings, profile: str = "retail", minutes: int = 60) -> None:
    # Re-anchor skips data seeded less than a minute ago, so age the disposable anchor first.
    client = AsyncMongoClient(settings.mongodb_uri, serverSelectionTimeoutMS=15_000)
    try:
        await client[settings.db_name][COLLECTIONS["entities"]].update_many({"profile": profile, "anchorAt": {"$type": "date"}}, [{"$set": {"anchorAt": {"$dateSubtract": {"startDate": "$anchorAt", "unit": "minute", "amount": minutes}}}}])
    finally:
        await client.close()


def main(argv: list[str] | None = None) -> None:
    args = arguments(argv)
    base_settings = Settings.from_env()
    settings = Settings(base_settings.mongodb_uri, args.database, base_settings.default_profile, "127.0.0.1", args.port, base_settings.admin_token)
    environment = {**os.environ, "DEMO_DB_NAME": args.database, "PYTHON_PORT": str(args.port), "HOST": "127.0.0.1"}
    server: subprocess.Popen | None = None
    failed = False
    try:
        import asyncio
        asyncio.run(drop_database(settings))
        subprocess.run([sys.executable, "-m", "app.cli", "setup"], cwd=ROOT / "python", env=environment, check=True, stdout=subprocess.DEVNULL)
        server = subprocess.Popen([sys.executable, "-m", "app.run"], cwd=ROOT / "python", env=environment, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        wait_for_server(f"http://127.0.0.1:{args.port}", settings.admin_token)
        verify(f"http://127.0.0.1:{args.port}", settings.admin_token, before_reanchor=lambda: asyncio.run(age_demo_anchor(settings)))
        print("operator journey verified; temporary database will be removed")
    except BaseException:
        failed = True
        raise
    finally:
        if server:
            server.terminate()
            server.wait(timeout=15)
        import asyncio
        try:
            asyncio.run(drop_database(settings))
        except Exception:
            if not failed:
                raise


if __name__ == "__main__":
    main()