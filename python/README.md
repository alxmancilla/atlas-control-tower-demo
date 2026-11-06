# FastAPI implementation

This directory is the in-progress Python recreation of the tagged Node `0.1` reference application. It uses the same non-production Atlas demo database and serves a Python-native dashboard at `http://127.0.0.1:8000/`.

## Run

From this directory, use the root `.env` without copying its credentials:

`uv run --env-file ../.env python -m app.run`

The Python service defaults to port `8000`; set `PYTHON_PORT` to override it. It enforces the same Atlas-only and demo-database-name guards as the Node reference.

## Implemented endpoints

- `GET /health`
- `GET /` (Python-native browser dashboard)
- `GET /api/status`
- `GET /api/stream` (Server-Sent Events)
- `GET /api/profiles`
- `GET /api/overview?profile=retail`
- `GET /api/portfolio?profile=retail`
- `GET /api/kpis?profile=retail[&fresh=true]`
- `GET /api/telemetry?profile=retail[&hours=8&min=1&max=4]`
- `GET /api/search/status`
- `GET /api/search?q=traffic&profile=retail`
- `GET /api/exceptions?profile=retail`
- `GET /api/exceptions/{exceptionId}/similar`
- `POST /api/events?profile=retail`
- `POST /api/telemetry/reading?profile=retail` (`{"kind":"normal"}` or `{"kind":"excursion"}`)
- `POST /api/exceptions/{exceptionId}/action?profile=retail`
- `POST /api/exceptions/{exceptionId}/recommendation?profile=retail`

Event submission validates its source, milestone, and category against the selected profile. It stores an event and its exception work item in a multi-document transaction, suppresses duplicate source events using the Node-compatible `idempotencyKey`, and keeps a bounded exception audit trail. Operator actions support acknowledge, assign, note, resolve, and one-time accept/decline decisions for seeded recommendations.

The overview and portfolio routes run their own MongoDB aggregations. The KPI route materializes one profile snapshot with `$merge` when it is missing or older than 15 seconds; add `fresh=true` to force immediate refresh. The telemetry route uses the existing `telemetry_readings` time-series collection to calculate a six-reading moving average, range excursions, and summary statistics. The application-scoped Change Stream watches events, exceptions, entities, and maintenance state, then broadcasts compact summaries over SSE. It retains resume tokens only while the server runs, reconnects with bounded backoff, and asks clients to resync after non-resumable failures. The dashboard intentionally exposes only the Python-implemented safe workflows; Node-only setup/reset, reanchor, validation, and sample-event controls are not shown.

Search routes are read-only. They report existing Atlas Search and Automated Embedding index readiness, and they refuse queries until the corresponding indexes are queryable. This Python service does not create or modify search indexes.