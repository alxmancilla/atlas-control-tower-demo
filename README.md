# Atlas Control Tower Demo

A synthetic operations console for demonstrating how a team can detect a disruption, understand its business consequence, make an accountable decision, and close the loop.

It is designed for a short, customer-facing conversation—not as a production control tower or an integration with live operational systems.

## The operator story

1. **See the disruption** — follow an upstream signal to the affected shipment, transfer, or component lot.
2. **Understand the consequence** — compare the projected arrival with inventory or line-side cover at each destination.
3. **Prioritize the work** — use exception severity, SLA, and downstream impact to focus attention.
4. **Make a governed decision** — accept or decline an explainable recommendation with an audit trail.
5. **Track the handoff** — move a simulated execution request through its lifecycle.
6. **Close the loop** — record receipt or recovery and show current impact clear while schedule history remains.

All data is synthetic. The demo never writes to a carrier, supplier, WMS, TMS, ERP, MES, production schedule, or clinical system.

## Choose a scenario

| Scenario | Operational question | Recommended story |
| --- | --- | --- |
| **Retail** | Will delayed replenishment leave stores short during a promotion? | Supplier ETA → DC → chilled shipment → store shelf cover |
| **Healthcare** | Can controlled supplies reach the lab on time and within their handling range? | Supplier release → supply hub → courier → lab receiving |
| **Manufacturing** | Will a late or held component lot stop a production line? | Supplier ASN → receiving dock → quality → line-side delivery |

**Retail** is the best default for a general audience. It has the clearest end-to-end story: a delay becomes a stockout risk, an operator accepts an expedite recommendation, and the simulated handoff is reconciled.

## Quick start

### 1. Configure and provision

