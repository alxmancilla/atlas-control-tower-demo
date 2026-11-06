# FastAPI implementation

This directory is the in-progress Python recreation of the tagged Node `0.1` reference application. It uses the same non-production Atlas demo database and does not yet serve the browser UI.

## Run

From this directory, use the root `.env` without copying its credentials:

`uv run --env-file ../.env python -m app.run`

The Python service defaults to port `8000`; set `PYTHON_PORT` to override it. It enforces the same Atlas-only and demo-database-name guards as the Node reference.

## Implemented endpoints

- `GET /health`
- `GET /api/status`
- `GET /api/profiles`
- `GET /api/overview?profile=retail`
- `GET /api/portfolio?profile=retail`
- `GET /api/kpis?profile=retail[&fresh=true]`
- `GET /api/exceptions?profile=retail`
- `POST /api/events?profile=retail`
- `POST /api/exceptions/{exceptionId}/action?profile=retail`
- `POST /api/exceptions/{exceptionId}/recommendation?profile=retail`

Event submission validates its source, milestone, and category against the selected profile. It stores an event and its exception work item in a multi-document transaction, suppresses duplicate source events using the Node-compatible `idempotencyKey`, and keeps a bounded exception audit trail. Operator actions support acknowledge, assign, note, resolve, and one-time accept/decline decisions for seeded recommendations.

The overview and portfolio routes run their own MongoDB aggregations. The KPI route materializes one profile snapshot with `$merge` when it is missing or older than 15 seconds; add `fresh=true` to force immediate refresh. If the database is not initialized, it retains the original read-only live fallback. Setup/reset, Search, telemetry, change streams, and the UI are later ports.