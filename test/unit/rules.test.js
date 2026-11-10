import { test } from 'node:test';
import assert from 'node:assert/strict';
import { prioritizeException, buildQueue, checkExceptionAction, WorkflowError, classifyArrival, constraintGate } from '../../src/domain/rules.js';
import retail from '../../src/profiles/retail.js';

const now = new Date('2026-01-01T12:00:00Z');
const at = (min) => new Date(now.getTime() + min * 60000);
const rules = retail.priority;

test('priority combines severity, SLA urgency, impact, and acknowledgement with explanations', () => {
  const p = prioritizeException({ severity: 'high', status: 'open', slaDueAt: at(-10), impact: 3 }, now, rules);
  assert.equal(p.score, 35 + 40 + 15);
  assert.equal(p.minutesToSla, -10);
  assert.match(p.reasons.join('|'), /SLA breached 10 min ago/);
  const ack = prioritizeException({ severity: 'high', status: 'acknowledged', slaDueAt: at(30), impact: 3 }, now, rules);
  assert.equal(ack.score, 35 + 25 + 15 - 5);
});

test('queue puts breached critical work first and resolved work last', () => {
  const q = buildQueue(
    [
      { exceptionId: 'low', severity: 'low', status: 'open', slaDueAt: at(400), impact: 1 },
      { exceptionId: 'done', severity: 'critical', status: 'resolved', slaDueAt: at(-500), impact: 4 },
      { exceptionId: 'hot', severity: 'high', status: 'open', slaDueAt: at(-5), impact: 3 },
    ],
    now,
    rules
  );
  assert.deepEqual(q.map((e) => e.exceptionId), ['hot', 'low', 'done']);
});

test('workflow transitions enforce allowed states and required fields', () => {
  assert.equal(checkExceptionAction('open', 'acknowledge').nextStatus, 'acknowledged');
  assert.equal(checkExceptionAction('acknowledged', 'resolve', { note: 'fixed' }).nextStatus, 'resolved');
  assert.throws(() => checkExceptionAction('resolved', 'acknowledge'), WorkflowError);
  assert.throws(() => checkExceptionAction('open', 'resolve', { note: '  ' }), /resolution note/);
  assert.throws(() => checkExceptionAction('open', 'assign', {}), /owner/);
  assert.equal(checkExceptionAction('resolved', 'note', { note: 'follow-up' }).nextStatus, 'resolved');
  assert.throws(() => checkExceptionAction('open', 'delete'), /Unknown action/);
});

test('arrival classification detects late, out-of-order, and repeated milestones', () => {
  const risk = retail.risk;
  assert.deepEqual(classifyArrival({ eventTime: at(-2), receivedAt: now }, risk), { status: 'accepted', flags: [], lagMinutes: 2, confidence: 'high' });
  const late = classifyArrival({ eventTime: at(-190), receivedAt: now, latestEventTime: at(-10), milestoneRecordedAt: at(-200) }, risk);
  assert.equal(late.status, 'out_of_order');
  assert.equal(late.confidence, 'low');
  assert.deepEqual(late.flags.sort(), ['late', 'milestone_already_recorded', 'out_of_order']);
  const earlier = classifyArrival({ eventTime: at(-250), receivedAt: now, milestoneRecordedAt: at(-200) }, risk);
  assert.ok(earlier.flags.includes('milestone_earlier_report'));
  assert.equal(earlier.status, 'late');
});

test('constraint gate enables only MongoDB 9.0 and later', () => {
  assert.equal(constraintGate('9.0.4').supported, true);
  assert.equal(constraintGate('10.1.0').supported, true);
  const old = constraintGate('8.0.12');
  assert.equal(old.supported, false);
  assert.match(old.reason, /8\.0\.12.*9\.0/);
  assert.equal(constraintGate(undefined).supported, false);
});
