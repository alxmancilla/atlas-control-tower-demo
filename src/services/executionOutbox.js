import { randomUUID } from 'node:crypto';
import { COLL } from '../db.js';
import { BadRequestError, ConflictError, NotFoundError } from '../errors.js';

const MAX_TEXT = 500;
const AUDIT_MAX = 100;
const TRANSITIONS = {
  submit: { from: ['pending'], to: 'submitted' },
  acknowledge: { from: ['submitted'], to: 'acknowledged' },
  fail: { from: ['submitted'], to: 'failed' },
  reconcile: { from: ['acknowledged', 'failed'], to: 'reconciled' },
};

const clean = (value) => (typeof value === 'string' && value.trim() ? value.trim().slice(0, MAX_TEXT) : null);
const audit = (entry) => ({ audit: { $each: [entry], $slice: -AUDIT_MAX } });

export const executionKeyFor = (profile, exceptionId, actionId) => `${profile}:${exceptionId}:${actionId}`;

export function checkExecutionAction(status, action, note) {
  const transition = TRANSITIONS[action];
  if (!transition) throw new BadRequestError(`Unknown execution action "${action}"`);
  if (!transition.from.includes(status)) throw new ConflictError(`Cannot ${action} an execution request that is ${status}`);
  if (['fail', 'reconcile'].includes(action) && !clean(note)) {
    throw new BadRequestError(`${action === 'fail' ? 'Failure' : 'Reconciliation'} requires a note`);
  }
  return { nextStatus: transition.to, note: clean(note) };
}

export function buildExecutionRequest({ profile, exception, action, decision, now = new Date() }) {
  return {
    executionId: `OUT-${randomUUID().slice(-8).toUpperCase()}`,
    executionKey: executionKeyFor(profile, exception.exceptionId, action.actionId),
    profile,
    trackedEntityId: exception.trackedEntityId,
    exceptionId: exception.exceptionId,
    actionId: action.actionId,
    status: 'pending',
    connector: { type: 'simulated', label: 'Simulation only — no external system write', externalWrite: false },
    handoff: { recommendation: { actionId: action.actionId, title: action.title ?? action.actionId, rationale: action.rationale ?? null, expectedOutcome: action.expectedOutcome ?? null }, acceptedDecision: decision },
    submissionCount: 0,
    createdAt: now,
    updatedAt: now,
    audit: [{ at: now, action: 'created', by: decision.by, note: 'Accepted recommendation queued for simulated handoff; no external system was called.' }],
  };
}

export async function createExecutionRequest(db, input, { session } = {}) {
  const request = buildExecutionRequest(input);
  await db.collection(COLL.executionOutbox).insertOne(request, { session });
  return request;
}

export async function executionQueue(db, profile) {
  return db.collection(COLL.executionOutbox).find({ profile: profile.id, trackedEntityId: profile.entity.trackedEntityId }).sort({ updatedAt: -1 }).toArray();
}

export async function applyExecutionAction(db, profile, executionId, { action, note, operator } = {}) {
  const coll = db.collection(COLL.executionOutbox);
  const current = await coll.findOne({ executionId, profile: profile.id });
  if (!current) throw new NotFoundError(`Execution request ${executionId} not found`);
  const transition = checkExecutionAction(current.status, action, note);
  const now = new Date();
  const by = clean(operator) || 'demo.operator';
  const set = { status: transition.nextStatus, updatedAt: now };
  if (action === 'submit') set.submissionCount = (current.submissionCount ?? 0) + 1;
  const updated = await coll.findOneAndUpdate(
    { executionId, profile: profile.id, status: { $in: TRANSITIONS[action].from } },
    { $set: set, $push: audit({ at: now, action, by, note: transition.note || 'Simulated lifecycle transition; no external system was called.' }) },
    { returnDocument: 'after' }
  );
  if (!updated) throw new ConflictError('The execution request changed while you were working on it. Refresh and retry.');
  return updated;
}