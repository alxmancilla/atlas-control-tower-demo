import { ObjectId } from 'mongodb';
import { COLL } from '../db.js';
import { BadRequestError, ConflictError, NotFoundError } from '../errors.js';

const AUDIT_MAX = 100;
const ISSUE_TYPES = {
  late: { label: 'Late source event', severity: 'medium' },
  out_of_order: { label: 'Out-of-order event', severity: 'high' },
  future_event_time: { label: 'Future event timestamp', severity: 'high' },
  milestone_earlier_report: { label: 'Earlier milestone report', severity: 'medium' },
  milestone_already_recorded: { label: 'Repeated milestone report', severity: 'low' },
  duplicate_suppressed: { label: 'Duplicate source event suppressed', severity: 'low' },
};

const clean = (value) => (typeof value === 'string' ? value.trim().slice(0, 500) : '');
const pushAudit = (entry) => ({ audit: { $each: [entry], $slice: -AUDIT_MAX } });

export function qualityIssueTypes(event) {
  const flags = event.reliability?.flags ?? [];
  const types = flags.filter((flag) => ISSUE_TYPES[flag]);
  if ((event.reliability?.duplicateCount ?? 0) > 0) types.push('duplicate_suppressed');
  return [...new Set(types)];
}

// Called from the event transaction. The unique key makes retries and repeated duplicates converge.
export async function upsertQualityIssues(db, profile, event, { session } = {}) {
  const now = new Date(event.receivedAt ?? Date.now());
  const issues = [];
  for (const type of qualityIssueTypes(event)) {
    const config = ISSUE_TYPES[type];
    const issueKey = `${profile.id}:${event._id}:${type}`;
    const reliability = { status: event.reliability?.status, flags: event.reliability?.flags ?? [], lagMinutes: event.reliability?.lagMinutes, duplicateCount: event.reliability?.duplicateCount ?? 0, confidence: type === 'duplicate_suppressed' ? 'medium' : event.reliability?.confidence ?? 'medium' };
    const update = {
      $setOnInsert: {
        issueId: `DQ-${new ObjectId().toHexString().slice(-8).toUpperCase()}`,
        issueKey,
        profile: profile.id,
        trackedEntityId: event.trackedEntityId,
        eventId: event._id,
        source: event.source,
        issueType: type,
        issueLabel: config.label,
        severity: config.severity,
        eventTime: event.eventTime,
        receivedAt: event.receivedAt,
        createdAt: now,
        audit: [{ at: now, action: 'opened', by: `source:${event.source}`, note: config.label, eventId: event._id }],
      },
      $set: { status: 'open', resolution: null, reliability, updatedAt: now, observedCount: reliability.duplicateCount || 1 },
    };
    const result = await db.collection(COLL.qualityIssues).updateOne({ issueKey }, update, { upsert: true, session });
    issues.push({ issueKey, issueType: type, outcome: result.upsertedCount ? 'opened' : 'updated' });
  }
  return issues;
}

export async function dataQualityQueue(db, profile) {
  return db.collection(COLL.qualityIssues)
    .find({ profile: profile.id, trackedEntityId: profile.entity.trackedEntityId })
    .sort({ status: 1, severity: -1, updatedAt: -1 })
    .toArray();
}

export async function applyQualityIssueAction(db, issueId, { action, note, operator } = {}) {
  if (!['acknowledge', 'resolve'].includes(action)) throw new BadRequestError(`Unknown data-quality action "${action}"`);
  const issue = await db.collection(COLL.qualityIssues).findOne({ issueId });
  if (!issue) throw new NotFoundError(`Data-quality issue ${issueId} not found`);
  if (issue.status === 'resolved') throw new ConflictError(`Cannot ${action} a data-quality issue that is resolved`);
  const by = clean(operator) || 'demo.operator';
  const resolution = clean(note);
  if (action === 'resolve' && !resolution) throw new BadRequestError('Resolve requires a resolution note');
  const now = new Date();
  const status = action === 'resolve' ? 'resolved' : 'acknowledged';
  const updated = await db.collection(COLL.qualityIssues).findOneAndUpdate(
    { issueId, status: { $ne: 'resolved' } },
    { $set: { status, updatedAt: now, ...(action === 'resolve' ? { resolution: { note: resolution, by, at: now } } : { acknowledgedAt: now }) }, $push: pushAudit({ at: now, action, by, note: resolution || null }) },
    { returnDocument: 'after' }
  );
  if (!updated) throw new ConflictError('The data-quality issue changed while you were working on it. Refresh and retry.');
  return updated;
}