# Atlas Control Tower Demo

A small operator console that runs against a **real MongoDB Atlas cluster**. It centers on three workflows:

1. **Exception workflow**: source events open, deduplicate, and link work items; operators acknowledge, assign, note, and resolve them with an audit trail.
2. **Milestone risk**: planned-versus-actual milestones with transparent, rules-based risk (a rules estimate, not a prediction model).
3. **Event reliability**: duplicate, late, out-of-order, and malformed events are labeled, and they never move the timeline backward.

Three synthetic domain profiles share the same workflow: **retail** (store replenishment shipment), **healthcare** (temperature-controlled lab supply transfer; operational data only, no patient data), and **manufacturing** (inbound component lot). All data is synthetic.

## Prerequisites

- Node.js 22 or later (uses `--env-file-if-exists` and `node:test`).
- A **non-production** Atlas cluster. M10+ is recommended. Free and Flex tiers have limits on Search index count and some commands, and the results below were not verified on them.
- A database user with `readWrite` and `dbAdmin` on the demo database. `collMod` needs `dbAdmin`.
- Your IP address in Atlas **Network Access**.
- For the 4th validation stage (`validationLevel: "constraint"`): a cluster on **MongoDB 9.0 or later**. On earlier versions, the stage is disabled in the UI and the reason is shown.

## Setup

```bash
npm install
cp .env.example .env          # set MONGODB_URI (Atlas only; *.mongodb.net is required)
npm run setup                 # collections, indexes, time-series collection, seed data, Search indexes
npm run search:status         # wait until both Search indexes report queryable: true
npm start                     # http://localhost:3000
```

`DEMO_DB_NAME` must contain `demo`. Setup, seed, and reset only touch that database. Setup is idempotent: it creates what is missing and never drops anything. The UI also offers **Initialize demo data** when the database is missing collections, indexes, or a profile's seed data.

Setup inserts seed documents only when they're absent, so **after editing a profile file, run reset** to see the changes; setup won't rewrite existing seeded documents.

Setup, reset, and re-anchor (from the UI or the CLI) hold a maintenance lock in `demo_state`. While it's held, the page shows a banner, pauses refreshes, and disables buttons, so you never see half-updated data. Other API calls return 503 until it finishes. A lock left by a crashed process expires after 10 minutes.

Reset drops and recreates only the demo collections, and it requires confirmation:

```bash
npm run reset -- --confirm atlas_control_tower_demo
```

**Before presenting, re-anchor.** Seeded times are relative to when setup ran, so hours later every milestone looks missed and telemetry falls outside the 8-hour window. Re-anchoring shifts all event, milestone, SLA, and audit times so the seed moment becomes "now". Every gap between them stays the same, and your changes are kept. Telemetry is regenerated, because time-series collections don't allow updating the time field. The UI offers **Re-anchor to now** once the data is more than 30 minutes old.

```bash
npm run reanchor
```

## Data model

| Collection | Purpose |
|---|---|
| `tracked_entities` | One document per tracked item, with the planned milestones embedded |
| `operational_events` | Shared envelope (`trackedEntityId`, `eventType`, `eventTime`, `receivedAt`, `source`, `sourceEventId`, `idempotencyKey`) plus a flexible `details` object per source, and a computed `reliability` sub-document |
| `exceptions` | One work item per entity + category (`exceptionKey`), with severity, SLA, owner, related events, and an embedded audit trail capped at the newest 100 entries (`$push` with `$slice`) |
| `telemetry_readings` | **Time-series** collection (`timeField: ts`, `metaField: meta`) |
| `demo_state` | The persisted validation stage and its history |

Index reasons appear in the UI (**Atlas capabilities → Indexes**) and in `src/db.js`. Two unique indexes carry the reliability guarantees. `idempotencyKey` stops a duplicate source event from being stored twice. `exceptionKey` stops repeated reports from opening a second work item.

## How the reliability rules work

