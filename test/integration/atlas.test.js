import { test, before, after } from 'node:test';
import assert from 'node:assert/strict';
import { loadConfig } from '../../src/config.js';
import { connect, serverInfo, COLL } from '../../src/db.js';
import { getProfile } from '../../src/profiles/index.js';
import { resetDemo, setupDemo, isInitialized } from '../../src/services/setup.js';
import { withMaintenance, maintenanceState } from '../../src/services/maintenance.js';
import { handleApi, statusFor } from '../../src/api.js';
import { triggerSample, ingestEvent } from '../../src/services/events.js';
import { applyExceptionAction, decideRecommendedAction, exceptionQueue, AUDIT_MAX } from '../../src/services/exceptions.js';
import { entityOverview } from '../../src/services/overview.js';
import { telemetryView } from '../../src/services/telemetry.js';
import { insertInvalidSample, repairInvalid, removeInvalid, transitionStage, findInvalidEvents } from '../../src/services/validation.js';
import { findSimilar, searchStatus } from '../../src/services/search.js';
import { ChangeFeed } from '../../src/services/changeFeed.js';
import { reanchorDemo, demoClockAge } from '../../src/services/reanchor.js';
import { IMPACT_LEVELS } from '../../src/domain/impact.js';
import { kpiSnapshot, refreshKpis } from '../../src/services/kpis.js';
import { portfolioOverview } from '../../src/services/portfolio.js';
import { entityPipeline } from '../../src/services/overview.js';
import { buildEvent, byIdempotencyKey, idempotencyKey } from '../../src/domain/documents.js';

// Runs against a real Atlas cluster only. No mocks, no local substitute.
const skip = process.env.MONGODB_URI ? false : 'MONGODB_URI not set: Atlas integration tests skipped (see README "Testing")';
const profile = getProfile('retail');
let client;
let db;
let info;

before(async () => {
  if (skip) return;
  const config = loadConfig({ ...process.env, DEMO_DB_NAME: process.env.TEST_DB_NAME || 'atlas_control_tower_demo_test' });
  ({ client, db } = await connect(config));
  info = await serverInfo(db);
  await resetDemo(db, db.databaseName, { withSearch: false });
});
after(async () => client?.close());

test('setup creates collections, a time-series telemetry collection, and seeds every profile', { skip }, async () => {
  const colls = Object.fromEntries((await db.listCollections().toArray()).map((c) => [c.name, c.type]));
  assert.equal(colls[COLL.telemetry], 'timeseries');
  assert.equal(await db.collection(COLL.entities).countDocuments(), 12);
  const again = await resetDemo(db, db.databaseName, { withSearch: false });
  assert.equal(await db.collection(COLL.entities).countDocuments(), 12, 'reset + reseed stays idempotent');
  assert.ok(again.dropped.includes(COLL.events));
});

test('portfolio lists the primary load and synthetic active-load context in business-impact order', { skip }, async () => {
  const portfolio = await portfolioOverview(db, profile);
  assert.deepEqual(portfolio.summary, { total: 4, critical: 0, high: 2, watch: 1, openExceptions: 3 });
  assert.equal(portfolio.loads[0].trackedEntityId, profile.entity.trackedEntityId, 'the primary high-impact load has two open exceptions');
  assert.equal(portfolio.loads[0].interactive, true);
  const delayed = portfolio.loads.find((load) => load.trackedEntityId === 'RTL-SHP-24019');
  assert.equal(delayed.nearestException.owner, 'planner.lee');
  assert.match(delayed.lastTrustedSignal.description, /delay at cross-dock/);
});

test('re-running setup on seeded data is a no-op: seed events are not counted as duplicates', { skip }, async () => {
  const dupTotal = async () =>
    (await db.collection(COLL.events).aggregate([{ $group: { _id: null, n: { $sum: { $ifNull: ['$reliability.duplicateCount', 0] } } } }]).toArray())[0].n;
  const [events, exceptions, before] = [await db.collection(COLL.events).countDocuments(), await db.collection(COLL.exceptions).countDocuments(), await dupTotal()];
  const res = await setupDemo(db, { withSearch: false });
  assert.ok(res.seeds.every((s) => s.events.already_present === getProfile(s.profile).seedEvents.length && !s.events.deduplicated));
  assert.equal(await dupTotal(), before);
  assert.equal(await db.collection(COLL.events).countDocuments(), events);
  assert.equal(await db.collection(COLL.exceptions).countDocuments(), exceptions);
});

