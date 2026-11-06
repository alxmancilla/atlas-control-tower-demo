# Atlas Control Tower Demo

A small operator console that runs against a **real MongoDB Atlas cluster**. It turns noisy operational signals into prioritized work and explainable operator decisions through four workflows:

1. **Portfolio prioritization**: active loads are ranked by business impact, open work, and SLA urgency; one primary load offers the full interactive drill-down.
2. **Exception-to-decision workflow**: source events open, deduplicate, and link work items; operators acknowledge, assign, and resolve them, and can accept or decline explainable recommended actions with an audit trail.
3. **Milestone risk**: planned-versus-actual milestones with transparent, rules-based risk (a rules estimate, not a prediction model).
4. **Event reliability**: duplicate, late, out-of-order, and malformed events are labeled, and they never move the timeline backward.

Three synthetic domain profiles share the same workflow: **retail** (store replenishment shipment), **healthcare** (temperature-controlled lab supply transfer; operational data only, no patient data), and **manufacturing** (inbound component lot). All data is synthetic.

## Prerequisites

- Node.js 22 or later (uses `--env-file-if-exists` and `node:test`).
- A **non-production** Atlas cluster. M10+ is recommended. Free and Flex tiers have limits on Search index count and some commands, and the results below were not verified on them.
- A database user with `readWrite` and `dbAdmin` on the demo database. `collMod` needs `dbAdmin`.
- Your IP address in Atlas **Network Access**.
- For the 4th validation stage (`validationLevel: "constraint"`): a cluster on **MongoDB 9.0 or later**. On earlier versions, the stage is disabled in the UI and the reason is shown.
- For **similar past cases** (Vector Search with Automated Embedding): on M10+ clusters, **storage auto-scaling must be enabled**. Atlas generates the embeddings with the `voyage-4` model; embedding the index and each lookup consume tokens from your organization's free allocation (200M per model, then billed). If the index can't be built, the rest of the demo works and the panel explains why.

## Setup

```bash
npm install
cp .env.example .env          # set MONGODB_URI (Atlas only; *.mongodb.net is required)
npm run setup                 # collections, indexes, time-series collection, seed data, Search and Vector Search indexes
npm run search:status         # wait until the three indexes report queryable: true
npm start                     # http://localhost:3000
```

### Python / FastAPI recreation (in progress)

The tagged Node application remains the complete `0.1` reference. A parallel Python implementation lives in [`python/`](python/); it uses FastAPI and PyMongo Async against the **same non-production Atlas demo database** without changing the existing Node code or UI. It provides safe read APIs for status, profiles, overview, portfolio, materialized KPIs (use `fresh=true` to force refresh), and the exception queue, plus transactional event ingestion and auditable operator-exception/recommendation decisions. It does not yet implement setup/reset, Search, telemetry, change streams, or serve the browser UI.

