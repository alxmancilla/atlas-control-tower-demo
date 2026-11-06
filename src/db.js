import { MongoClient } from 'mongodb';
import { redact } from './config.js';
import { constraintGate } from './domain/rules.js';

export const COLL = {
  entities: 'tracked_entities',
  events: 'operational_events',
  exceptions: 'exceptions',
  telemetry: 'telemetry_readings',
  positions: 'inventory_positions',
  kpis: 'kpi_snapshots',
  state: 'demo_state',
};

// Normal (B-tree) indexes and the operator lookup each one serves.
export const INDEXES = [
  { coll: COLL.entities, key: { trackedEntityId: 1 }, options: { unique: true, name: 'trackedEntityId_unique' },
    reason: 'One document per tracked entity; overview lookups by ID.' },
  { coll: COLL.entities, key: { profile: 1 }, options: { name: 'profile' }, reason: 'List entities for the selected profile.' },
  { coll: COLL.events, key: { trackedEntityId: 1, eventTime: 1 }, options: { name: 'entity_eventTime' },
    reason: 'Timeline sorted by event time and the newest-event check for out-of-order detection.' },
  { coll: COLL.events, key: { trackedEntityId: 1, milestoneId: 1, eventTime: 1 }, options: { name: 'entity_milestone_eventTime' },
    reason: 'Earliest report per milestone for planned-versus-actual.' },
  { coll: COLL.events, key: { idempotencyKey: 1 }, reason: 'Uniqueness guarantee for source + sourceEventId so duplicates are never stored twice.',
    options: { unique: true, name: 'idempotencyKey_unique', partialFilterExpression: { idempotencyKey: { $type: 'string' } } } },
  { coll: COLL.events, key: { profile: 1, receivedAt: -1 }, options: { name: 'profile_receivedAt' },
    reason: 'Recent events and reliability counts per profile.' },
  { coll: COLL.exceptions, key: { exceptionId: 1 }, options: { unique: true, name: 'exceptionId_unique' },
    reason: 'Stable exception identifier used by operator actions.' },
  { coll: COLL.exceptions, key: { exceptionKey: 1 }, options: { unique: true, name: 'exceptionKey_unique' },
    reason: 'One work item per entity + category so repeated source events update instead of duplicating work.' },
  { coll: COLL.exceptions, key: { profile: 1, status: 1, slaDueAt: 1 }, options: { name: 'profile_status_sla' },
    reason: 'Exception queue filtered by status and ordered by SLA.' },
  { coll: COLL.exceptions, key: { trackedEntityId: 1, status: 1 }, options: { name: 'entity_status' },
    reason: 'Join target for the overview $lookup: open exceptions per tracked entity.' },
  { coll: COLL.positions, key: { locationId: 1, itemRef: 1 }, options: { unique: true, name: 'location_item_unique' },
    reason: 'One stock position per location and item; join target for the business-impact $lookup.' },
  { coll: COLL.telemetry, key: { 'meta.trackedEntityId': 1, 'meta.metric': 1, ts: 1 }, options: { name: 'meta_entity_metric_ts' },
    reason: 'Recent readings for one entity and metric (secondary index on the time-series metaField).' },
];

export async function connect(config) {
  const client = new MongoClient(config.uri, { appName: 'atlas-control-tower-demo', serverSelectionTimeoutMS: 15000 });
  try {
    await client.connect();
    await client.db(config.dbName).command({ ping: 1 });
  } catch (err) {
    await client.close().catch(() => {});
    throw new Error(`Could not connect to Atlas target ${config.target}: ${explainConnectError(err)}`);
  }
  return { client, db: client.db(config.dbName) };
}

export function explainConnectError(err) {
  const msg = redact(err?.message);
  if (/authentication failed|bad auth/i.test(msg)) return `${msg}. Check the database user name and password in MONGODB_URI.`;
  if (/ENOTFOUND|querySrv/i.test(msg)) return `${msg}. Check the cluster host name in MONGODB_URI.`;
  if (/Server selection timed out|ECONNREFUSED|ETIMEDOUT|ReplicaSetNoPrimary/i.test(msg)) {
    return `${msg}. Check Atlas Network Access: add this machine's IP address to the project IP access list.`;
  }
  return msg;
}

export async function serverInfo(db) {
  const build = await db.admin().command({ buildInfo: 1 });
  let fcv = null;
  try {
    const res = await db.admin().command({ getParameter: 1, featureCompatibilityVersion: 1 });
    fcv = res.featureCompatibilityVersion?.version ?? null;
  } catch {
    fcv = null; // Requires extra privileges; informational only.
  }
  return { version: build.version, fcv, gate: constraintGate(build.version) };
}

export async function collectionInfo(db) {
  const list = await db.listCollections({}, { nameOnly: false }).toArray();
  return Object.fromEntries(list.map((c) => [c.name, { type: c.type, options: c.options ?? {} }]));
}

// Creates collections and indexes if missing. Never drops anything.
export async function ensureSetup(db) {
  const existing = await collectionInfo(db);
  const created = [];
  for (const name of [COLL.entities, COLL.events, COLL.exceptions, COLL.positions, COLL.kpis, COLL.state]) {
    if (!existing[name]) {
      await db.createCollection(name);
      created.push(name);
    }
  }
  if (!existing[COLL.telemetry]) {
    await db.createCollection(COLL.telemetry, {
      timeseries: { timeField: 'ts', metaField: 'meta', granularity: 'minutes' },
    });
    created.push(COLL.telemetry);
  } else if (existing[COLL.telemetry].type !== 'timeseries') {
    throw new Error(`${COLL.telemetry} exists but is not a time-series collection. Reset the demo database to recreate it.`);
  }
  for (const idx of INDEXES) {
    await db.collection(idx.coll).createIndex(idx.key, idx.options);
  }
  await db.collection(COLL.state).updateOne(
    { _id: 'validation' },
    { $setOnInsert: { stage: 'flexible', history: [], updatedAt: new Date() } },
    { upsert: true }
  );
  return { created, indexes: INDEXES.length };
}

export function serverError(err) {
  return {
    name: err?.name,
    code: err?.code,
    codeName: err?.codeName,
    message: redact(err?.message),
    errInfo: err?.errInfo,
  };
}
