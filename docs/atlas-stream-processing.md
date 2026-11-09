# Atlas Stream Processing telemetry extension

## Purpose and boundary

This opt-in extension continuously materializes telemetry rollups. It does not replace
the direct `operational_events` transaction path: event ordering, idempotency,
exception linking, and operator decisions remain application-owned.

```text
Kafka topic → Atlas Stream Processing → telemetry_rollups → FastAPI dashboard
```

The existing `telemetry_readings` time-series collection remains the direct demo and
fallback read model. It is not an ASP source.

## Input contract

Each Kafka message is JSON and must include the following fields:

| Field | Type | Meaning |
| --- | --- | --- |
| `eventId` | string | Producer-assigned unique reading identifier for tracing. |
| `eventTime` | ISO-8601 string | Authoritative device observation time. |
| `profile` | string | `retail`, `healthcare`, or `manufacturing`. |
| `trackedEntityId`, `sensorId`, `metric`, `unit` | string | Telemetry identity and presentation data. |
| `value` | number | Observed measurement. |
| `range.min`, `range.max` | number | Thresholds effective when the device emitted the reading. |

Producers must retain `eventId` while retrying. The processor accepts event time up to
five minutes behind the watermark; later messages are outside this demo's rollup SLA
and require producer/DLQ investigation.

## Processor contract

`npm run stream:processor` renders the reviewed pipeline from
`src/services/streamProcessing.js`. It reads JSON from `ASP_TELEMETRY_TOPIC`, uses
`eventTime` as the source time field, and produces non-overlapping one-minute,
event-time windows. Each output document has the unique identity:

`profile + sensorId + metric + windowStart`

The final `$merge` replaces the matching rollup, making replay or window correction
converge on the current aggregate. A rollup includes count, average, min/max,
out-of-range count, and `status` (`normal` or `excursion`).

## Provisioning inputs

No credential belongs in this repository. An Atlas administrator must create a Stream
Processing instance and register these connections before deployment:

- `ASP_KAFKA_CONNECTION`: Kafka connection-registry label.
- `ASP_TELEMETRY_TOPIC`: Kafka topic name.
- `ASP_ATLAS_CONNECTION`: Atlas sink connection-registry label.
- `DEMO_DB_NAME`: existing guarded demo database; defaults to
  `atlas_control_tower_demo`.

Use `npm run stream:processor:check` to validate only these non-secret labels, then
copy the rendered pipeline into the Atlas Stream Processing UI/API, start the
processor, and inspect its status and dead-letter configuration there.

## Application behavior

`npm run setup` creates the empty `telemetry_rollups` collection and its unique lookup
index. Set `ASP_TELEMETRY_ENABLED=true` only after the processor is running. The
FastAPI telemetry response then includes recent rollups; if none exist, its direct
time-series view remains available and explains that it is waiting for processor
output. If the flag is false, no rollup collection query is issued.

## Verification and operations

1. Run `npm run setup` after deploying this change.
2. Render and validate the processor artifact with the two `stream:processor` commands.
3. Publish normal and out-of-range messages for one profile to Kafka.
4. Confirm a unique rollup document appears for each window and the dashboard reports
   its `normal`/`excursion` status.
5. Monitor processor state, source lag, sink errors, and DLQ in Atlas. Stop the
   processor before changing its source or sink contract.

The repository cannot provision the instance, connection-registry entries, Kafka
credentials, or processor because those are cloud-side administrative operations.