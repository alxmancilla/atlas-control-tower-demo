import { ObjectId } from 'mongodb';
import { COLL } from '../db.js';
import { buildQueue, checkExceptionAction, WorkflowError } from '../domain/rules.js';
import { NotFoundError } from '../errors.js';

const MAX_TEXT = 500;
// Keeps the embedded audit array bounded; the oldest entries are dropped first.
export const AUDIT_MAX = 100;
const pushAudit = (entry) => ({ audit: { $each: [entry], $slice: -AUDIT_MAX } });
const clean = (s) => (typeof s === 'string' ? s.trim().slice(0, MAX_TEXT) : null);
const addMinutes = (d, m) => new Date(new Date(d).getTime() + m * 60000);

export const exceptionKeyFor = (profileId, trackedEntityId, category) => `${profileId}:${trackedEntityId}:${category}`;

/**
 * Opens a work item for (entity, category) or links the event to the existing one.
 * The unique exceptionKey index keeps this idempotent under retries and concurrent writers.
 * With a session it joins the caller's transaction; a duplicate-key race then aborts that
 * transaction and the caller retries.
 */
export async function openOrUpdateException(db, profile, evt, { session } = {}) {
  const coll = db.collection(COLL.exceptions);
  const category = evt.exception.category;
  const cfg = profile.exceptionCategories[category];
  if (!cfg) throw new Error(`Unknown exception category "${category}" for profile ${profile.id}`);
  const exceptionKey = exceptionKeyFor(profile.id, evt.trackedEntityId, category);
  const now = new Date(evt.receivedAt ?? Date.now());
  const ref = { eventId: evt._id, source: evt.source, sourceEventId: evt.sourceEventId, eventTime: evt.eventTime };

  try {
    const res = await coll.updateOne(
      { exceptionKey },
      {
        $setOnInsert: {
          exceptionId: `EXC-${new ObjectId().toHexString().slice(-8).toUpperCase()}`,
          exceptionKey,
          trackedEntityId: evt.trackedEntityId,
          profile: profile.id,
          category,
          categoryLabel: cfg.label,
          severity: cfg.severity,
          impact: cfg.impact,
          status: 'open',
          owner: null,
          summary: `${cfg.label}: ${evt.description}`,
          triggeringEvent: ref,
          relatedEventIds: [evt._id],
          slaDueAt: addMinutes(evt.eventTime, cfg.slaMin),
          createdAt: now,
          updatedAt: now,
          resolution: null,
          audit: [{ at: now, action: 'opened', by: `source:${evt.source}`, note: evt.description, eventId: evt._id }],
        },
      },
      { upsert: true, session }
    );
    if (res.upsertedCount === 1) return { outcome: 'opened', exceptionKey };
  } catch (err) {
    // Outside a transaction, a concurrent writer created it first: fall through and link.
    if (err?.code !== 11000 || session?.inTransaction()) throw err;
  }

  const current = await coll.findOne({ exceptionKey }, { projection: { status: 1, relatedEventIds: 1 }, session });
  if (current.relatedEventIds?.some((id) => String(id) === String(evt._id))) {
    return { outcome: 'already_linked', exceptionKey };
  }
  const reopen = current.status === 'resolved';
  await coll.updateOne(
    { exceptionKey, relatedEventIds: { $ne: evt._id } },
    {
      $addToSet: { relatedEventIds: evt._id },
      $set: { updatedAt: now, ...(reopen ? { status: 'open', resolution: null } : {}) },
      $push: pushAudit({ at: now, action: reopen ? 'reopened' : 'linked_event', by: `source:${evt.source}`, note: evt.description, eventId: evt._id }),
    },
    { session }
  );
  return { outcome: reopen ? 'reopened' : 'updated', exceptionKey };
}

