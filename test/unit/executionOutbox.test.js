import { test } from 'node:test';
import assert from 'node:assert/strict';
import { buildExecutionRequest, checkExecutionAction, executionKeyFor } from '../../src/services/executionOutbox.js';
import { BadRequestError, ConflictError } from '../../src/errors.js';
import { decideRecommendedAction } from '../../src/services/exceptions.js';

const now = new Date('2026-01-01T12:00:00Z');
const exception = { exceptionId: 'EXC-123', trackedEntityId: 'RTL-SHP-24017' };
const action = { actionId: 'expedite_store_1187', title: 'Expedite delivery', rationale: 'Shelf cover is low' };
const decision = { decision: 'accepted', by: 'alex', at: now };

test('accepted recommendation builds one simulated-only idempotent handoff request', () => {
  const request = buildExecutionRequest({ profile: 'retail', exception, action, decision, now });
  assert.match(request.executionId, /^OUT-[A-F0-9]{8}$/);
  assert.equal(request.executionKey, executionKeyFor('retail', 'EXC-123', 'expedite_store_1187'));
  assert.equal(request.status, 'pending');
  assert.deepEqual(request.connector, { type: 'simulated', label: 'Simulation only — no external system write', externalWrite: false });
  assert.equal(request.handoff.acceptedDecision, decision);
  assert.match(request.audit[0].note, /no external system was called/);
});

test('execution lifecycle is ordered, terminal, and requires failure/reconciliation evidence', () => {
  assert.deepEqual(checkExecutionAction('pending', 'submit'), { nextStatus: 'submitted', note: null });
  assert.deepEqual(checkExecutionAction('submitted', 'acknowledge'), { nextStatus: 'acknowledged', note: null });
  assert.deepEqual(checkExecutionAction('submitted', 'fail', 'adapter timeout'), { nextStatus: 'failed', note: 'adapter timeout' });
  assert.deepEqual(checkExecutionAction('failed', 'reconcile', 'operator confirmed manual handoff'), { nextStatus: 'reconciled', note: 'operator confirmed manual handoff' });
  assert.throws(() => checkExecutionAction('submitted', 'fail'), BadRequestError);
  assert.throws(() => checkExecutionAction('acknowledged', 'reconcile'), BadRequestError);
  assert.throws(() => checkExecutionAction('reconciled', 'submit'), ConflictError);
  assert.throws(() => checkExecutionAction('pending', 'acknowledge'), ConflictError);
});

test('acceptance records the exception decision and simulated handoff in one transaction', async () => {
  const session = { withTransaction: (work) => work() };
  const storedException = { ...exception, profile: 'retail', status: 'open', recommendedActions: [action], actionDecisions: {} };
  const exceptions = {
    async findOne(_query, options) { assert.equal(options.session, session); return storedException; },
    async findOneAndUpdate(_query, update, options) {
      assert.equal(options.session, session);
      assert.equal(update.$set['actionDecisions.expedite_store_1187'].decision, 'accepted');
      return { ...storedException, actionDecisions: { expedite_store_1187: update.$set['actionDecisions.expedite_store_1187'] } };
    },
  };
  const outbox = { async insertOne(request, options) { assert.equal(options.session, session); this.request = request; } };
  const db = { client: { withSession: (work) => work(session) }, collection: (name) => name === 'exceptions' ? exceptions : outbox };
  const result = await decideRecommendedAction(db, 'EXC-123', { actionId: 'expedite_store_1187', decision: 'accepted', operator: 'alex' });
  assert.equal(result.execution.status, 'pending');
  assert.equal(outbox.request.executionKey, 'retail:EXC-123:expedite_store_1187');
});