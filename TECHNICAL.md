# Technical reference

This is the sole application and administration runtime for the non-production Atlas demo. It serves a Python-native FastAPI dashboard on `0.0.0.0:8000` (open `http://localhost:8000/` locally) and owns setup, reset, Search-index administration, operational workflows, and verification.

## Run

From the project root:

`uv run --env-file .env python -m app.run`

Use `uv run --env-file .env control-tower setup` to provision the non-production database, `control-tower search-status` to inspect Atlas Search readiness, and `control-tower reset --confirm <demo-database-name>` for an explicitly confirmed reset.

The Python service defaults to `0.0.0.0:8000`; set `HOST` or `PYTHON_PORT` to override it (`PORT` is not read). The provided `.env.example` sets `HOST=127.0.0.1`, which keeps the dashboard local-only and needs no admin token. When `HOST` is a network address, set `ADMIN_TOKEN` before starting; the service refuses to start without it. `GET /api/status` reports `adminTokenRequired`, and the dashboard only prompts for a token when it is `true`. Loopback hosts are allowed by default; add a LAN hostname or IP address (without a port) to `ALLOWED_HOSTS` before using the dashboard from another device. It enforces Atlas-only and demo-database-name guards. The browser page is deliberately small: its stylesheet is served from `app/static/dashboard.css`, while the page keeps the interaction code next to the DOM contract in `app/static/index.html`. Dynamic operational values remain HTML-escaped before rendering.

## Opt-in live operator verification

The ordinary Python tests do not contact Atlas. For a complete isolated journey, run the following from the project root. It creates and drops only the explicitly named disposable database; the name must end in `_operator_verify`, and the confirmation must repeat it exactly.

`uv run --env-file .env python verify_operator_journey.py --database atlas_control_tower_demo_operator_verify --confirm-cleanup atlas_control_tower_demo_operator_verify`

The verifier uses the Python administration CLI to seed the temporary database, starts FastAPI on port `8011`, and checks initialization, event deduplication, receipt-driven impact refresh, recommendation acceptance and the simulated outbox lifecycle, re-anchoring, telemetry, and observe/enforce schema validation. Because re-anchoring skips data seeded less than a minute earlier, the verifier first moves the temporary database's retail anchor back one hour. It then stops the server and removes the temporary database even when a check fails. It does not accept or print credentials.

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
- `GET /api/data-quality?profile=retail` (reliability- and source-contract-derived quality queue)
- `GET /api/execution-outbox?profile=retail` (internal simulated handoff queue)
- `GET /api/disruption-trace?profile=retail` (bounded profile-specific cross-domain trace)
- `GET /api/validation` (validation stage, capability gate, and bounded invalid-document list)
- `GET /api/telemetry?profile=retail[&hours=8&min=1&max=4]`
- `GET /api/search/status`
- `GET /api/search?q=traffic&profile=retail`
- `GET /api/exceptions?profile=retail` (queue ranked by a per-profile priority score; each item includes `priority.score`, `priority.reasons`, and `priority.minutesToSla`)
- `GET /api/exceptions/{exceptionId}/similar`
- `POST /api/events?profile=retail`
- `POST /api/events/sample?profile=retail` (`{"sampleId":"arrived_dc"}`)
- `POST /api/validation/{stage,invalid,valid,repair,remove}` (maintenance-locked validation demonstration)
- `POST /api/reanchor` (maintenance-locked synthetic timestamp reanchor)
- `POST /api/telemetry/reading?profile=retail` (`{"kind":"normal"}` or `{"kind":"excursion"}`)
- `POST /api/exceptions/{exceptionId}/action?profile=retail`
- `POST /api/data-quality/{issueId}/action?profile=retail` (`acknowledge` or `resolve` with a note)
- `POST /api/exceptions/{exceptionId}/recommendation?profile=retail`
- `POST /api/execution-outbox/{executionId}/action?profile=retail` (`submit`, `acknowledge`, `fail`, or `reconcile`)

Event submission validates its source, milestone, and category against the selected profile. It stores an event, any exception work item, and reliability- or source-contract-derived quality work items in a multi-document transaction; duplicate source events use the stable `idempotencyKey` and update the original event's deduplicated quality issue. Every canonical event has a derived `high`, `medium`, or `low` confidence state. Declared upstream supplier signals additionally receive versioned `sourceContract` evidence: required correlation keys, mapped/incomplete status, and freshness relative to a source-specific lag target. Missing required keys create a high-severity `source_contract_incomplete` work item without changing confidence rules. Adapters—not this API—are responsible for mapping arbitrary partner payloads into canonical `details` fields. Source-quality actions support acknowledge and audited resolve; they record stewardship intent only and never write back to a carrier, supplier, ERP, WMS, or TMS.

Source-contract evidence is created at ingestion. Reset the non-production demo database to display it for existing seeded trace signals; newly submitted samples receive it without a reset.

Accepting a recommendation creates an idempotent `execution_outbox` request in the same transaction as the decision. Its `simulated` connector records only a bounded recommendation/decision snapshot; it has no external URL, credential, adapter, or write-back capability. Operators can transition it from `pending` to `submitted`, then to `acknowledged` or `failed`, and finally `reconciled`; failures and reconciliations require an audit note. The FastAPI dashboard displays and controls this simulated lifecycle through the normal guarded POST requests, and the application Change Stream watches outbox changes with the other operational collections. Run `control-tower setup` after upgrading an existing demo database to add missing collections and indexes; no reset is required.

The overview and portfolio routes run their own MongoDB aggregations. The overview includes a `$facet` reliability summary, and the timeline lists up to 300 events for the profile's primary entity. Every profile trace embeds a bounded synthetic four-node topology and returns only its linked late source signal: retail supplier → DC → shipment → store promotion, healthcare reagent supplier → hub → courier → lab, and manufacturing component supplier → dock → quality → production line. These traces demonstrate operational impact correlation, not live network onboarding, execution, patient/clinical data processing, or production-schedule automation. The fixed per-profile sample catalog is exposed in profile metadata; sample submission accepts only its ID, derives the complete synthetic payload server-side, and uses the same transaction and duplicate-suppression path as a regular event. The validation panel has fixed legacy-invalid samples and a staged MongoDB `$jsonSchema` workflow: flexible, observe/warn, enforce/error, and (MongoDB 9.0+) constraint. Every validation mutation holds the 10-minute maintenance lease; repair/remove accepts only `demo.invalidSample` records. The KPI route materializes one profile snapshot with `$merge` when it is missing or older than 15 seconds; add `fresh=true` to force immediate refresh. The telemetry route uses the `telemetry_readings` time-series collection to calculate a six-reading moving average, range excursions, and summary statistics. The application-scoped Change Stream watches events, exceptions, source-quality issues, execution-outbox requests, entities, and maintenance state, then broadcasts compact summaries over SSE. It retains resume tokens only while the server runs, reconnects with bounded backoff, and asks clients to resync after non-resumable failures. `POST /api/reanchor` is protected by the normal local-host, JSON, and optional admin-token guards, proves that the demo is initialized, shifts synthetic timestamps while holding a 10-minute lease, then regenerates time-series telemetry. It never creates collections or indexes.

Search routes are read-only. They report existing Atlas Search and Automated Embedding index readiness, and they refuse queries until the corresponding indexes are queryable. The web service never creates or modifies search indexes; the administration CLI does: `control-tower setup` and `reset` request them unless `--without-search` is given, and `control-tower search-create` requests any that are missing.