export async function applyExceptionAction(db, exceptionId, { action, owner, note, operator } = {}) {
  const coll = db.collection(COLL.exceptions);
  const exc = await coll.findOne({ exceptionId });
  if (!exc) throw new NotFoundError(`Exception ${exceptionId} not found`);
  const payload = { owner: clean(owner), note: clean(note) };
  const { allowedFrom, nextStatus } = checkExceptionAction(exc.status, action, payload);
  const now = new Date();
  const by = clean(operator) || 'demo.operator';
  const set = { status: nextStatus, updatedAt: now };
  if (action === 'acknowledge') set.acknowledgedAt = now;
  if (action === 'assign') set.owner = payload.owner;
  if (action === 'resolve') set.resolution = { note: payload.note, by, at: now };
  const updated = await coll.findOneAndUpdate(
    { exceptionId, status: { $in: allowedFrom } },
    { $set: set, $push: pushAudit({ at: now, action, by, note: payload.note, owner: payload.owner }) },
    { returnDocument: 'after' }
  );
  if (!updated) throw new WorkflowError('The exception changed while you were working on it. Refresh and retry.');
  return updated;
}

// The decision is separate from the recommendation. Recomputing impact can refresh the latter
// without overwriting a human's accepted/declined decision or its audit evidence.
export async function decideRecommendedAction(db, exceptionId, { actionId, decision, note, operator } = {}) {
  const coll = db.collection(COLL.exceptions);
  const exc = await coll.findOne({ exceptionId });
  if (!exc) throw new NotFoundError(`Exception ${exceptionId} not found`);
  if (exc.status === 'resolved') throw new WorkflowError('Cannot decide an action for a resolved exception');
  if (!['accepted', 'declined'].includes(decision)) throw new WorkflowError('Decision must be "accepted" or "declined"');
  const action = exc.recommendedActions?.find((candidate) => candidate.actionId === actionId);
  if (!action) throw new WorkflowError('This recommended action is no longer available. Refresh and retry.');
  if (exc.actionDecisions?.[actionId]) throw new WorkflowError('This recommended action has already been decided');
  const cleanNote = clean(note);
  if (decision === 'declined' && !cleanNote) throw new WorkflowError('Declining a recommendation requires a reason');
  const now = new Date();
  const by = clean(operator) || 'demo.operator';
  const record = { decision, by, at: now, ...(cleanNote ? { note: cleanNote } : {}) };
  const updated = await coll.findOneAndUpdate(
    { exceptionId, status: { $ne: 'resolved' }, 'recommendedActions.actionId': actionId, [`actionDecisions.${actionId}`]: { $exists: false } },
    { $set: { [`actionDecisions.${actionId}`]: record, updatedAt: now }, $push: pushAudit({ at: now, action: `recommendation_${decision}`, by, note: `${action.title}${cleanNote ? ` — ${cleanNote}` : ''}` }) },
    { returnDocument: 'after' }
  );
  if (!updated) throw new WorkflowError('The recommendation changed while you were working on it. Refresh and retry.');
  return updated;
}

// The queue and its counts cover the current tracked entity; resolved history from earlier
// shipments stays in the collection as the corpus for similar-case lookup.
const currentScope = (profile) => ({ profile: profile.id, trackedEntityId: profile.entity.trackedEntityId });

export async function exceptionQueue(db, profile, now = new Date()) {
  const list = await db.collection(COLL.exceptions).find(currentScope(profile)).toArray();
  return buildQueue(list, now, profile.priority);
}

export async function exceptionSummary(db, profile, now = new Date()) {
  const [res] = await db
    .collection(COLL.exceptions)
    .aggregate([
      { $match: currentScope(profile) },
      {
        $facet: {
          byStatus: [{ $group: { _id: '$status', n: { $sum: 1 } } }],
          openBySeverity: [{ $match: { status: { $ne: 'resolved' } } }, { $group: { _id: '$severity', n: { $sum: 1 } } }],
          slaBreached: [{ $match: { status: { $ne: 'resolved' }, slaDueAt: { $lt: now } } }, { $count: 'n' }],
        },
      },
    ])
    .toArray();
  const toMap = (arr) => Object.fromEntries(arr.map((x) => [x._id, x.n]));
  return { byStatus: toMap(res.byStatus), openBySeverity: toMap(res.openBySeverity), slaBreached: res.slaBreached[0]?.n ?? 0 };
}
