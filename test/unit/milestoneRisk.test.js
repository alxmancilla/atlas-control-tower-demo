import { test } from 'node:test';
import assert from 'node:assert/strict';
import { assessMilestones } from '../../src/domain/milestoneRisk.js';

const risk = { onTimeToleranceMin: 15, missedGraceMin: 30, atRiskWindowMin: 45, lateArrivalMin: 30 };
const now = new Date('2026-01-01T12:00:00Z');
const at = (min) => new Date(now.getTime() + min * 60000);
const plan = (actuals) =>
  [-300, -240, -45, 60, 180].map((off, i) => ({
    id: `m${i + 1}`,
    label: `M${i + 1}`,
    plannedTime: at(off),
    maxDwellMin: 600,
    actualTime: actuals[i] == null ? null : at(actuals[i]),
  }));

test('on-time completions and future milestones are low risk', () => {
  const r = assessMilestones(plan([-300, -238, -44]), now, risk);
  assert.equal(r.milestones[0].status, 'completed');
  assert.equal(r.milestones[3].status, 'pending');
  assert.equal(r.riskLevel, 'low');
  assert.equal(r.nextMilestone.milestoneId, 'm4');
  assert.equal(r.currentStatus.milestoneId, 'm3');
});

test('late completion is flagged and lateness carries forward to downstream projections', () => {
  const r = assessMilestones(plan([-300, -200, -5]), now, risk);
  assert.equal(r.milestones[1].status, 'completed_late');
  assert.equal(r.milestones[1].varianceMin, 40);
  assert.equal(r.milestones[3].status, 'at_risk');
  assert.match(r.milestones[3].reasons.join(' '), /upstream lateness/);
  assert.equal(r.projectedFinalLateMin, 40);
  assert.equal(r.riskLevel, 'medium');
});

test('milestone overdue but inside the grace window is at risk, not missed', () => {
  const p = plan([-300, -240]);
  p[2].plannedTime = at(-20);
  const r = assessMilestones(p, now, risk);
  assert.equal(r.milestones[2].status, 'at_risk');
  assert.match(r.milestones[2].reasons[0], /inside 30 min grace/);
});

test('missed when overdue beyond grace window', () => {
  const p = plan([-300, -240]);
  p[2].plannedTime = at(-31);
  const r = assessMilestones(p, now, risk);
  assert.equal(r.milestones[2].status, 'missed');
  assert.equal(r.riskLevel, 'high');
});

test('a skipped milestone is missed when a later one is complete, and sequence wins over arrival order', () => {
  const r = assessMilestones(plan([-300, null, -45]), now, risk);
  assert.equal(r.milestones[1].status, 'missed');
  assert.match(r.milestones[1].reasons[0], /later milestone is complete/);
  assert.equal(r.currentStatus.milestoneId, 'm3');
});

test('dwell over the limit marks the next milestone at risk', () => {
  const p = plan([-300, -240]);
  p[2].plannedTime = at(100);
  p[2].maxDwellMin = 60;
  const r = assessMilestones(p, now, risk);
  assert.equal(r.milestones[2].status, 'at_risk');
  assert.match(r.milestones[2].reasons.join(' '), /Dwell since "M2" is 240 min/);
});
