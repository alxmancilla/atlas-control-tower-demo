import { test } from 'node:test';
import assert from 'node:assert/strict';
import { assessImpact, assessLoadImpact, impactSummary } from '../../src/domain/impact.js';
import { kpiPipeline } from '../../src/services/kpis.js';
import { prioritizeException } from '../../src/domain/rules.js';
import { buildEvent, buildHistoryException, fillTemplates } from '../../src/domain/documents.js';
import { SEARCH_INDEXES, similarPipeline } from '../../src/services/search.js';
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

test('a multi-stop load is assessed per stop and takes the worst stop level', () => {
  const line = (stop, id, offsetMin) => ({ orderRef: `PO-${stop}`, stop, stopOffsetMin: offsetMin, destination: { id, name: `Store ${id}` }, itemRef: 'SKU-1', quantity: 50, unit: 'cases' });
  const lines = [line(1, 'S1', 0), line(2, 'S2', 60), line(3, 'S3', 120)];
  const positions = [
    { locationId: 'S1', itemRef: 'SKU-1', ...pos, onHand: 50 }, // 2.5 h cover, arrives in 2 h
    { locationId: 'S2', itemRef: 'SKU-1', ...pos, onHand: 200 }, // 10 h cover, arrives in 3 h
    { locationId: 'S3', itemRef: 'SKU-1', ...pos, onHand: 20 }, // 1 h cover, arrives in 4 h
  ];
  const load = assessLoadImpact({ assessment: plan({ projectedTime: at(2) }), lines, positions, now, cfg });
  assert.deepEqual(load.lines.map((l) => l.level), ['watch', 'none', 'critical']);
  assert.deepEqual([load.level, load.lineCount, load.linesAtRisk, load.unitsShort, load.unit], ['critical', 3, 1, 60, 'cases']);
  assert.match(load.reasons[0], /1 of 3 stops short: Store S3/);
  assert.equal(impactSummary(load).linesAtRisk, 1);
  const delivered = assessLoadImpact({ assessment: plan({ actualTime: at(-0.1) }), lines, positions, now, cfg });
  assert.equal(delivered.level, 'none');
  assert.equal(assessLoadImpact({ assessment: plan({ projectedTime: at(2) }), lines: [], positions, now, cfg }).lineCount, 0);
});

test('KPI pipeline is scoped to the profile and materializes one snapshot per profile with $merge', () => {
  const p = kpiPipeline(retail);
  assert.deepEqual(p[0].$match, { trackedEntityId: retail.entity.trackedEntityId, profile: 'retail' });
  assert.deepEqual(p.at(-1).$merge, { into: 'kpi_snapshots', on: '_id', whenMatched: 'replace', whenNotMatched: 'insert' });
  assert.equal(p.at(-2).$project._id, '$profile');
});

test('business impact adds explained priority points', () => {
  const exc = { severity: 'high', status: 'open', slaDueAt: at(10), impact: 3 };
  const base = prioritizeException(exc, now, retail.priority).score;
  const high = impactSummary(assessImpact({ assessment: plan({ projectedTime: at(3.8) }), position: pos, now, cfg }));
  const p = prioritizeException({ ...exc, businessImpact: high }, now, retail.priority);
  assert.equal(p.score, base + retail.priority.businessImpactPoints.high);
  assert.match(p.reasons.join('|'), /business impact high \(0\.8 h short\) \+20/);
});

test('similar-case query embeds the exception summary and pre-filters to resolved cases in its profile', () => {
  const exc = { _id: 'X1', summary: 'Missed pickup: carrier no-show', profile: 'retail' };
  const [vs, proj] = similarPipeline(exc, { limit: 3 });
  assert.equal(vs.$vectorSearch.index, 'ops_exceptions_similar');
  assert.equal(vs.$vectorSearch.query, exc.summary);
  // One extra result so the exception itself can be dropped; Atlas rejects an _id pre-filter here.
  assert.equal(vs.$vectorSearch.limit, 4);
  assert.equal(vs.$vectorSearch.numCandidates, 80);
  assert.deepEqual(vs.$vectorSearch.filter.$and, [{ status: { $eq: 'resolved' } }, { profile: { $eq: 'retail' } }]);
  assert.deepEqual(proj.$project.score, { $meta: 'vectorSearchScore' });
  const idx = SEARCH_INDEXES.find((i) => i.name === 'ops_exceptions_similar');
  assert.equal(idx.type, 'vectorSearch');
  assert.deepEqual(idx.definition.fields.map((f) => `${f.type}:${f.path}`), ['autoEmbed:summary', 'filter:status', 'filter:profile']);
});

test('history exceptions are resolved, belong to past entities, and carry a resolution', () => {
  const h = retail.history[0];
  const doc = buildHistoryException(retail, h, 0, now);
  assert.equal(doc.status, 'resolved');
  assert.notEqual(doc.trackedEntityId, retail.entity.trackedEntityId);
  assert.equal(doc.resolution.note, h.resolution);
  assert.match(doc.summary, /^Missed pickup: /);
  assert.ok(doc.createdAt < now);
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
