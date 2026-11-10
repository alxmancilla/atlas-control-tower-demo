# Atlas Control Tower Demo

A small operator console that runs against a **real MongoDB Atlas cluster**. Its core operator story is: trace a disruption, ingest a signal, prioritize its exception, record a decision, and find similar resolved work. The same shared data model also demonstrates these workflows:

1. **Portfolio prioritization**: active loads are ranked by business impact, open work, and SLA urgency; one primary load offers the full interactive drill-down.
2. **Exception-to-decision workflow**: source events open, deduplicate, and link work items; operators acknowledge, assign, and resolve them, and can accept or decline explainable recommended actions with an audit trail.
3. **Milestone risk**: planned-versus-actual milestones with transparent, rules-based risk (a rules estimate, not a prediction model).
4. **Event reliability and data quality**: duplicate, late, out-of-order, future-dated, and malformed events are labeled. Reliability flags create deduplicated, auditable source-quality work items and never move the timeline backward.
5. **Cross-domain disruption trace**: each profile links a deliberately late upstream signal to bounded downstream operational impact: supplier → DC → store promotion (retail), supplier release → hub → courier → lab (healthcare), and supplier ASN → dock → quality → production line (manufacturing).
6. **Source-contract evidence**: upstream supplier signals carry a versioned canonical correlation contract, mapping status, and a freshness target; an incomplete contract creates auditable data-quality work without changing a partner system.
7. **Governed execution handoff**: an accepted recommendation creates one idempotent, auditable **simulated** outbox request. Operators move it through `pending → submitted → acknowledged` or `failed → reconciled`; it never contacts an external system.

Three synthetic domain profiles share the same workflow: **retail** (store replenishment shipment), **healthcare** (temperature-controlled lab supply transfer; operational data only, no patient data), and **manufacturing** (inbound component lot). All data is synthetic.

## Prerequisites