test('duplicate event is suppressed and does not create a second exception', { skip }, async () => {
  const first = await triggerSample(db, profile, 'delay_alert');
  assert.equal(first.exception.outcome, 'opened');
  const second = await triggerSample(db, profile, 'delay_alert');
  assert.equal(second.outcome, 'deduplicated');
  assert.equal(second.event.reliability.duplicateCount, 1);
  const sameCategory = await triggerSample(db, profile, 'delay_store_report');
  assert.equal(sameCategory.exception.outcome, 'updated');
  const excs = await db.collection(COLL.exceptions).find({ trackedEntityId: profile.entity.trackedEntityId, category: 'delivery_delay' }).toArray();
  assert.equal(excs.length, 1);
  assert.equal(excs[0].relatedEventIds.length, 2);
});

test('late out-of-order event is labeled and does not move the milestone timeline backward', { skip }, async () => {
  await triggerSample(db, profile, 'arrived_dc');
  const before = await entityOverview(db, profile);
  const res = await triggerSample(db, profile, 'late_departure_scan');
  assert.equal(res.outcome, 'out_of_order');
  assert.ok(res.event.reliability.flags.includes('late'));
  const afterOv = await entityOverview(db, profile);
  assert.equal(afterOv.assessment.currentStatus.milestoneId, before.assessment.currentStatus.milestoneId);
  const departed = afterOv.assessment.milestones.find((m) => m.id === 'departed');
  assert.equal(departed.reports, 2);
});

test('recommended actions record an auditable operator decision exactly once', { skip }, async () => {
  const exc = await db.collection(COLL.exceptions).findOne({ trackedEntityId: profile.entity.trackedEntityId, category: 'delivery_delay' });
  assert.ok(exc.recommendedActions?.length, JSON.stringify(exc));
  const action = exc.recommendedActions[0];
  await assert.rejects(
    decideRecommendedAction(db, exc.exceptionId, { actionId: action.actionId, decision: 'declined', operator: 'tester' }),
    /requires a reason/
  );
  const updated = await decideRecommendedAction(db, exc.exceptionId, { actionId: action.actionId, decision: 'accepted', operator: 'tester', note: 'Expedite request sent' });
  assert.equal(updated.actionDecisions[action.actionId].decision, 'accepted');
  assert.equal(updated.actionDecisions[action.actionId].by, 'tester');
  assert.match(updated.audit.at(-1).action, /recommendation_accepted/);
  await assert.rejects(
    decideRecommendedAction(db, exc.exceptionId, { actionId: action.actionId, decision: 'accepted', operator: 'tester' }),
    /already been decided/
  );
});

test('exception workflow: acknowledge, assign, resolve with audit trail', { skip }, async () => {
  const [top] = await exceptionQueue(db, profile);
  await applyExceptionAction(db, top.exceptionId, { action: 'acknowledge', operator: 'tester' });
  await applyExceptionAction(db, top.exceptionId, { action: 'assign', owner: 'planner.a', operator: 'tester' });
  await assert.rejects(applyExceptionAction(db, top.exceptionId, { action: 'resolve', note: '' }), /resolution note/);
  const done = await applyExceptionAction(db, top.exceptionId, { action: 'resolve', note: 'Rerouted', operator: 'tester' });
  assert.equal(done.status, 'resolved');
  assert.deepEqual(done.audit.slice(-3).map((a) => a.action), ['acknowledge', 'assign', 'resolve']);

  // The embedded audit array stays bounded: the newest AUDIT_MAX entries are kept.
  let last;
  for (let i = 1; i <= AUDIT_MAX + 5; i++) {
    last = await applyExceptionAction(db, top.exceptionId, { action: 'note', note: `follow-up ${i}`, operator: 'tester' });
  }
  assert.equal(last.audit.length, AUDIT_MAX);
  assert.equal(last.audit.at(-1).note, `follow-up ${AUDIT_MAX + 5}`);
  assert.equal(last.audit[0].note, 'follow-up 6');
});

test('telemetry aggregation reports out-of-range readings from the time-series collection', { skip }, async () => {
  const t = await telemetryView(db, profile, { hours: 12 });
  assert.ok(t.stats.count > 0);
  assert.ok(t.stats.outOfRange >= 2);
  assert.ok(t.excursions.every((e) => e.value > t.range.max || e.value < t.range.min));
});

