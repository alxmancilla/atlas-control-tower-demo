import { COLL } from '../db.js';
import { assessLoadImpact, impactSummary } from '../domain/impact.js';
import { entityOverview } from './overview.js';

// Recomputed results older than this are refreshed when the overview is read.
export const IMPACT_MAX_AGE_MS = 5 * 60 * 1000;

// One stock position per stop on the load (destination + item).
export async function seedPositions(db, profile, now = new Date()) {
  const report = { created: 0, existing: 0 };
  for (const line of profile.entity.orderLines) {
    const locationId = line.destination.id;
    const doc = { locationId, itemRef: line.itemRef, locationName: line.destination.name, unit: line.unit, ...line.position, profile: profile.id, updatedAt: now };
    const res = await db.collection(COLL.positions).updateOne({ locationId, itemRef: line.itemRef }, { $setOnInsert: doc }, { upsert: true });
    report[res.upsertedCount ? 'created' : 'existing'] += 1;
  }
  return report;
}

/**
 * Computed pattern: business impact is derived on write (after each ingested event, setup, and
 * re-anchor) and stored on the tracked entity and its open exceptions, so reads and the exception
 * queue use it without recomputing or joining.
 */
export async function recomputeImpact(db, profile, now = new Date()) {
  const overview = await entityOverview(db, profile, now);
  if (!overview) return null;
  const impact = assessLoadImpact({
    assessment: overview.assessment,
    lines: overview.entity.orderLines,
    positions: overview.positions,
    now,
    cfg: profile.impact,
  });
  const id = profile.entity.trackedEntityId;
  await db.collection(COLL.entities).updateOne({ trackedEntityId: id }, { $set: { businessImpact: impact } });
  await db
    .collection(COLL.exceptions)
    .updateMany({ trackedEntityId: id, status: { $ne: 'resolved' } }, { $set: { businessImpact: impactSummary(impact) } });
  return impact;
}

export const isStale = (impact, now = new Date()) => !impact?.computedAt || now - new Date(impact.computedAt) > IMPACT_MAX_AGE_MS;
