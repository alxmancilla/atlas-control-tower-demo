import { COLL } from '../db.js';
import { classifyArrival } from '../domain/rules.js';
import { buildEvent, byIdempotencyKey, idempotencyKey } from '../domain/documents.js';
import { openOrUpdateException } from './exceptions.js';
import { BadRequestError } from '../errors.js';
import { recomputeImpact } from './impact.js';
import { upsertQualityIssues } from './dataQuality.js';

const isDuplicateKey = (err, field) => err?.code === 11000 && (!field || String(err.message).includes(field));

// withTransaction retries the callback on transient errors (such as write conflicts), so callbacks
// build fresh documents on every attempt.
const inTransaction = (db, fn) => db.client.withSession((session) => session.withTransaction(() => fn(session)));

/**
 * Ingests one event in a multi-document transaction: the event and its exception work item commit
 * together or not at all. The unique idempotencyKey index makes duplicates safe: the insert fails,
 * the transaction rolls back, and a second transaction records the duplicate on the original event.
 */
export async function ingestEvent(db, profile, evt, { receivedAt = new Date() } = {}) {
  const base = { ...evt, receivedAt: new Date(receivedAt) };
  let result;
  for (let attempt = 1; !result; attempt++) {
    try {
      result = await inTransaction(db, (session) => insertNew(db, profile, { ...base }, session));
    } catch (err) {
      if (isDuplicateKey(err, 'idempotencyKey')) result = await inTransaction(db, (session) => recordDuplicate(db, profile, base, session));
      // A concurrent writer opened the same work item first; the retry finds it and links to it.
      else if (isDuplicateKey(err, 'exceptionKey') && attempt < 3) continue;
      else throw err;
    }
  }
  // Derived data is refreshed after commit; a failure here never undoes the committed event.
  try {
    result.impact = await recomputeImpact(db, profile);
  } catch (err) {
    result.impactError = err.message;
  }
  return result;
}

async function insertNew(db, profile, doc, session) {
  const events = db.collection(COLL.events);
  const [latest] = await events
    .find({ trackedEntityId: doc.trackedEntityId, eventTime: { $type: 'date' } }, { session })
    .sort({ eventTime: -1 })
    .limit(1)
    .project({ eventTime: 1 })
    .toArray();
  let milestoneRecordedAt = null;
  if (doc.milestoneId) {
    const [first] = await events
      .find({ trackedEntityId: doc.trackedEntityId, milestoneId: doc.milestoneId, eventTime: { $type: 'date' } }, { session })
      .sort({ eventTime: 1 })
      .limit(1)
      .project({ eventTime: 1 })
      .toArray();
    milestoneRecordedAt = first?.eventTime ?? null;
  }
  doc.reliability = {
    ...classifyArrival(
      { eventTime: doc.eventTime, receivedAt: doc.receivedAt, latestEventTime: latest?.eventTime, milestoneRecordedAt },
      profile.risk
    ),
    duplicateCount: 0,
  };
  const res = await events.insertOne(doc, { session });
  doc._id = res.insertedId;
  const exception = doc.exception?.category ? await openOrUpdateException(db, profile, doc, { session }) : null;
  const qualityIssues = await upsertQualityIssues(db, profile, doc, { session });
  return { outcome: doc.reliability.status, event: doc, exception, qualityIssues, transactional: true };
}

async function recordDuplicate(db, profile, doc, session) {
  const original = await db.collection(COLL.events).findOneAndUpdate(
    byIdempotencyKey(doc.idempotencyKey),
    { $inc: { 'reliability.duplicateCount': 1 }, $set: { 'reliability.lastDuplicateAt': doc.receivedAt, 'reliability.confidence': 'medium' } },
    { returnDocument: 'after', session }
  );
  // Re-linking is idempotent, so a retry after a partial failure still converges to one work item.
  const exception = original?.exception?.category ? await openOrUpdateException(db, profile, original, { session }) : null;
  const qualityIssues = await upsertQualityIssues(db, profile, original, { session });
  return { outcome: 'deduplicated', event: original, exception, qualityIssues, transactional: true };
}

export async function triggerSample(db, profile, sampleId, now = new Date()) {
  const spec = profile.samples.find((s) => s.id === sampleId);
  if (!spec) throw new BadRequestError(`Unknown sample "${sampleId}" for profile ${profile.id}`);
  const base = spec.duplicateOf ? profile.samples.find((s) => s.id === spec.duplicateOf) : spec;
  let evt = null;
  if (spec.duplicateOf) {
    evt = await db
      .collection(COLL.events)
      .findOne(byIdempotencyKey(idempotencyKey(base.source, base.sourceEventId)), { projection: { _id: 0, receivedAt: 0, reliability: 0 } });
  }
  evt ??= buildEvent(profile, base, { now });
  return ingestEvent(db, profile, evt, { receivedAt: now });
}

// A fresh, well-formed event used by the validation panel to show MongoDB accepting valid writes.
export function validProbeEvent(profile, now = new Date()) {
  const source = Object.keys(profile.sources)[0];
  return buildEvent(
    profile,
    {
      source,
      eventType: 'status_note',
      sourceEventId: `VAL-${now.getTime()}`,
      description: 'Validation check: well-formed status note',
      details: { probe: true },
    },
    { now }
  );
}

export async function timeline(db, profile, invalidFilter) {
  const events = db.collection(COLL.events);
  const id = profile.entity.trackedEntityId;
  const docs = await events
    .aggregate([
      { $match: { profile: profile.id, $or: [{ trackedEntityId: id }, { trackedEntityId: { $not: { $type: 'string' } } }] } },
      {
        $set: {
          eventTimeType: { $type: '$eventTime' },
          linked: { $eq: [{ $type: '$trackedEntityId' }, 'string'] },
        },
      },
      { $sort: { eventTime: 1, receivedAt: 1 } },
      { $limit: 300 },
    ])
    .toArray();
  const invalidIds = new Set(
    (await events.find({ _id: { $in: docs.map((d) => d._id) }, ...invalidFilter }).project({ _id: 1 }).toArray()).map((d) =>
      String(d._id)
    )
  );
  return docs.map((d) => ({ ...d, _id: String(d._id), schemaValid: !invalidIds.has(String(d._id)) }));
}

export async function reliabilitySummary(db, profile) {
  const [res] = await db
    .collection(COLL.events)
    .aggregate([
      { $match: { profile: profile.id } },
      {
        $facet: {
          byStatus: [
            {
              $group: {
                _id: { $ifNull: ['$reliability.status', 'unclassified'] },
                events: { $sum: 1 },
                duplicatesSuppressed: { $sum: { $ifNull: ['$reliability.duplicateCount', 0] } },
              },
            },
          ],
          unlinked: [{ $match: { trackedEntityId: { $not: { $type: 'string' } } } }, { $count: 'n' }],
          stringTimes: [{ $match: { eventTime: { $type: 'string' } } }, { $count: 'n' }],
        },
      },
    ])
    .toArray();
  return {
    byStatus: Object.fromEntries(res.byStatus.map((s) => [s._id, s.events])),
    duplicatesSuppressed: res.byStatus.reduce((n, s) => n + s.duplicatesSuppressed, 0),
    unlinked: res.unlinked[0]?.n ?? 0,
    stringEventTimes: res.stringTimes[0]?.n ?? 0,
  };
}