test('re-anchoring moves the demo clock while preserving every relative gap', { skip }, async () => {
  const snapshot = async (now) => {
    const ov = await entityOverview(db, profile, now);
    const queue = await exceptionQueue(db, profile, now);
    return {
      milestones: ov.assessment.milestones.map((m) => `${m.id}:${m.status}:${m.varianceMin}`),
      sla: queue.map((e) => `${e.exceptionId}:${e.priority.minutesToSla}`).sort(),
      invalid: (await findInvalidEvents(db)).count,
    };
  };
  assert.equal((await transitionStage(db, 'enforce', info.gate)).stage, 'enforce');
  const t0 = new Date();
  const anchor = (await db.collection(COLL.entities).findOne({ profile: profile.id })).anchorAt;
  // Re-anchoring preserves gaps relative to the anchor, so compare snapshots taken at the same anchor offset.
  const offsetMs = t0 - anchor;
  const original = await snapshot(t0);
  assert.ok(original.invalid > 0, 'invalid legacy records must be present so the bypass path is exercised');
  const past = new Date(t0.getTime() - 3 * 3600000);
  await reanchorDemo(db, past);
  assert.equal(await demoClockAge(db, t0), 180);
  const drifted = await snapshot(t0);
  assert.notDeepEqual(drifted.milestones, original.milestones, 'a 3 h old anchor should look worse');
  const report = await reanchorDemo(db, t0);
  assert.equal(report.find((r) => r.profile === 'retail').shiftedMin, 180);
  assert.deepEqual(await snapshot(new Date(t0.getTime() + offsetMs)), original);
  assert.ok((await telemetryView(db, profile, { hours: 8 }, t0)).stats.count > 0);
  assert.equal((await reanchorDemo(db, t0)).every((r) => r.skipped === 'already current'), true);
  assert.equal((await transitionStage(db, 'flexible', info.gate)).stage, 'flexible');
});

test('validation stages: observe accepts, enforce rejects, repair and remove clear invalid records', { skip }, async () => {
  assert.equal((await transitionStage(db, 'observe', info.gate)).stage, 'observe');
  assert.equal((await insertInvalidSample(db, profile, 'legacy_string_time')).ok, true);
  assert.equal((await transitionStage(db, 'enforce', info.gate)).stage, 'enforce');
  const rejected = await insertInvalidSample(db, profile, 'legacy_missing_entity');
  assert.equal(rejected.ok, false);
  assert.equal(rejected.error.code, 121);
  for (const d of (await findInvalidEvents(db)).docs) {
    const r = await repairInvalid(db, d._id);
    if (!r.ok) await removeInvalid(db, d._id);
  }
  assert.equal((await findInvalidEvents(db)).count, 0);
});

test('constraint stage follows the server version gate', { skip }, async () => {
  const res = await transitionStage(db, 'constraint', info.gate);
  if (!info.gate.supported) {
    assert.equal(res.ok, false);
    assert.match(res.error.message, /9\.0/);
    return;
  }
  assert.equal(res.stage, 'constraint', JSON.stringify(res.error));
  assert.equal((await insertInvalidSample(db, profile, 'legacy_missing_entity')).ok, false);
  assert.equal((await transitionStage(db, 'flexible', info.gate)).stage, 'flexible');
});

test('change stream delivers inserts to the feed', { skip }, async () => {
  const feed = new ChangeFeed(db).start();
  try {
    await new Promise((resolve, reject) => {
      const t = setTimeout(() => reject(new Error('change stream did not become live')), 15000);
      feed.on('status', (s) => s.state === 'live' && (clearTimeout(t), resolve()));
    });
    const got = new Promise((resolve) => feed.on('change', (c) => c.coll === COLL.events && c.op === 'insert' && resolve(c)));
    await triggerSample(db, profile, 'out_for_delivery');
    const change = await Promise.race([got, new Promise((_, r) => setTimeout(() => r(new Error('no change event')), 15000))]);
    assert.equal(change.profile, 'retail');
  } finally {
    await feed.stop();
  }
});

test('duplicate lookup and overview exception join use indexes, not collection scans', { skip }, async () => {
  const stages = (node, acc = []) => {
    if (!node) return acc;
    acc.push(node.stage + (node.indexName ? `:${node.indexName}` : ''));
    stages(node.inputStage, acc);
    for (const c of node.inputStages ?? []) stages(c, acc);
    return acc;
  };
  const key = idempotencyKey(profile.samples[0].source, profile.samples[0].sourceEventId);
  const ex = await db.collection(COLL.events).find(byIdempotencyKey(key)).explain('queryPlanner');
  const plan = stages(ex.queryPlanner.winningPlan.queryPlan ?? ex.queryPlanner.winningPlan);
  assert.ok(plan.some((s) => s.endsWith(':idempotencyKey_unique')), plan.join(' <- '));
  const ov = await db.collection(COLL.entities).aggregate(entityPipeline(profile)).explain('executionStats');
  const joins = ov.stages.filter((s) => s.$lookup);
  for (const j of joins) assert.equal(j.collectionScans, 0, `$lookup into ${j.$lookup.from} scanned the collection`);
  assert.ok(joins.find((j) => j.$lookup.from === COLL.exceptions).indexesUsed.includes('entity_status'));
});

