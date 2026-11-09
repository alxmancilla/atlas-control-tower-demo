# FastAPI implementation

This directory is the FastAPI recreation of the tagged Node `0.1` reference application. It uses the same non-production Atlas demo database and serves a Python-native dashboard at `http://127.0.0.1:8000/`. The Node implementation remains the reference for setup/reset and its original UI; FastAPI implements the operational read/write, Change Stream, validation, reanchor, Search-read, and dashboard workflows without modifying that implementation.

## Run

From this directory, use the root `.env` without copying its credentials:

`uv run --env-file ../.env python -m app.run`

The Python service defaults to port `8000`; set `PYTHON_PORT` to override it. It enforces the same Atlas-only and demo-database-name guards as the Node reference.

The browser page is deliberately small: its stylesheet is served from `app/static/dashboard.css`, while the page keeps the interaction code next to the DOM contract in `app/static/index.html`. Dynamic operational values remain HTML-escaped before rendering. If `ASP_TELEMETRY_ENABLED=true`, the existing telemetry response also reads recent `telemetry_rollups` written by the optional Kafka-to-Atlas Stream Processing pipeline; direct time-series telemetry remains its fallback. Processor deployment is documented in [`../docs/atlas-stream-processing.md`](../docs/atlas-stream-processing.md).

## Opt-in live operator verification

The ordinary Python tests do not contact Atlas. For a complete isolated journey, run the following from this directory. It creates and drops only the explicitly named disposable database; the name must end in `_operator_verify`, and the confirmation must repeat it exactly.

`uv run --env-file ../.env python verify_operator_journey.py --database atlas_control_tower_demo_operator_verify --confirm-cleanup atlas_control_tower_demo_operator_verify`

The verifier uses the existing root Node setup command to seed the temporary database, starts FastAPI on port `8011`, checks initialization, sample-event deduplication, telemetry, and observe/enforce schema validation, then stops the server and removes the temporary database even when a check fails. It does not accept or print credentials.

## Implemented endpoints

- `GET /health`
- `GET /` (Python-native browser dashboard)
- `GET /api/status`
- `GET /api/readiness` (read-only collection, index, profile-seed, and clock diagnostics)
- `GET /api/stream` (Server-Sent Events)
- `GET /api/profiles`
- `GET /api/overview?profile=retail`
- `GET /api/portfolio?profile=retail`
- `GET /api/kpis?profile=retail[&fresh=true]`
- `GET /api/timeline?profile=retail`
- `GET /api/validation` (validation stage, capability gate, and bounded invalid-document list)
- `GET /api/telemetry?profile=retail[&hours=8&min=1&max=4]`
- `GET /api/search/status`
- `GET /api/search?q=traffic&profile=retail`
- `GET /api/exceptions?profile=retail`
- `GET /api/exceptions/{exceptionId}/similar`
- `POST /api/events?profile=retail`
- `POST /api/events/sample?profile=retail` (`{"sampleId":"arrived_dc"}`)
- `POST /api/validation/{stage,invalid,valid,repair,remove}` (maintenance-locked validation demonstration)
- `POST /api/reanchor` (maintenance-locked synthetic timestamp reanchor)
- `POST /api/telemetry/reading?profile=retail` (`{"kind":"normal"}` or `{"kind":"excursion"}`)
- `POST /api/exceptions/{exceptionId}/action?profile=retail`
- `POST /api/exceptions/{exceptionId}/recommendation?profile=retail`

Event submission validates its source, milestone, and category against the selected profile. It stores an event and its exception work item in a multi-document transaction, suppresses duplicate source events using the Node-compatible `idempotencyKey`, and keeps a bounded exception audit trail. Operator actions support acknowledge, assign, note, resolve, and one-time accept/decline decisions for seeded recommendations.

The overview and portfolio routes run their own MongoDB aggregations. The overview includes a `$facet` reliability summary, and the timeline lists up to 300 events for the profile's primary entity. The fixed per-profile sample catalog is exposed in profile metadata; sample submission accepts only its ID, derives the complete synthetic payload server-side, and uses the same transaction and duplicate-suppression path as a regular event. The validation panel has fixed legacy-invalid samples and a staged MongoDB `$jsonSchema` workflow: flexible, observe/warn, enforce/error, and (MongoDB 9.0+) constraint. Every validation mutation holds the 10-minute maintenance lease; repair/remove accepts only `demo.invalidSample` records. The KPI route materializes one profile snapshot with `$merge` when it is missing or older than 15 seconds; add `fresh=true` to force immediate refresh. The telemetry route uses the existing `telemetry_readings` time-series collection to calculate a six-reading moving average, range excursions, and summary statistics. With `ASP_TELEMETRY_ENABLED=true`, it additionally returns recent processor-derived `telemetry_rollups` for the selected profile; when no rollups exist it reports that state without hiding direct data. The application-scoped Change Stream watches events, exceptions, entities, rollups, and maintenance state, then broadcasts compact summaries over SSE. It retains resume tokens only while the server runs, reconnects with bounded backoff, and asks clients to resync after non-resumable failures. `POST /api/reanchor` is protected by the normal local-host, JSON, and optional admin-token guards, proves that the demo is initialized, shifts synthetic timestamps while holding a 10-minute lease, then regenerates time-series telemetry. It never creates collections or indexes.

Search routes are read-only. They report existing Atlas Search and Automated Embedding index readiness, and they refuse queries until the corresponding indexes are queryable. This Python service does not create or modify search indexes.