It requires Python 3.12+ and [uv](https://docs.astral.sh/uv/). From the `python/` directory, run `uv run --env-file ../.env python -m app.run`; it listens on `127.0.0.1:8000` by default (override with `PYTHON_PORT`). See [`python/README.md`](python/README.md) for its current endpoint contract and tests.

`DEMO_DB_NAME` must contain `demo`. Setup, seed, and reset only touch that database. Setup is idempotent: it creates what is missing and never drops anything. The UI also offers **Initialize demo data** when the database is missing collections, indexes, or a profile's seed data.

Setup inserts seed documents only when they're absent, so **after editing a profile file, run reset** to see the changes; setup won't rewrite existing seeded documents.

Setup, reset, and re-anchor (from the UI or the CLI) hold a maintenance lock in `demo_state`. While it's held, the page shows a banner, pauses refreshes, and disables buttons, so you never see half-updated data. Other API calls return 503 until it finishes. A lock left by a crashed process expires after 10 minutes.

Reset drops and recreates only the demo collections, and it requires confirmation:

```bash
npm run reset -- --confirm atlas_control_tower_demo
```

**Before presenting, re-anchor.** Seeded times are relative to when setup ran, so hours later every milestone looks missed and telemetry falls outside the 8-hour window. Re-anchoring shifts event, milestone, SLA, audit, and portfolio trusted-signal times so the seed moment becomes "now". Every gap between them stays the same, and your changes are kept. Telemetry is regenerated, because time-series collections don't allow updating the time field. The UI offers **Re-anchor to now** once the data is more than 30 minutes old.

```bash
npm run reanchor
```

## Data model

| Collection | Purpose |
|---|---|
| `tracked_entities` | One document per tracked load, with the planned milestones and the order lines (one per stop on a multi-stop route) embedded. Each profile seeds one fully interactive primary load plus three clearly marked synthetic active-load portfolio records |
| `operational_events` | Shared envelope (`trackedEntityId`, `eventType`, `eventTime`, `receivedAt`, `source`, `channel`, `sourceEventId`, `idempotencyKey`) plus a flexible `details` object per source, with the original message in `details.raw`, and a computed `reliability` sub-document |
| `exceptions` | One work item per entity + category (`exceptionKey`), with severity, SLA, owner, related events, a copy of the current business impact, explainable recommended actions, immutable operator decisions on those actions, and an embedded audit trail capped at the newest 100 entries (`$push` with `$slice`). Also holds a synthetic history of resolved cases from earlier shipments (`demo.history`), which the queue excludes and similar-case lookup searches |
| `inventory_positions` | Stock at a location for an item (on hand, consumption rate), one per `locationId` + `itemRef`; one per stop on the load, and the business-impact join target. Kept separate from the load because stock changes independently of it |
| `kpi_snapshots` | Materialized view: one KPI document per profile, written by an aggregation with `$merge` |
| `telemetry_readings` | **Time-series** collection (`timeField: ts`, `metaField: meta`) |
| `demo_state` | The persisted validation stage, its history, and the maintenance lock |

**One collection, many source formats.** Each event records its `channel` (`edi`, `api`, `wms_scan`, `gps`, `iot`, `manual`, `batch_file`) and keeps the source's own payload in `details.raw`: an EDI 214 or 856 segment string, a WMS scan record, a GPS ping, an IoT logger reading, a webhook body, or a CSV row. The validator enforces the shared envelope and leaves `details` free, so a new source needs no schema migration. Expand **raw** on any timeline row to see it. Raw timestamps are written at ingest; re-anchoring shifts the envelope times but leaves raw payloads as received.

Index reasons appear in the UI (**Atlas capabilities → Indexes**) and in `src/db.js`. Two unique indexes carry the reliability guarantees. `idempotencyKey` stops a duplicate source event from being stored twice. `exceptionKey` stops repeated reports from opening a second work item.

## How the reliability rules work

- **Duplicates**: an insert that hits the unique `idempotencyKey` index increments `reliability.duplicateCount` on the original event. It never creates a second event or exception.
- **Late**: `receivedAt - eventTime` exceeds the profile's `lateArrivalMin`.
- **Out of order**: the `eventTime` is older than the newest stored event for the entity.
- **Milestone actual time**: the aggregation (`src/services/overview.js`) takes the *earliest* report per milestone, and status is derived from milestone *sequence*. A late or re-sent event can therefore add a report, but it cannot move the timeline backward.
- **Milestone risk** (`src/domain/milestoneRisk.js`): `completed_late` beyond tolerance; `missed` past planned time plus grace, or skipped while a later milestone is complete; `at_risk` near due, inside grace, carrying upstream lateness, or over the dwell limit. Each status lists its reasons.
- **Business impact** (`src/domain/impact.js`): for each stop on the load, hours of cover (on hand ÷ consumption per hour) compared with that stop's projected arrival (the final milestone's projected time plus the stop's `stopOffsetMin`). A shortfall is `high`, a shortfall beyond the profile's critical threshold is `critical`, and a thin margin is `watch`. The load takes the worst stop's level and sums the units short across stops. Each level lists its reasons, for example "3 h of cover; Arrival at Store #1187 Riverside projected in 3.8 h; 0.8 h short: shelf empty at Store #1187 Riverside during the weekend promo".
- **Recommended actions** (`src/domain/recommendations.js`): each `high` or `critical` stop receives an explainable **Expedite delivery** recommendation. It states the affected order, the stockout deadline, units and hours short, and the minimum arrival improvement needed to avoid the projected shortfall. It is an operator decision aid, not automation: accept or decline it in the exception detail. A decline requires a reason; either decision is stored once in `actionDecisions` and added to the exception audit trail. Fresh impact can refresh the recommendation without overwriting the human decision.
- **Exception priority** (`src/domain/rules.js`): severity points + SLA breached or SLA soon + category impact + business impact level, minus a small amount for acknowledged work. Hover a score in the UI to see every rule applied. Thresholds live in each profile file.

## Atlas capabilities shown

- **Multi-document ACID transactions**: each ingested event and its exception work item commit together or not at all (`ingestEvent` in `src/services/events.js`). If opening the exception fails, the event insert is rolled back. A duplicate aborts the insert transaction, and a second transaction records the duplicate on the original. Change events from a transaction carry its `txnNumber`, shown as a **txn** badge in the activity log, so you can see the event and exception arrive as one unit.
- **Computed pattern with `$lookup`**: the overview pipeline joins `inventory_positions` with the array field `orderLines.destination.id` as `localField`, so one `$lookup` fetches the stock at every stop, and a sub-pipeline keeps only the items on the load. The business-impact result (per stop and for the load) is stored on write on the tracked entity, and a summary is copied to its open exceptions. The exception queue ranks by impact without a join. The stored value is refreshed after every ingested event, after setup and re-anchor, and when it's more than 5 minutes old at read time. The UI shows it in **Downstream impact**.
- **Materialized view with `$merge`** (`src/services/kpis.js`): one aggregation per profile computes milestone adherence, late, out-of-order, and duplicate rates, reliability per source, open and SLA-breached exceptions, mean time to resolve (including the resolved history), and stops at risk. It writes the result to `kpi_snapshots` with `$merge` (`whenMatched: "replace"`). Reads return the stored snapshot while it is less than 15 seconds old and recompute it otherwise. The page recomputes it when the change stream reports new events or exception changes, and **Recompute** forces a refresh (`GET /api/kpis?fresh=1`). This is an on-demand materialized view: MongoDB does not refresh it automatically.
- **Decision outcomes in the KPI view**: the same materialized aggregation counts recommendations, recorded decisions, accepted actions, and declines for the current load. **Action adoption** is accepted decisions as a percentage of decided recommendations; it is a workflow-adoption signal, not proof that delivery was actually expedited. Production integrations would confirm the operational outcome with a carrier/TMS event.
- **Portfolio aggregation** (`src/services/portfolio.js`): the **Active load portfolio** starts with every `tracked_entities` document for the selected profile and uses `$lookup` to attach each load's nearest open exception. It ranks critical/high/watch business impact ahead of healthy loads, then breaks ties by open work and SLA. The primary load below is the full event-to-decision drill-down; the other three rows are deliberately labeled synthetic portfolio context, so the demo does not claim to model an entire WMS/TMS integration.
- **Change streams**: one database-level stream (`src/services/changeFeed.js`) with resume tokens and exponential-backoff reconnection, pushed to the browser over Server-Sent Events. Views refresh from the stream, not from button clicks. The header pill shows `live`, `reconnecting`, or `resuming`. Resume tokens are kept in memory, so they cover network blips while the server runs; after a server restart, the stream starts from "now" and the page reloads its data instead of replaying missed changes.
- **MongoDB Search**: `$search` with fuzzy matching, highlights, and token filters by profile, source, and status, over event descriptions and notes and over exception summaries and resolution notes. Search stays disabled until Atlas reports both indexes `queryable: true`. The UI then shows MISSING, building, or UNAVAILABLE with the reason. The index definitions are in `SEARCH_INDEXES` in `src/services/search.js`, and you can paste them into the Atlas UI JSON editor if you prefer to create them manually.
- **Vector Search with Automated Embedding**: the `ops_exceptions_similar` index (`type: "vectorSearch"`) declares `summary` as an `autoEmbed` field with the `voyage-4` model, plus `status` and `profile` filter fields. Atlas generates and maintains the embeddings, including for new and updated exceptions, so the app contains no embedding code. Opening an exception runs `$vectorSearch` with the exception's own summary as the query text, pre-filtered to resolved cases in the same profile, and shows the 3 closest cases with their resolutions. **Use this resolution** copies a past note into the resolve field. Matches are semantic, not keyword-based: "Carrier missed the scheduled pickup window" finds "Tractor never showed at the outbound dock". Note: Atlas rejected an `_id` pre-filter on this index on our cluster, so the lookup requests one extra result and drops the exception itself in code. Results are cached per exception in the page, because every lookup embeds the query text and uses tokens.
- **Time series**: a `$setWindowFields` moving average, range checks, and excursions. You can change the range in the UI. It is a demo threshold, not a compliance limit. Telemetry is queried on demand because **time-series collections do not support change streams or MongoDB Search**.
- **Schema validation**, applied to `operational_events` with `collMod`:
  1. **Flexible**: no validator.
  2. **Observe**: `validationAction: "warn"`. Invalid writes succeed. The server logs a warning, and the driver does not return it, so the UI uses the `{ $nor: [ { $jsonSchema } ] }` query to surface invalid records.
  3. **Enforce**: `validationAction: "error"`. New invalid writes are rejected with error code 121 and the server's `errInfo`. Existing invalid documents can remain.
  4. **Constraint** (9.0+): the demo runs `prepareConstraintValidationLevel: true`, then `validationLevel: "constraint"`. MongoDB scans the existing documents and fails if any violate the validator. In that case, the UI lists the violating documents so you can **Repair** or **Remove** them first. To leave constraint, the demo downgrades to `strict` before it changes the validator, as MongoDB requires.

**Atlas Stream Processing is not used.** Each event here needs a stateful, per-entity decision (dedupe, ordering, exception linking), which this app makes against the operational collections directly. Stream Processing would fit continuous windowed telemetry from Kafka. Note that it cannot read from time-series collections.

## Demo script (about 10 minutes, retail)

1. **Portfolio → overview**: start with **Active load portfolio**. It ranks four active loads by business impact and SLA urgency; the highlighted primary load is the one with a fully interactive event timeline below. The other rows provide clearly labeled synthetic WMS/TMS context. Then show the shipment's latest reliable milestone, next milestone, risk, open exceptions, and **Store shelf cover**. **Downstream impact** lists the three stores on the primary load, with each store's stock, projected arrival, and shortfall; later stops are hit harder by the same delay. **Control tower KPIs** shows the materialized snapshot.
2. **Live event flow**: click *Carrier: arrived at cross-dock*. The activity log shows the change-stream insert, then the milestone and timeline refresh. Expand **raw EDI** on the new row: the same collection holds EDI segments, WMS scans, GPS pings, and webhook bodies.
3. Click *Carrier: resend arrival (duplicate)*. The timeline shows "1 duplicate suppressed" and no new row.
4. Click *WMS: departure scan uploaded 3h late*. The event is labeled **out of order** and **late**, and "Departed" still shows the original actual time with 2 reports.
5. Click *Carrier: ETA slip alert*, then *Store: reports same delay*. Each click commits the event and the exception in one transaction (the **txn** badge in the activity log). One exception now has 2 related events and rises to the top of the queue, with its business impact in the score. Open it: **Recommended actions** identifies the specific store stockout, decision deadline, and required arrival improvement. Enter your name and click **Accept action** to record the decision in the audit trail; the **Action adoption** KPI updates. This records the operator's expedite decision—it does not pretend to dispatch a carrier. **Similar resolved cases** lists past delays and how they were handled. Click **Use this resolution**, acknowledge, assign, add a note, and resolve it. Later, *Carrier: out for delivery* (a GPS geofence ping) and *Store: received* bring the shelf cover back to `none`.
6. **Search** for `delayed replenishment` or `traffic` and point out the highlights.
7. **Telemetry**: click *Add out-of-range reading*, then *Run aggregation*. Tighten the range and run it again.
8. **Data quality**: go to **Observe** and submit a legacy event. It is accepted and listed as invalid. Go to **Enforce** and submit the other legacy event. It is rejected and the server error is shown. Repair or remove the invalid records, then click **Constraint** (9.0+). Optionally, try Constraint while an invalid record exists to see the scan fail.
9. Switch the profile to healthcare or manufacturing. The workflow is the same; only the vocabulary and data change.

## Testing

```bash
npm test                    # unit tests for the rules, schema transitions, profiles, and config (no network)
npm run test:integration    # against Atlas: uses TEST_DB_NAME (default atlas_control_tower_demo_test)
```

Integration tests **skip with an explicit message** when `MONGODB_URI` is not set. They never mock Atlas or use a local deployment. They reset only `TEST_DB_NAME`, which must contain `demo`. Before the first run, they do not create Search indexes, so the search test checks status reporting only.

## Troubleshooting

| Symptom | Fix |
|---|---|
| `querySrv ENOTFOUND` | The cluster host in `MONGODB_URI` is wrong |
| `bad auth` / authentication failed | Check the database user and password; URL-encode special characters |
| Server selection timed out | Add your IP address in Atlas Network Access |
| `Unauthorized` on stage buttons | The user needs `dbAdmin` on the demo database for `collMod` |
| Search disabled, "building" | Wait; check `npm run search:status` |
| Similar resolved cases says "building" or "missing" | Wait for `ops_exceptions_similar` to report queryable (about a minute for the demo data), or run `npm run search:create` |
| Similar resolved cases says "unavailable" or fails | Check storage auto-scaling (M10+), your organization's Automated Embedding policy, and token usage in Atlas → Search & Vector Search → Automated Embedding |
| "Maintenance in progress" banner that doesn't clear | Another setup, reset, or re-anchor is running; a lock from a crashed process expires after 10 minutes |
| Page unreachable from another machine | By design: set `HOST=0.0.0.0` and `ADMIN_TOKEN` to expose it |
| 415 or 403 from scripts calling the API | Send `Content-Type: application/json`, and no foreign `Origin` header |
| Search UNAVAILABLE | The cluster tier or user cannot manage Search indexes; create them in the Atlas UI |
| Constraint button disabled | The server is earlier than 9.0; the UI shows the reported version |
| Constraint fails with a validation error | Repair or remove the listed invalid documents, then retry |
| Every milestone missed, telemetry empty | Demo clock is stale: `npm run reanchor` or the UI banner button |
| Change stream `reconnecting` | It resumes automatically from the last resume token; if history was lost, it restarts and the page resyncs |

Connection strings are never logged. Errors show only the host name (see `redact`/`targetLabel` in `src/config.js`).

## Access and safety

The demo can drop and reseed its database, so the server protects its write API (`src/requestGuard.js`):

- **Local only by default.** It listens on `127.0.0.1`. Setting `HOST` to anything else, such as `0.0.0.0`, refuses to start unless `ADMIN_TOKEN` is also set.
- **No cross-site writes.** POST requests must be `Content-Type: application/json`, and any `Origin` header must match the page. This stops another website you visit from calling the API in the background. Requests with an unexpected `Host` header are rejected, which blocks DNS-rebinding pages.
- **Optional admin token.** With `ADMIN_TOKEN` set, every change needs it in `X-Admin-Token`. The page asks for it once per browser tab.
- **Browser hardening.** Pages are served with a strict Content-Security-Policy, and every dynamic value is HTML-escaped before rendering.
- **Clear errors.** Caller mistakes return 400, 404, or 409 with a message; 500 is reserved for real server faults.