- Python 3.12+ and [uv](https://docs.astral.sh/uv/) for the default FastAPI demo.
- Node.js 22 or later for demo setup/reset, Search administration, and the retained Node reference implementation.
- A **non-production** Atlas cluster. M10+ is recommended. Free and Flex tiers have limits on Search index count and some commands, and the results below were not verified on them.
- A database user with `readWrite` and `dbAdmin` on the demo database. `collMod` needs `dbAdmin`.
- Your IP address in Atlas **Network Access**.

**Optional capabilities:** the [Stream Processing runbook](docs/atlas-stream-processing.md) covers its separate Kafka, Docker, public-broker, and MSK prerequisites. The **Constraint** validation stage needs MongoDB 9.0+. Similar-case Vector Search needs M10+ storage auto-scaling and consumes Atlas Automated Embedding tokens. The core workflow continues to work when either optional capability is unavailable.

## Setup

```bash
npm install
cp .env.example .env          # set MONGODB_URI and a strong ADMIN_TOKEN
npm run setup                 # collections, indexes, time-series collection, seed data, Search and Vector Search indexes
npm run search:status         # wait until the three indexes report queryable: true
npm start                     # default FastAPI dashboard: http://localhost:8000
```

`MONGODB_URI` must point to Atlas (`*.mongodb.net`). `ADMIN_TOKEN` is required by
default because FastAPI listens on `0.0.0.0:8000`; it protects every write action.
For local-only use, set `HOST=127.0.0.1` instead. To use the dashboard from another
device, add this machine's LAN hostname or IP address to `ALLOWED_HOSTS`.

### Optional: Atlas Stream Processing telemetry rollups

This is deliberately outside the core demo. The application keeps event-to-exception
decisions transactional; Atlas Stream Processing is an opt-in Kafka telemetry rollup
into `telemetry_rollups`. It never reads the direct `telemetry_readings` time-series
collection. The [dedicated runbook](docs/atlas-stream-processing.md) covers the local
contract stack, secure public broker, private MSK path, processor deployment, and
`ASP_TELEMETRY_ENABLED=true` activation.

### Default implementation: Python / FastAPI

`npm start` launches the FastAPI dashboard from [`python/`](python/) on
`0.0.0.0:8000`. Open `http://localhost:8000` locally, or use this machine's LAN
address from another device. It uses the same non-production Atlas demo database
and supports the dashboard's operational read/write, Search, telemetry, Change
Stream, validation, and reanchor workflows.

The tagged Node application remains the setup/reset and original-UI reference. Run
it explicitly with `npm run start:node` at `http://localhost:3000`; continue using
the Node CLI commands above to provision or administer demo data. `PORT` controls
that Node server, while `PYTHON_PORT` controls FastAPI (default `8000`). See
[`python/README.md`](python/README.md) for FastAPI's endpoint contract and tests.

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
| `data_quality_issues` | One deduplicated work item per original event + reliability issue (`issueKey`), with source, confidence, event/receipt times, observed count, status, and a bounded audit trail. It is separate from the load because source-quality work grows and is actioned independently |
| `execution_outbox` | One internal simulated-handoff request per accepted recommendation (`executionKey`: profile + exception + action). It preserves a bounded recommendation/decision snapshot, connector label, lifecycle state, submission count, and newest-100 audit entries. It contains no external endpoint, credential, or adapter |
| `inventory_positions` | Stock at a location for an item (on hand, consumption rate), one per `locationId` + `itemRef`; one per stop on the load, and the business-impact join target. Kept separate from the load because stock changes independently of it |
| `kpi_snapshots` | Materialized view: one KPI document per profile, written by an aggregation with `$merge` |
| `telemetry_readings` | **Time-series** collection (`timeField: ts`, `metaField: meta`) |
| `telemetry_rollups` | Optional ASP-derived one-minute telemetry aggregates, keyed by profile/sensor/metric/window start |
| `demo_state` | The persisted validation stage, its history, and the maintenance lock |

**One collection, many source formats.** Each event records its `channel` (`edi`, `api`, `wms_scan`, `gps`, `iot`, `manual`, `batch_file`) and keeps the source's own payload in `details.raw`: an EDI 214 or 856 segment string, a WMS scan record, a GPS ping, an IoT logger reading, a webhook body, or a CSV row. The validator enforces the shared envelope and leaves `details` free, so a new source needs no schema migration. Expand **raw** on any timeline row to see it. Raw timestamps are written at ingest; re-anchoring shifts the envelope times but leaves raw payloads as received.

Index reasons appear in the UI (**Atlas capabilities → Indexes**) and in `src/db.js`. Four unique indexes carry the workflow guarantees. `idempotencyKey` stops a duplicate source event from being stored twice. `exceptionKey` stops repeated reports from opening a second exception. `issueKey` ensures repeated delivery of the same unreliable signal updates one data-quality work item. `executionKey` guarantees one outbox handoff per accepted recommendation.

## How the reliability rules work

- **Duplicates**: an insert that hits the unique `idempotencyKey` index increments `reliability.duplicateCount` on the original event. It never creates a second event or exception.
- **Late**: `receivedAt - eventTime` exceeds the profile's `lateArrivalMin`.
- **Out of order**: the `eventTime` is older than the newest stored event for the entity.
- **Confidence and quality queue**: accepted event envelopes receive `high`, `medium`, or `low` confidence from their reliability flags. Late, out-of-order, future-dated, and repeated-milestone flags—and duplicate suppression—upsert a source-quality work item in the same transaction as the event. Operators may acknowledge it or resolve it with an auditable note; resolving a demo issue records a stewardship decision, not a correction in an upstream source system.
- **Source-contract evidence**: the upstream supplier source in every profile has an immutable `v1` contract. It records required canonical correlation keys, a mapping result, and a source-specific freshness target on the accepted event. Retail requires `purchaseOrder` and `supplierId`; healthcare requires `transferOrder` and `supplierId`; manufacturing requires `purchaseOrder`, `asnId`, and `supplierId`. The ingestion boundary does **not** parse arbitrary partner payloads: adapters must map their data into `details` before submission. If any required key is absent, `sourceContract.mappingStatus` is `incomplete` and a high-severity **Source contract incomplete** work item is upserted in the same transaction. Freshness is evidence (`fresh`, `stale`, or `future_dated`) calculated from the received-event lag; it does not alter the reliability confidence rules.

Source-contract evidence is written at ingestion. Reset the non-production demo database after this profile change to show it on the pre-seeded trace signals; new submitted samples receive it without a reset.
- **Milestone actual time**: the aggregation (`src/services/overview.js`) takes the *earliest* report per milestone, and status is derived from milestone *sequence*. A late or re-sent event can therefore add a report, but it cannot move the timeline backward.
- **Milestone risk** (`src/domain/milestoneRisk.js`): `completed_late` beyond tolerance; `missed` past planned time plus grace, or skipped while a later milestone is complete; `at_risk` near due, inside grace, carrying upstream lateness, or over the dwell limit. Each status lists its reasons.
- **Business impact** (`src/domain/impact.js`): for each stop on the load, hours of cover (on hand ÷ consumption per hour) compared with that stop's projected arrival (the final milestone's projected time plus the stop's `stopOffsetMin`). A shortfall is `high`, a shortfall beyond the profile's critical threshold is `critical`, and a thin margin is `watch`. The load takes the worst stop's level and sums the units short across stops. Each level lists its reasons, for example "3 h of cover; Arrival at Store #1187 Riverside projected in 3.8 h; 0.8 h short: shelf empty at Store #1187 Riverside during the weekend promo".
- **Recommended actions and execution handoff** (`src/domain/recommendations.js`, `src/services/executionOutbox.js`): each `high` or `critical` stop receives an explainable **Expedite delivery** recommendation. It is an operator decision aid, not automation. A decline requires a reason; either decision is stored once in `actionDecisions`. An acceptance also creates an `execution_outbox` record in the same transaction, with a unique key, immutable action/decision snapshot, and an explicit `simulated` connector marker. Operators may simulate `submit`, `acknowledge`, `fail`, and `reconcile`; failure and reconciliation require a note. No lifecycle action writes to a carrier, supplier, ERP, WMS, TMS, MES, purchase order, production schedule, or any external API. Fresh impact can refresh the recommendation without overwriting the human decision or handoff evidence.
- **Exception priority** (`src/domain/rules.js`): severity points + SLA breached or SLA soon + category impact + business impact level, minus a small amount for acknowledged work. Hover a score in the UI to see every rule applied. Thresholds live in each profile file.

## Atlas capabilities shown

- **Multi-document ACID transactions**: each ingested event, its exception work item (when applicable), and any derived data-quality work items commit together or not at all (`ingestEvent` in `src/services/events.js`). If a dependent work item fails, the event insert is rolled back. A duplicate aborts the insert transaction, and a second transaction records the duplicate on the original. Change events from a transaction carry its `txnNumber`, shown as a **txn** badge in the activity log, so you can see related records arrive as one unit.
- **Governed simulated outbox**: accepting a recommendation updates the exception decision and inserts its unique `execution_outbox` request in one transaction. The dashboard's **Governed execution handoff** panel simulates the lifecycle only; it has no integration configuration or network-capable connector. The collection is included in the database-level Change Stream and re-anchors with the synthetic demo clock. After upgrading an existing demo database, run `npm run setup` once to create its collection and indexes; a reset is not required.
- **Bounded cross-domain trace**: every primary entity embeds a four-node, synthetic topology because it is small and always displayed with that entity. Retail follows supplier → DC allocation → shipment → store promotion; healthcare follows reagent supplier → supply hub → courier → lab receiving; manufacturing follows component supplier → receiving dock → quality station → Line B. The linked late source signal remains in `operational_events`; `GET /api/disruption-trace` returns the topology and its supporting signal. This demonstrates correlation and impact, not a live multi-enterprise network, patient/clinical data processing, supplier-system write-back, or production-schedule automation.
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

**Atlas Stream Processing is optional and telemetry-only.** Each operational event needs a stateful, per-entity decision (dedupe, ordering, exception linking), which this app makes transactionally against operational collections. The optional processor instead reads Kafka telemetry, applies one-minute event-time windows with a five-minute lateness allowance, and materializes `telemetry_rollups`. It does not read the `telemetry_readings` time-series collection. See [`docs/atlas-stream-processing.md`](docs/atlas-stream-processing.md).

## Core demo script (about 10 minutes, retail)

1. **Trace → portfolio → impact**: open **Cross-domain disruption trace** and follow the late supplier ETA through the DC, chilled shipment, and promo exposure. Then show **Active load portfolio** and the primary load's **Downstream impact** by store.
2. **Live event flow**: click *Carrier: arrived at cross-dock*, expand its raw EDI data, then click *Carrier: resend arrival (duplicate)* to show suppression without a second timeline row.
3. **Exception → decision**: click *Carrier: ETA slip alert* and *Store: reports same delay*. The transaction badge shows the related writes. Open the delivery-delay exception, explain its stockout recommendation, and accept or decline it with an operator identity.
4. **Search → resolution**: search `delayed replenishment` or `traffic`, open a similar resolved case, then acknowledge, assign, note, and resolve the current exception. *Store: received* refreshes downstream impact to `none`.

### Optional extensions

- **Data quality and source contracts:** inspect the seeded late supplier signal, then run the missing-correlation and late-departure samples. They create separate auditable stewardship work; they do not change a partner record.
- **Governed handoff:** after accepting a recommendation, simulate `pending → submitted → acknowledged → reconciled` (or `failed → reconciled`). It is an audit demonstration, never an external dispatch.
- **Telemetry and schema validation:** add an excursion, run the time-series aggregation, then use Observe/Enforce and optionally Constraint (MongoDB 9.0+) validation stages.
- **Other profiles:** revisit the trace and data-quality queue for healthcare and manufacturing. They contain operational logistics data only and no patient, clinical, supplier, or production-system writes.

## Testing

```bash
npm test                    # unit tests for the rules, schema transitions, profiles, and config (no network)
npm run test:integration    # against Atlas: uses TEST_DB_NAME (default atlas_control_tower_demo_test)
cd python && uv run pytest  # FastAPI unit/workflow tests (no network)
cd python && uv run --env-file ../.env python verify_operator_journey.py --database atlas_control_tower_demo_operator_verify --confirm-cleanup atlas_control_tower_demo_operator_verify
```

Integration tests **skip with an explicit message** when `MONGODB_URI` is not set. They never mock Atlas or use a local deployment. They reset only `TEST_DB_NAME`, which must contain `demo`. Before the first run, they do not create Search indexes, so the search test checks status reporting only.

The final command is the opt-in FastAPI Atlas verification. It creates and removes only
the explicitly confirmed `_operator_verify` database, then checks ingestion and
deduplication, impact refresh on receipt, recommendation acceptance → simulated outbox
lifecycle, re-anchor, telemetry, and validation stages through the public API.

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
| `npm start` refuses to start | Set a strong `ADMIN_TOKEN`; the default FastAPI bind is `0.0.0.0:8000` |
| 415 or 403 from scripts calling the API | Send `Content-Type: application/json`, and no foreign `Origin` header |
| Search UNAVAILABLE | The cluster tier or user cannot manage Search indexes; create them in the Atlas UI |
| Constraint button disabled | The server is earlier than 9.0; the UI shows the reported version |
| Constraint fails with a validation error | Repair or remove the listed invalid documents, then retry |
| Every milestone missed, telemetry empty | Demo clock is stale: `npm run reanchor` or the UI banner button |
| Change stream `reconnecting` | It resumes automatically from the last resume token; if history was lost, it restarts and the page resyncs |

Connection strings are never logged. Errors show only the host name (see `redact`/`targetLabel` in `src/config.js`).

## Access and safety

The demo can drop and reseed its database, so the server protects its write API (`src/requestGuard.js`):

- **Protected network binding.** FastAPI defaults to `0.0.0.0:8000`; it refuses to start unless `ADMIN_TOKEN` is set. Override `HOST=127.0.0.1` for loopback-only access.
- **Explicit hosts.** Loopback hosts are allowed by default. Add a LAN hostname or IP to `ALLOWED_HOSTS` for remote dashboard access; unexpected `Host` headers are rejected to block DNS-rebinding pages.
- **No cross-site writes.** POST requests must be `Content-Type: application/json`, and any `Origin` header must match the page. This stops another website you visit from calling the API in the background.
- **Admin token.** With the default network binding, every change needs `ADMIN_TOKEN` in `X-Admin-Token`. The page asks for it once per browser tab.
- **Browser hardening.** The dashboard has a per-response nonce-based Content Security Policy, and every dynamic value is HTML-escaped before rendering.
- **Clear errors.** Caller mistakes return 400, 404, or 409 with a message; 500 is reserved for real server faults.