Prerequisites: Python 3.12+, [uv](https://docs.astral.sh/uv/), and a **non-production** MongoDB Atlas cluster with a database user that can create collections and indexes.

```bash
cp .env.example .env
# Set MONGODB_URI in .env. HOST=127.0.0.1 keeps the dashboard local-only.

uv sync
uv run --env-file .env control-tower setup
uv run --env-file .env python -m app.run
```

Open <http://localhost:8000>. Enter your name in **Acting as** once, and click **▶ Guided demo** to see the operator story step by step for the selected scenario. You can also open <http://localhost:8000/?guide> to start with the guide already open.

With `HOST=127.0.0.1` the dashboard is reachable only from your machine, and no admin token is needed. To share it on the network, set `HOST=0.0.0.0` and a private `ADMIN_TOKEN`; the app refuses to start on a network address without one, and the browser asks for the token once per tab.

### 2. Start fresh before a presentation

`reset` is destructive: it removes the current synthetic events, decisions, handoffs, telemetry, and demo work items, then recreates the three seeded scenarios.

```bash
uv run --env-file .env control-tower reset --confirm atlas_control_tower_demo
```

Replace `atlas_control_tower_demo` with the exact value of `DEMO_DB_NAME` when you use a different demo database. The name must contain `demo` as a safety guard.

Before presenting, open the **Demo setup** tab. Its pre-flight checklist confirms that Atlas is connected, the demo is seeded, live updates are running, Search is ready, and no scenario has been used since the last reset; when something needs attention it shows the fix, including the exact `reset` command for your database. If the demo has been left running for a while, use **Reset scenario time** on that tab. It preserves records while shifting synthetic timestamps and regenerating telemetry so the scenario is current again.

## Running the guided demo

The **▶ Guided demo** panel walks through the six steps of the operator story for whichever scenario is selected. It works the same way for Retail, Healthcare, and Manufacturing.

- Click each step's button **once**. While a step runs (about two seconds), its button shows "Working…" and ignores further clicks.
- **Step 2** (Show business impact) only scrolls to the overview; it changes no data.
- **Step 5** (Advance handoff) takes **three clicks**: submitted → acknowledged → reconciled.
- **Step 3** opens the highest-priority exception, which may not be the one triggered in step 1. In Retail it is *Missed pickup* (SLA already breached), not the delivery delay — a useful point: the most urgent work comes first.
- Guide progress lives in the browser tab. Reloading restarts the guide but keeps the data; repeated events are deduplicated and already-decided recommendations are skipped.
- After "Story complete", run `reset` (see above) before the next audience.

The dashboard is split into three tabs: **Operator view** (the story), **Data & reliability** (KPIs, event reliability, data-quality queue, telemetry, search, schema governance), and **Demo setup** (presenter-only pre-flight checklist and scenario-time reset).

## How the exception queue is ranked

Each open exception gets a transparent priority score, shown with its reasons in the **Priority** column:

| Factor | Points |
| --- | --- |
| Severity | critical 50, high 35, medium 20, low 10 |
| SLA | +40 if breached; +25 if due soon (Retail 60 min, Healthcare 30 min, Manufacturing 45 min) |
| Category impact | impact level × 5 (Retail) or × 6 (Healthcare, Manufacturing) |
| Business impact | critical +30, high +20, watch +5 |
| Already acknowledged | −5 |

Resolved exceptions sink to the bottom; equal scores are ordered by the nearest SLA. The weights live in `app/profiles.py`.

## A 10-minute retail demonstration (manual)

Use this path instead of the guided panel when you want more freedom. Everything below is on the **Operator view** tab unless stated otherwise.

1. Select **Retail** and open **Cross-domain disruption trace**.
   - Tell the supplier → DC → shipment → store-promotion story.
2. Show **Operational overview** and **Active load portfolio**.
   - Explain the difference between schedule risk and current business impact.
3. In **Simulate incoming events**, select **Carrier: ETA slip alert**.
   - Show the delivery-delay exception, per-store shortfall, and expedite recommendation.
4. Open the exception and accept the recommendation.
   - Show the decision audit trail and the simulated **Governed execution handoff**.
5. Progress the handoff from `pending` to `submitted`, `acknowledged`, and `reconciled`.
   - Emphasize that this is a simulation; no external system is contacted.
6. On the **Data & reliability** tab, search for `traffic`, `carrier`, or `berries`.
   - Connect the operational record to related event and exception context.
7. Optional close: select **Store: received**.
   - Business impact clears because supply arrived; the late schedule history remains visible.

## Optional supporting moments

- **Event reliability:** submit *Carrier: arrived at cross-dock*, then *Carrier: resend arrival (duplicate)* to show duplicate suppression.
- **Data stewardship:** submit the supplier missing-correlation sample, then acknowledge or resolve the resulting quality work item on the **Data & reliability** tab.
- **Telemetry:** add an excursion to show a range breach and moving average.
- **Schema governance:** on the **Data & reliability** tab, follow the four on-screen steps — accept a legacy document, observe, repair, enforce — to show flexible intake becoming governed data without touching real data.
- **Similar cases:** once Search is ready, inspect a resolved synthetic exception for a comparable prior outcome.

## What the demo proves

- Multi-stop impact is explained in business terms: projected arrival, hours of cover, shortfall, and affected destinations.
- Exceptions are deduplicated, ranked by a transparent priority score, assigned, resolved, and audited.
- Recommendations support an operator decision; they do not automate external actions.
- Quality problems are handled as separate, auditable work rather than silently changing upstream data.
- The portfolio separates the primary interactive scenario from clearly labelled synthetic context loads.
- The dashboard stays live as events, exceptions, and handoffs change, using MongoDB Change Streams — no separate streaming or messaging layer is required.

## Operational commands

Run these from the project root:

| Command | Purpose |
| --- | --- |
| `uv run --env-file .env control-tower setup` | Create missing demo collections, indexes, and seed data without overwriting existing demo activity. |
| `uv run --env-file .env control-tower check` | Check initialization, Search readiness, and validation state. |
| `uv run --env-file .env control-tower reanchor` | Shift synthetic timestamps to now without deleting activity. |
| `uv run --env-file .env control-tower reset --confirm <demo-db-name>` | Destructively recreate the synthetic demo database. |
| `uv run --env-file .env control-tower search-status` | Check whether Search and similar-case lookup are queryable. |
| `uv run --env-file .env control-tower search-create` | Request any missing Atlas Search and vector indexes (for example after a setup run with `--without-search`). |

Add `--without-search` to `setup` or `reset` to skip creating Search indexes.

Search can take a short time to become queryable after first setup or a reset. The main operational story works while it is building.

## Verification

```bash
uv run pytest
uv run --env-file .env python verify_operator_journey.py \
  --database atlas_control_tower_demo_operator_verify \
  --confirm-cleanup atlas_control_tower_demo_operator_verify
```

The first command is the local test suite. The second is an opt-in Atlas smoke test that creates and removes only the explicitly named disposable verification database.

## Technical reference

The Python/FastAPI application is the only runtime; the earlier Node.js runtime and the optional Atlas Stream Processing / Kafka telemetry tooling have been retired. For endpoints, API behavior, data model details, Atlas capability notes, and security controls, see [`TECHNICAL.md`](TECHNICAL.md).

Connection strings and tokens are never logged. Keep `.env` private and use this project only with a non-production database.