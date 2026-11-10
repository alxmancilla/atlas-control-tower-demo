import { COLL } from '../db.js';

// The trace topology is bounded and embedded with the primary entity; source signals remain events.
export async function disruptionTrace(db, profile) {
  const entity = await db.collection(COLL.entities).findOne(
    { profile: profile.id, trackedEntityId: profile.entity.trackedEntityId },
    { projection: { _id: 0, trackedEntityId: 1, product: 1, businessImpact: 1, crossDomain: 1 } }
  );
  if (!entity) return null;
  const traceId = entity.crossDomain?.traceId;
  const signals = traceId
    ? await db.collection(COLL.events).find({ profile: profile.id, trackedEntityId: entity.trackedEntityId, 'details.crossDomainTrace': traceId }, { projection: { _id: 0, source: 1, description: 1, eventTime: 1, receivedAt: 1, reliability: 1, sourceContract: 1, details: 1 } }).sort({ eventTime: 1 }).toArray()
    : [];
  return { entity, trace: entity.crossDomain ?? null, signals };
}