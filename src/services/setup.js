import { COLL, INDEXES, collectionInfo, ensureSetup, serverError } from '../db.js';
import { PROFILES } from '../profiles/index.js';
import { buildEvent, buildInvalidEvent, buildTrackedEntity, byIdempotencyKey } from '../domain/documents.js';
import { detectStage } from '../domain/schema.js';
import { ingestEvent } from './events.js';
import { applyExceptionAction, exceptionKeyFor } from './exceptions.js';
import { seedTelemetry } from './telemetry.js';
import { createSearchIndexes } from './search.js';
import { memo, invalidate } from './cache.js';
import { BadRequestError } from '../errors.js';

/**
 * Seeds one profile. Safe to run repeatedly: entities upsert by trackedEntityId, seed events already
 * stored are skipped (so they are not counted as duplicates), exceptions dedupe by exceptionKey,
 * invalid samples by demo.sampleId.
 */
export async function seedProfile(db, profile, now = new Date()) {
  const report = { profile: profile.id, entity: 'existing', events: {}, invalidSamples: [], telemetry: null };
  const entities = db.collection(COLL.entities);
  const fresh = buildTrackedEntity(profile, now);
  const up = await entities.updateOne({ trackedEntityId: fresh.trackedEntityId }, { $setOnInsert: fresh }, { upsert: true });
  if (up.upsertedCount) report.entity = 'created';
  const { anchorAt } = await entities.findOne({ trackedEntityId: fresh.trackedEntityId }, { projection: { anchorAt: 1 } });

  const ordered = [...profile.seedEvents].sort((a, b) => a.eventTimeOffsetMin - b.eventTimeOffsetMin);
  for (const spec of ordered) {
    const evt = buildEvent(profile, spec, { now: anchorAt, seeded: true });
    if (await db.collection(COLL.events).countDocuments(byIdempotencyKey(evt.idempotencyKey), { limit: 1 })) {
      report.events.already_present = (report.events.already_present ?? 0) + 1;
      continue;
    }
    const receivedAt = new Date(evt.eventTime.getTime() + 2 * 60000);
    const res = await ingestEvent(db, profile, evt, { receivedAt });
    report.events[res.outcome] = (report.events[res.outcome] ?? 0) + 1;
    if (res.exception?.outcome === 'opened' && spec.afterSeed) {
      const exc = await db.collection(COLL.exceptions).findOne({ exceptionKey: exceptionKeyFor(profile.id, evt.trackedEntityId, spec.exception.category) });
      for (const step of spec.afterSeed) await applyExceptionAction(db, exc.exceptionId, { action: step.action, operator: step.by });
    }
  }

  const events = db.collection(COLL.events);
  for (const spec of profile.invalidSamples) {
    const exists = await events.countDocuments({ profile: profile.id, 'demo.sampleId': spec.id, 'demo.seededInvalid': true }, { limit: 1 });
    if (exists) {
      report.invalidSamples.push({ id: spec.id, result: 'already present' });
      continue;
    }
    const doc = buildInvalidEvent(profile, spec, anchorAt);
    doc.demo.seededInvalid = true;
    try {
      await events.insertOne(doc);
      report.invalidSamples.push({ id: spec.id, result: 'inserted' });
    } catch (err) {
      report.invalidSamples.push({ id: spec.id, result: 'rejected by validator', error: serverError(err).message });
    }
  }
  report.telemetry = await seedTelemetry(db, profile, anchorAt);
  return report;
}

export async function setupDemo(db, { now = new Date(), withSearch = true } = {}) {
  invalidate(`${db.databaseName}:`);
  const setup = await ensureSetup(db);
  const seeds = [];
  for (const profile of Object.values(PROFILES)) seeds.push(await seedProfile(db, profile, now));
  const search = withSearch ? await createSearchIndexes(db) : [];
  const info = await collectionInfo(db);
  return { setup, seeds, search, validationStage: detectStage(info[COLL.events]?.options) };
}

// Explicit, confirmed reset limited to the demo collections in the configured demo database.
export async function resetDemo(db, confirmation, opts = {}) {
  if (confirmation !== db.databaseName) {
    throw new BadRequestError(`Reset not confirmed. Type the database name "${db.databaseName}" to confirm.`);
  }
  invalidate(`${db.databaseName}:`);
  const info = await collectionInfo(db);
  const dropped = [];
  for (const name of Object.values(COLL)) {
    if (name === COLL.state || !info[name]) continue;
    await db.dropCollection(name);
    dropped.push(name);
  }
  // demo_state is cleared rather than dropped so the maintenance lock held by the caller survives.
  if (info[COLL.state]) await db.collection(COLL.state).deleteMany({ _id: { $ne: 'maintenance' } });
  return { dropped, ...(await setupDemo(db, opts)) };
}

/**
 * Readiness: every collection exists with the right type, every planned index exists, and every
 * profile has its tracked entity and seed events. `problems` explains anything missing.
 */
export async function isInitialized(db) {
  const info = await collectionInfo(db);
  const missing = Object.values(COLL).filter((c) => !info[c]);
  if (missing.length) return { initialized: false, missing, problems: [`Missing collections: ${missing.join(', ')}`] };
  const problems = [];
  if (info[COLL.telemetry].type !== 'timeseries') problems.push(`${COLL.telemetry} is not a time-series collection; reset the demo database`);
  for (const coll of new Set(INDEXES.map((i) => i.coll))) {
    const names = new Set((await db.collection(coll).indexes()).map((i) => i.name));
    const absent = INDEXES.filter((i) => i.coll === coll && !names.has(i.options.name)).map((i) => i.options.name);
    if (absent.length) problems.push(`Missing indexes on ${coll}: ${absent.join(', ')}`);
  }
  const [entities, seeded] = await Promise.all([
    db.collection(COLL.entities).aggregate([{ $group: { _id: '$profile' } }]).toArray(),
    db.collection(COLL.events).aggregate([{ $match: { 'demo.seeded': true } }, { $group: { _id: '$profile' } }]).toArray(),
  ]);
  const has = (rows) => new Set(rows.map((r) => r._id));
  const [withEntity, withSeed] = [has(entities), has(seeded)];
  for (const id of Object.keys(PROFILES)) {
    if (!withEntity.has(id)) problems.push(`Profile ${id} has no tracked entity`);
    else if (!withSeed.has(id)) problems.push(`Profile ${id} has no seed events`);
  }
  return { initialized: problems.length === 0, missing, problems, profiles: withEntity.size };
}

// Every API request checks initialization; share the answer for 5 s. setupDemo and resetDemo invalidate it.
export const cachedIsInitialized = (db) => memo(`${db.databaseName}:isInitialized`, 5000, () => isInitialized(db));
