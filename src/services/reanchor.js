import { COLL } from '../db.js';
import { PROFILES } from '../profiles/index.js';
import { invalidDocumentsFilter, operationalEventSchema } from '../domain/schema.js';
import { seedTelemetry } from './telemetry.js';
import { recomputeImpact } from './impact.js';

// Adds `ms` to a field only when it holds a BSON date, so legacy string times stay untouched.
const shift = (path, ms) => ({ $cond: [{ $eq: [{ $type: path }, 'date'] }, { $add: [path, ms] }, path] });

// Shifts obj.field only when obj is an embedded document; leaves missing or null parents unchanged.
const shiftIn = (obj, field, ms) => ({
  $cond: [
    { $eq: [{ $type: `$${obj}` }, 'object'] },
    { $mergeObjects: [`$${obj}`, { [field]: shift(`$${obj}.${field}`, ms) }] },
    `$${obj}`,
  ],
});

export const eventShiftPipeline = (ms) => [
  {
    $set: {
      eventTime: shift('$eventTime', ms),
      receivedAt: shift('$receivedAt', ms),
      reliability: shiftIn('reliability', 'lastDuplicateAt', ms),
    },
  },
];

export const exceptionShiftPipeline = (ms) => [
  {
    $set: {
      slaDueAt: shift('$slaDueAt', ms),
      createdAt: shift('$createdAt', ms),
      updatedAt: shift('$updatedAt', ms),
      acknowledgedAt: shift('$acknowledgedAt', ms),
      triggeringEvent: shiftIn('triggeringEvent', 'eventTime', ms),
      resolution: shiftIn('resolution', 'at', ms),
      audit: { $map: { input: '$audit', as: 'a', in: { $mergeObjects: ['$$a', { at: { $add: ['$$a.at', ms] } }] } } },
    },
  },
];

export const entityShiftPipeline = (ms) => [
  {
    $set: {
      anchorAt: { $add: ['$anchorAt', ms] },
      plannedMilestones: {
        $map: { input: '$plannedMilestones', as: 'm', in: { $mergeObjects: ['$$m', { plannedTime: { $add: ['$$m.plannedTime', ms] } }] } },
      },
    },
  },
];

/**
 * Moves each profile's demo clock so its anchor equals `now`, preserving every relative gap
 * (lateness, SLA countdowns, dwell). Telemetry is regenerated because a time-series timeField cannot be updated.
 */
export async function reanchorDemo(db, now = new Date()) {
  const report = [];
  for (const profile of Object.values(PROFILES)) {
    const entity = await db.collection(COLL.entities).findOne({ profile: profile.id }, { projection: { anchorAt: 1 } });
    if (!entity?.anchorAt) {
      report.push({ profile: profile.id, skipped: 'not seeded' });
      continue;
    }
    const ms = now.getTime() - entity.anchorAt.getTime();
    if (Math.abs(ms) < 60000) {
      report.push({ profile: profile.id, skipped: 'already current', shiftedMin: 0 });
      continue;
    }
    const scope = { profile: profile.id };
    const events = db.collection(COLL.events);
    const valid = await events.updateMany({ ...scope, $jsonSchema: operationalEventSchema }, eventShiftPipeline(ms));
    // Invalid demo records only exist below the constraint stage, where bypass is permitted.
    const invalidScope = { ...scope, ...invalidDocumentsFilter() };
    let invalid = { modifiedCount: 0 };
    if (await events.countDocuments(invalidScope, { limit: 1 })) {
      invalid = await events.updateMany(invalidScope, eventShiftPipeline(ms), { bypassDocumentValidation: true });
    }
    const exc = await db.collection(COLL.exceptions).updateMany(scope, exceptionShiftPipeline(ms));
    await db.collection(COLL.entities).updateMany(scope, entityShiftPipeline(ms));
    const telemetry = db.collection(COLL.telemetry);
    const removed = await telemetry.deleteMany({ 'meta.trackedEntityId': profile.entity.trackedEntityId });
    const reseeded = await seedTelemetry(db, profile, now);
    const impact = await recomputeImpact(db, profile, now);
    report.push({
      profile: profile.id,
      shiftedMin: Math.round(ms / 60000),
      events: valid.modifiedCount + invalid.modifiedCount,
      exceptions: exc.modifiedCount,
      telemetry: { removed: removed.deletedCount, inserted: reseeded.inserted },
      businessImpact: impact?.level ?? null,
    });
  }
  return report;
}

// Minutes since the oldest profile anchor; used by the UI to suggest re-anchoring.
export async function demoClockAge(db, now = new Date()) {
  const [oldest] = await db.collection(COLL.entities).find({}, { projection: { anchorAt: 1 } }).sort({ anchorAt: 1 }).limit(1).toArray();
  return oldest?.anchorAt ? Math.round((now - oldest.anchorAt) / 60000) : null;
}