- **Duplicates**: an insert that hits the unique `idempotencyKey` index increments `reliability.duplicateCount` on the original event. It never creates a second event or exception.
- **Late**: `receivedAt - eventTime` exceeds the profile's `lateArrivalMin`.
- **Out of order**: the `eventTime` is older than the newest stored event for the entity.
- **Milestone actual time**: the aggregation (`src/services/overview.js`) takes the *earliest* report per milestone, and status is derived from milestone *sequence*. A late or re-sent event can therefore add a report, but it cannot move the timeline backward.
- **Milestone risk** (`src/domain/milestoneRisk.js`): `completed_late` beyond tolerance; `missed` past planned time plus grace, or skipped while a later milestone is complete; `at_risk` near due, inside grace, carrying upstream lateness, or over the dwell limit. Each status lists its reasons.
- **Exception priority** (`src/domain/rules.js`): severity points + SLA breached or SLA soon + impact level, minus a small amount for acknowledged work. Hover a score in the UI to see every rule applied. Thresholds live in each profile file.

## Atlas capabilities shown

- **Change streams**: one database-level stream (`src/services/changeFeed.js`) with resume tokens and exponential-backoff reconnection, pushed to the browser over Server-Sent Events. Views refresh from the stream, not from button clicks. The header pill shows `live`, `reconnecting`, or `resuming`. Resume tokens are kept in memory, so they cover network blips while the server runs; after a server restart, the stream starts from "now" and the page reloads its data instead of replaying missed changes.
- **MongoDB Search**: `$search` with fuzzy matching, highlights, and token filters by profile, source, and status, over event descriptions and notes and over exception summaries and resolution notes. Search stays disabled until Atlas reports both indexes `queryable: true`. The UI then shows MISSING, building, or UNAVAILABLE with the reason. The index definitions are in `SEARCH_INDEXES` in `src/services/search.js`, and you can paste them into the Atlas UI JSON editor if you prefer to create them manually.
- **Time series**: a `$setWindowFields` moving average, range checks, and excursions. You can change the range in the UI. It is a demo threshold, not a compliance limit. Telemetry is queried on demand because **time-series collections do not support change streams or MongoDB Search**.
- **Schema validation**, applied to `operational_events` with `collMod`:
  1. **Flexible**: no validator.
  2. **Observe**: `validationAction: "warn"`. Invalid writes succeed. The server logs a warning, and the driver does not return it, so the UI uses the `{ $nor: [ { $jsonSchema } ] }` query to surface invalid records.
  3. **Enforce**: `validationAction: "error"`. New invalid writes are rejected with error code 121 and the server's `errInfo`. Existing invalid documents can remain.
  4. **Constraint** (9.0+): the demo runs `prepareConstraintValidationLevel: true`, then `validationLevel: "constraint"`. MongoDB scans the existing documents and fails if any violate the validator. In that case, the UI lists the violating documents so you can **Repair** or **Remove** them first. To leave constraint, the demo downgrades to `strict` before it changes the validator, as MongoDB requires.

**Atlas Stream Processing is not used.** Each event here needs a stateful, per-entity decision (dedupe, ordering, exception linking), which this app makes against the operational collections directly. Stream Processing would fit continuous windowed telemetry from Kafka. Note that it cannot read from time-series collections.

## Demo script (about 10 minutes, retail)

1. **Overview**: the shipment's latest reliable milestone, next milestone, risk, and open exceptions. The seed already includes a missed pickup and a late departure.
2. **Live event flow**: click *Carrier: arrived at cross-dock*. The activity log shows the change-stream insert, then the milestone and timeline refresh.
3. Click *Carrier: resend arrival (duplicate)*. The timeline shows "1 duplicate suppressed" and no new row.
4. Click *WMS: departure scan uploaded 3h late*. The event is labeled **out of order** and **late**, and "Departed" still shows the original actual time with 2 reports.
5. Click *Carrier: ETA slip alert*, then *Store: reports same delay*. One exception now has 2 related events and rises to the top of the queue. Acknowledge it, assign it, add a note, and resolve it, and watch the audit trail.
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
