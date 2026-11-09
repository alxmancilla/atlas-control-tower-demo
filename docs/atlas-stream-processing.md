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

## Local Kafka contract stack

The repository includes a single-node Apache Kafka 4.0.2 container for local contract
development. It creates no cloud resources, binds only to `127.0.0.1:9092`, and has
no persistent volume; stopping it removes local topic data.

```bash
npm run kafka:up       # start Kafka, wait for health, create the topic
npm run kafka:publish  # publish normal and excursion readings for all profiles
npm run kafka:verify   # read the six published messages from the beginning
npm run kafka:down     # stop and remove the local container
```

The publisher emits newline-delimited JSON directly through Kafka's console producer;
it adds no npm Kafka client dependency and does not read or write Atlas. Set the
non-secret `KAFKA_TELEMETRY_TOPIC` to use another safe topic name locally.

> The local broker is deliberately a loopback-only `PLAINTEXT` development stack.
> Atlas Stream Processing cannot connect to it: ASP requires a reachable bootstrap
> endpoint and supports `SSL`, `SASL_SSL`, or `SASL_PLAINTEXT` for Kafka. Do not
> expose this development broker to the internet.

## Atlas prerequisites and provisioning inputs

No credential belongs in this repository. An Atlas administrator must create a Stream
Processing workspace in a region close to the Atlas cluster, then create these
Connection Registry entries before deploying the processor:

- `ASP_KAFKA_CONNECTION`: Kafka connection-registry label for an externally reachable
  TLS/SASL Kafka endpoint. Its advertised broker hostname and TLS certificate must
  match; `localhost` and the local Compose broker are invalid here.
- `ASP_TELEMETRY_TOPIC`: Kafka topic name.
- `ASP_ATLAS_CONNECTION`: Atlas sink connection-registry label.
- `DEMO_DB_NAME`: existing guarded demo database; defaults to
  `atlas_control_tower_demo`.

For a real end-to-end demo, run the same Apache Kafka image on a cloud VM or container
platform with a stable public or privately networked DNS endpoint, TLS, and a supported
Kafka authentication mechanism. Alternatively, use an approved networking solution
that provides those properties. A transient localhost tunnel is not sufficient because
Kafka clients follow the broker's advertised listener metadata.

In the Atlas UI, create a Kafka connection with the secured broker bootstrap server and
its matching security settings, then create an Atlas Cluster connection targeting the
demo cluster. Keep passwords, SASL credentials, client keys, and CA material in Atlas
Connection Registry fields only; do not put them in `.env` or this repository.

Use `npm run stream:processor:check` to validate only the non-secret labels. Render
the pipeline and copy it into the Stream Processing UI/API, configure a DLQ, start the
processor, and inspect processor state and source lag there.

## Application behavior

`npm run setup` creates the empty `telemetry_rollups` collection and its unique lookup
index. Set `ASP_TELEMETRY_ENABLED=true` only after the processor is running. The
FastAPI telemetry response then includes recent rollups; if none exist, its direct
time-series view remains available and explains that it is waiting for processor
output. If the flag is false, no rollup collection query is issued.

## Verification and operations

1. Run `npm run setup` to create `telemetry_rollups` and its index.
2. Use the local Kafka commands above to inspect the exact source-message contract.
3. Create the ASP workspace and secure Kafka/Atlas connections as described above.
4. Set the three `ASP_*` labels, run both `stream:processor` commands, configure a DLQ,
   and deploy/start the processor.
5. Publish normal and out-of-range messages to the secured broker.
6. Set `ASP_TELEMETRY_ENABLED=true` and confirm a unique rollup appears for each
   window; the dashboard reports `normal` or `excursion` without hiding direct data.
7. Monitor processor state, source lag, sink errors, and DLQ in Atlas. Stop the
   processor before changing its source or sink contract.

The repository cannot provision the instance, Connection Registry entries, Kafka
credentials, secure public endpoint, or processor because those are cloud-side
administrative operations.