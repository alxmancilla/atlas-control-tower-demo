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

## Opt-in public-IP Kafka broker

`docker-compose.kafka.public.yml` is a separate, single-node **demo-only** profile
for an existing Stream Processing workspace that uses Public IP networking. It does
not alter the loopback stack. It exposes only TCP `9094`, uses `SASL_SSL` with a
default-deny Kafka ACL policy, and keeps its administrative listener inside the Docker
network. It is less safe and less resilient than the MSK PrivateLink option.

Before starting it, complete all of the following:

1. Reserve a stable, routable public IPv4 address. A changing ISP address invalidates
   the broker certificate and Atlas connection.
2. Forward only TCP `9094` from the router/public firewall to this machine. Do not
   expose `9092` or the Docker API.
3. Obtain the Atlas control-plane outbound IPs for the Stream Processing workspace's
   provider and region, then allow **only those addresses** through the host/router
   firewall on `9094`. The Atlas security documentation provides the
   `controlPlaneIPAddresses` endpoint for this list. Do not allow `0.0.0.0/0`.
4. Choose three independent strong passwords for the local `admin`, `producer`, and
   `asp` principals. Keep them in a password manager; never place them in `.env`, Git,
   a terminal history, or an Atlas processor definition.

Prepare the certificate and local-only credential files from a shell that has the
public IP available. The preparation script prompts silently for the three passwords
and requires `openssl` plus Java's `keytool`; CI may instead supply them through
protected environment variables. The script refuses to replace existing material and
writes it below the ignored `.local/kafka-public/` directory with restrictive
permissions.

```bash
export KAFKA_PUBLIC_IP=<stable-public-ipv4>
npm run kafka:public:prepare
```

Add only the non-secret `KAFKA_PUBLIC_IP` value to `.env`, then start and populate the
broker. The public profile creates the telemetry topic and grants the `producer`
principal write access and the `asp` principal read access limited to the processor's
dedicated consumer group.

```bash
npm run kafka:public:up
npm run kafka:public:publish
npm run kafka:public:verify
npm run kafka:public:certificate
```

In Atlas Stream Processing Connection Registry, create a **Kafka** connection with:

| Field | Value |
| --- | --- |
| Network access | `Public IP` |
| Bootstrap server | `<stable-public-ipv4>:9094` |
| Security protocol | `SASL_SSL` |
| SASL mechanism | `PLAIN` |
| Username | `asp` |
| Password | the private `KAFKA_PUBLIC_ASP_PASSWORD` value |
| Trusted broker certificate | the PEM file path printed by `kafka:public:certificate` |
| Connection name | `controlTowerKafkaPublic` or another safe label |

The preparation script creates a non-CA, TLS server-authentication certificate with the
configured public IP as its IP Subject Alternative Name and keeps hostname verification
enabled. It expires after 30 days; rotate it before expiry by stopping the profile,
removing the local secret directory through your operating system, preparing fresh
material, updating the Atlas trusted certificate, and starting the profile again. If
the public IP changes, perform the same rotation before ASP can reconnect.

Do not use the local `verify` command as evidence that the router is reachable from
the internet: it deliberately uses the non-public admin listener. After the firewall
allows Atlas control-plane addresses, the secure end-to-end check is successful Atlas
connection validation and processor output in `telemetry_rollups`. If you temporarily
allow a separate external test host, remove that firewall exception immediately after
testing.

Stop the profile before removing its network forwarding and credentials:

```bash
npm run kafka:public:down
```

## Atlas prerequisites and provisioning inputs

No credential belongs in this repository. An Atlas administrator must create a Stream
Processing workspace on AWS in the same region as MSK. The repository includes an
optional Terraform root at [`../infra/msk-atlas-streams/`](../infra/msk-atlas-streams/)
that creates a private MSK broker, its Atlas Streams PrivateLink endpoint, and both
Connection Registry entries. It requires explicit apply/destroy operations, a
non-production VPC, and encrypted access-controlled Terraform state.

Whether provisioned manually or by that Terraform root, deploy the processor only
after these Connection Registry entries exist:

- `ASP_KAFKA_CONNECTION`: Kafka connection-registry label for an ASP-reachable
  TLS/SASL Kafka endpoint. Its advertised broker hostname and TLS certificate must
  match; `localhost` and the local Compose broker are invalid here.
- `ASP_TELEMETRY_TOPIC`: Kafka topic name.
- `ASP_ATLAS_CONNECTION`: Atlas sink connection-registry label.
- `DEMO_DB_NAME`: existing guarded demo database; defaults to
  `atlas_control_tower_demo`.

For a real end-to-end demo, use the Terraform MSK path, the constrained public-IP
profile above, or another approved Kafka deployment with TLS and supported Kafka
authentication. A transient localhost tunnel is not sufficient because Kafka clients
follow the broker's advertised listener metadata.

For manual provisioning, create a Kafka connection with the secured broker bootstrap
server and its matching security settings, then create an Atlas Cluster connection
targeting the demo cluster. The Terraform root creates these entries instead. Keep
passwords, SASL credentials, client keys, and CA material in Atlas Connection Registry
fields only; do not put them in `.env` or this repository.

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

The Terraform root intentionally does not create the Stream Processing workspace,
Kafka topic/ACLs, or processor. Those require the workspace's region/principal values
and a Kafka control-plane administrator. It does create the demo MSK cluster, SCRAM
credential, PrivateLink endpoint, and Connection Registry entries when explicitly
applied.