import { test } from 'node:test';
import assert from 'node:assert/strict';
import { assessImpact, impactSummary } from '../../src/domain/impact.js';
import { prioritizeException } from '../../src/domain/rules.js';
import { buildEvent, fillTemplates } from '../../src/domain/documents.js';
import retail from '../../src/profiles/retail.js';

const now = new Date('2026-11-05T12:00:00Z');
const at = (h) => new Date(now.getTime() + h * 3600000);
const cfg = { consequence: 'shelf empty', criticalShortfallHours: 2, watchMarginHours: 1 };
const pos = { onHand: 60, unit: 'cases', consumptionPerHour: 20 }; // 3 h of cover
const plan = (final) => ({ milestones: [{ label: 'Departed', actualTime: at(-2) }, { label: 'Received', plannedTime: at(2), ...final }] });

test('impact levels follow cover versus projected arrival', () => {
  assert.equal(assessImpact({ assessment: plan({ projectedTime: at(1) }), position: pos, now, cfg }).level, 'none');
  const watch = assessImpact({ assessment: plan({ projectedTime: at(2.5) }), position: pos, now, cfg });
  assert.equal(watch.level, 'watch');
  assert.match(watch.reasons.join(' '), /0\.5 h of margin/);
  const high = assessImpact({ assessment: plan({ projectedTime: at(3.8) }), position: pos, now, cfg });
  assert.deepEqual([high.level, high.shortfallHours, high.unitsShort, high.coverHours], ['high', 0.8, 16, 3]);
  assert.match(high.reasons.join(' '), /0\.8 h short: shelf empty/);
  assert.equal(assessImpact({ assessment: plan({ projectedTime: at(5.5) }), position: pos, now, cfg }).level, 'critical');
});

test('delivered shipments and missing positions carry no impact', () => {
  assert.equal(assessImpact({ assessment: plan({ actualTime: at(-0.1) }), position: pos, now, cfg }).level, 'none');
  assert.equal(assessImpact({ assessment: plan({ projectedTime: at(9) }), position: null, now, cfg }).level, 'none');
});

test('business impact adds explained priority points', () => {
  const exc = { severity: 'high', status: 'open', slaDueAt: at(10), impact: 3 };
  const base = prioritizeException(exc, now, retail.priority).score;
  const high = impactSummary(assessImpact({ assessment: plan({ projectedTime: at(3.8) }), position: pos, now, cfg }));
  const p = prioritizeException({ ...exc, businessImpact: high }, now, retail.priority);
  assert.equal(p.score, base + retail.priority.businessImpactPoints.high);
  assert.match(p.reasons.join('|'), /business impact high \(0\.8 h short\) \+20/);
});

test('raw source payloads carry the envelope eventTime in their own format', () => {
  const when = new Date('2026-11-05T14:05:00Z');
  assert.deepEqual(fillTemplates({ a: 'AT7*X3***{{date}}*{{time}}', b: ['{{iso}}'], n: 3 }, when), {
    a: 'AT7*X3***20261105*1405',
    b: ['2026-11-05T14:05:00.000Z'],
    n: 3,
  });
  const arrived = buildEvent(retail, retail.samples.find((s) => s.id === 'arrived_dc'), { now: when });
  assert.equal(arrived.channel, 'edi');
  assert.match(arrived.details.raw, /^ST\*214\*.*AT7\*X3\*NS\*\*\*20261105\*1405/);
  const ofd = buildEvent(retail, retail.samples.find((s) => s.id === 'out_for_delivery'), { now: when });
  assert.equal(ofd.channel, 'gps');
  assert.equal(ofd.details.raw.fix, when.toISOString());
});