test('readiness check reports a missing index, and setup repairs it', { skip }, async () => {
  assert.equal((await isInitialized(db)).initialized, true);
  await db.collection(COLL.exceptions).dropIndex('entity_status');
  const broken = await isInitialized(db);
  assert.equal(broken.initialized, false);
  assert.match(broken.problems.join(' '), /Missing indexes on exceptions: entity_status/);
  await setupDemo(db, { withSearch: false });
  assert.equal((await isInitialized(db)).initialized, true);
});

test('maintenance lock: one holder at a time, API pauses, released on completion', { skip }, async () => {
  const app = { config: { defaultProfile: 'retail' }, info, ensureConnected: async () => db };
  const call = (method, path, body = {}) =>
    handleApi(app, method, new URL(`http://localhost${path}`), body).then(
      () => 200,
      (err) => statusFor(err)
    );
  let release;
  const running = withMaintenance(db, 'reanchor', () => new Promise((r) => (release = r)));
  await new Promise((r) => setTimeout(r, 500));
  assert.equal((await maintenanceState(db)).active, true);
  await assert.rejects(withMaintenance(db, 'setup', async () => {}), /Another maintenance operation \(reanchor\)/);
  assert.equal(await call('GET', '/api/overview?profile=retail'), 503);
  assert.equal(await call('GET', '/api/profiles'), 200);
  release();
  await running;
  assert.equal((await maintenanceState(db)).active, false);
  assert.equal(await call('GET', '/api/overview?profile=retail'), 200);
});

test('API returns 4xx for caller mistakes', { skip }, async () => {
  const app = { config: { defaultProfile: 'retail' }, info, ensureConnected: async () => db };
  const call = (method, path, body = {}) =>
    handleApi(app, method, new URL(`http://localhost${path}`), body).then(
      () => 200,
      (err) => statusFor(err)
    );
  assert.equal(await call('POST', '/api/events/sample', { sampleId: 'nope' }), 400);
  assert.equal(await call('POST', '/api/exceptions/EXC-NOPE/action', { action: 'acknowledge' }), 404);
  assert.equal(await call('POST', '/api/reset', { confirm: 'wrong' }), 400);
  assert.equal(await call('GET', '/api/telemetry?profile=retail&hours=abc'), 400);
  assert.equal(await call('GET', '/api/telemetry?profile=retail&min=5&max=1'), 400);
  assert.equal(await call('POST', '/api/telemetry/reading', { kind: 'weird' }), 400);
  assert.equal(await call('POST', '/api/validation/stage', { stage: 'bogus' }), 400);
  assert.equal(await call('POST', '/api/validation/repair', { id: 'not-an-id' }), 400);
  assert.equal(await call('POST', '/api/validation/remove', { id: '000000000000000000000000' }), 404);
  assert.equal(await call('GET', '/api/overview?profile=unknown'), 400);
  assert.equal(await call('POST', '/api/setup', ['not', 'an', 'object']), 400);
});

test('ingest is transactional: a failure after the event insert rolls the event back', { skip }, async () => {
  const bad = buildEvent(profile, { source: 'carrier', eventType: 'exception', sourceEventId: `TXN-ROLLBACK-${Date.now()}`,
    exception: { category: 'no_such_category' }, description: 'rollback probe' }, { now: new Date() });
  await assert.rejects(ingestEvent(db, profile, bad), /Unknown exception category/);
  assert.equal(await db.collection(COLL.events).countDocuments(byIdempotencyKey(bad.idempotencyKey)), 0, 'event must not survive the aborted transaction');

  const feed = new ChangeFeed(db).start();
  try {
    await new Promise((resolve) => feed.on('status', (s) => s.state === 'live' && resolve()));
    const seen = [];
    const both = new Promise((resolve) =>
      feed.on('change', (c) => {
        if (c.txn && (c.coll === COLL.events || c.coll === COLL.exceptions)) seen.push(c);
        if (new Set(seen.map((x) => x.coll)).size === 2) resolve();
      })
    );
    // A new report for an existing work item: the event insert and the exception update share one transaction.
    const report = buildEvent(profile, { source: 'store_receiving', eventType: 'exception', sourceEventId: `TXN-LINK-${Date.now()}`,
      exception: { category: 'delivery_delay' }, description: 'Store follow-up on delayed replenishment' }, { now: new Date() });
    const res = await ingestEvent(db, profile, report);
    assert.equal(res.transactional, true);
    assert.ok(['opened', 'updated', 'reopened'].includes(res.exception.outcome), res.exception.outcome);
    await Promise.race([both, new Promise((_, r) => setTimeout(() => r(new Error('no transactional change events')), 15000))]);
    assert.equal(new Set(seen.map((c) => c.txn)).size, 1, 'event and exception share one transaction');
  } finally {
    await feed.stop();
  }
});

