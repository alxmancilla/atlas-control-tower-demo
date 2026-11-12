"""Safe operational commands for the Python-only Atlas Control Tower demo."""
from __future__ import annotations

import argparse
import asyncio
from datetime import datetime
import json

from bson import ObjectId
from pymongo import AsyncMongoClient

from .config import Settings, redact
from .maintenance import with_maintenance
from .readiness import initialization_status
from .reanchor import reanchor_demo
from .search import create_search_indexes, search_status
from .setup import reset_demo, setup_demo
from .validation import validation_state


def arguments(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Administer the non-production Atlas Control Tower demo.")
    parser.add_argument("command", choices=("setup", "reset", "reanchor", "check", "search-create", "search-status"))
    parser.add_argument("--confirm", help="Required for reset; repeat DEMO_DB_NAME exactly")
    parser.add_argument("--without-search", action="store_true", help="Do not request Atlas Search indexes during setup or reset")
    return parser.parse_args(argv)


def _json(value):
    if isinstance(value, (datetime, ObjectId)):
        return str(value)
    raise TypeError(f"Cannot serialize {value.__class__.__name__}")


async def execute(settings: Settings, args: argparse.Namespace) -> dict:
    client = AsyncMongoClient(settings.mongodb_uri, appname="atlas-control-tower-fastapi-admin", serverSelectionTimeoutMS=15_000)
    try:
        db = client[settings.db_name]
        await db.command("ping")
        if args.command == "setup":
            return await with_maintenance(db, "setup", lambda: setup_demo(db, with_search=not args.without_search))
        if args.command == "reset":
            if args.confirm != settings.db_name:
                raise ValueError(f"Reset not confirmed. Use: control-tower reset --confirm {settings.db_name}")
            return await with_maintenance(db, "reset", lambda: reset_demo(db, args.confirm, with_search=not args.without_search))
        if args.command == "reanchor":
            return {"profiles": await with_maintenance(db, "reanchor", lambda: reanchor_demo(db))}
        if args.command == "search-create":
            return {"created": await create_search_indexes(db), "status": await search_status(db)}
        if args.command == "search-status":
            return await search_status(db)
        readiness = await initialization_status(db)
        return {"init": readiness, "search": await search_status(db), "validation": await validation_state(db) if readiness["initialized"] else None}
    finally:
        await client.close()


def main(argv: list[str] | None = None) -> None:
    args, settings = arguments(argv), Settings.from_env()
    try:
        result = asyncio.run(execute(settings, args))
    except Exception as error:
        raise SystemExit(f"Error: {redact(error)}") from error
    print(json.dumps(result, default=_json, indent=2))


if __name__ == "__main__":
    main()