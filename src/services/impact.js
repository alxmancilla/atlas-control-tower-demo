import { COLL } from '../db.js';
import { assessImpact, impactSummary } from '../domain/impact.js';
import { entityOverview } from './overview.js';

// Recomputed results older than this are refreshed when the overview is read.
export const IMPACT_MAX_AGE_MS = 5 * 60 * 1000;

export async function seedPosition(db, profile, now = new Date()) {
  const { locationId, itemRef, ...rest } = profile.impact.position;
  const res = await db
    .collection(COLL.positions)
    .updateOne({ locationId, itemRef }, { $setOnInsert: { locationId, itemRef, ...rest, profile: profile.id, updatedAt: now } }, { upsert: true });
  return res.upsertedCount ? 'created' : 'existing';
}

/**
 * Computed pattern: business impact is derived on write (after each ingested event, setup, and
 * re-anchor) and stored on the tracked entity and its open exceptions, so reads and the exception
 * queue use it without recomputing or joining.
 */
export async function recomputeImpact(db, profile, now = new Date()) {
  const overview = await entityOverview(db, profile, now);
  if (!overview) return null;
  const impact = assessImpact({ assessment: overview.assessment, position: overview.position, now, cfg: profile.impact });
  const id = profile.entity.trackedEntityId;
  await db.collection(COLL.entities).updateOne({ trackedEntityId: id }, { $set: { businessImpact: impact } });
  await db
    .collection(COLL.exceptions)
    .updateMany({ trackedEntityId: id, status: { $ne: 'resolved' } }, { $set: { businessImpact: impactSummary(impact) } });
  return impact;
}

export const isStale = (impact, now = new Date()) => !impact?.computedAt || now - new Date(impact.computedAt) > IMPACT_MAX_AGE_MS;