test('business impact is computed on write and ranks exceptions', { skip }, async () => {
  const entity = await db.collection(COLL.entities).findOne({ trackedEntityId: profile.entity.trackedEntityId });
  assert.ok(IMPACT_LEVELS.includes(entity.businessImpact.level), JSON.stringify(entity.businessImpact));
  const lines = entity.businessImpact.lines;
  assert.equal(lines.length, profile.entity.orderLines.length, 'one impact row per order line on the load');
  assert.deepEqual(lines.map((l) => l.coverHours), [3, 6.1, 4.7], 'cover comes from each stop\'s joined inventory position');
  assert.ok(lines[2].arrivalAt - lines[0].arrivalAt === 90 * 60000, 'later stops arrive after their stop offset');
  const open = await db.collection(COLL.exceptions).find({ trackedEntityId: profile.entity.trackedEntityId, status: { $ne: 'resolved' } }).toArray();
  assert.ok(open.length > 0 && open.every((e) => e.businessImpact?.level === entity.businessImpact.level && e.businessImpact.lineCount === 3));
  const [top] = await exceptionQueue(db, profile);
  if (['high', 'critical'].includes(entity.businessImpact.level)) assert.match(top.priority.reasons.join(' '), /business impact/);

  for (const id of ['out_for_delivery', 'received']) await triggerSample(db, profile, id);
  const after = await db.collection(COLL.entities).findOne({ trackedEntityId: profile.entity.trackedEntityId });
  assert.equal(after.businessImpact.level, 'none');
  assert.match(after.businessImpact.reasons[0], /supply restored/);
});

test('KPI snapshot is materialized with $merge and served from kpi_snapshots until stale', { skip }, async () => {
  const fresh = await kpiSnapshot(db, profile, { maxAgeMs: 0 });
  assert.equal(fresh.cached, false);
  assert.equal(fresh._id, 'retail');
  assert.ok(fresh.eventQuality.events > 0 && fresh.milestoneAdherence.completed > 0, JSON.stringify(fresh));
  assert.ok(fresh.exceptions.resolvedCount >= profile.history.length, 'resolved history feeds MTTR');
  assert.ok(fresh.sourceReliability.some((s) => s.source === 'carrier'));
  assert.ok(fresh.actionAdoption.recommended > 0);
  assert.ok(fresh.actionAdoption.accepted > 0);
  assert.equal(await db.collection(COLL.kpis).countDocuments({ _id: 'retail' }), 1);
  const cached = await kpiSnapshot(db, profile);
  assert.equal(cached.cached, true);
  assert.deepEqual(cached.computedAt, fresh.computedAt);
  await refreshKpis(db, profile);
  assert.equal(await db.collection(COLL.kpis).countDocuments({ _id: 'retail' }), 1, '$merge replaces, never duplicates');
});

test('resolved history is seeded per profile and kept out of the current queue', { skip }, async () => {
  for (const id of ['retail', 'healthcare', 'manufacturing']) {
    const p = getProfile(id);
    const hist = await db.collection(COLL.exceptions).countDocuments({ profile: id, 'demo.history': true, status: 'resolved' });
    assert.equal(hist, p.history.length, id);
    const queue = await exceptionQueue(db, p);
    assert.ok(queue.every((e) => e.trackedEntityId === p.entity.trackedEntityId), `${id} queue must not include history`);
  }
});

test('search status reports each feature, and similar-case lookup refuses cleanly until its index is queryable', { skip }, async () => {
  const s = await searchStatus(db);
  assert.equal(s.indexes.length, 3);
  assert.equal(typeof s.ready, 'boolean');
  assert.equal(typeof s.similar.ready, 'boolean');
  const [top] = await exceptionQueue(db, profile);
  if (s.similar.ready) {
    const res = await findSimilar(db, top.exceptionId);
    assert.ok(res.results.every((r) => r.resolution?.note));
  } else {
    await assert.rejects(findSimilar(db, top.exceptionId), (err) => statusFor(err) === 503 && /Similar-case lookup/.test(err.message));
  }
  await assert.rejects(findSimilar(db, 'EXC-NOPE'), (err) => statusFor(err) === 404);
});
