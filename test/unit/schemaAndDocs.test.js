import { test } from 'node:test';
import assert from 'node:assert/strict';
import { detectStage, planTransition, stageCommand } from '../../src/domain/schema.js';
import { buildEvent, buildInvalidEvent, buildPortfolioEntities, buildPortfolioException, buildTrackedEntity, planRepair } from '../../src/domain/documents.js';
import { PROFILE_IDS, getProfile, validateProfile } from '../../src/profiles/index.js';
import { assertDemoDbName, isAtlasHost, loadConfig, redact, targetLabel } from '../../src/config.js';
import { matchRoute } from '../../src/api.js';

const now = new Date('2026-01-01T12:00:00Z');
const C = 'operational_events';

test('stage detection follows collection options', () => {
  assert.equal(detectStage({}), 'flexible');
  assert.equal(detectStage({ validator: {}, validationLevel: 'off' }), 'flexible');
  assert.equal(detectStage({ validator: { $jsonSchema: {} }, validationLevel: 'strict', validationAction: 'warn' }), 'observe');
  assert.equal(detectStage({ validator: { $jsonSchema: {} }, validationLevel: 'strict', validationAction: 'error' }), 'enforce');
  assert.equal(detectStage({ validator: { $jsonSchema: {} }, validationLevel: 'constraint', validationAction: 'error' }), 'constraint');
  assert.equal(detectStage({ validator: { $jsonSchema: {} }, validationLevel: 'moderate' }), 'custom');
});

test('transition to constraint prepares the collection before setting the level', () => {
  const steps = planTransition({}, 'constraint', C).map((s) => s.command);
  assert.equal(steps[0].validationAction, 'error');
  assert.deepEqual(steps[1], { collMod: C, prepareConstraintValidationLevel: true });
  assert.equal(steps[2].validationLevel, 'constraint');
  assert.equal(steps[2].validator, undefined, 'validator must not change in the same collMod');
});

test('leaving constraint downgrades to strict before changing the validator', () => {
  const steps = planTransition({ validator: { $jsonSchema: {} }, validationLevel: 'constraint' }, 'flexible', C);
  assert.equal(steps[0].command.validationLevel, 'strict');
  assert.equal(steps[0].command.validator, undefined);
  assert.ok(steps.some((s) => s.command.prepareConstraintValidationLevel === false && s.optional));
  assert.deepEqual(steps.at(-1).command, stageCommand('flexible', C));
  assert.deepEqual(planTransition({}, 'flexible', C), []);
});

test('every profile is structurally valid and builds envelope documents', () => {
  for (const id of PROFILE_IDS) {
    const p = getProfile(id);
    assert.deepEqual(validateProfile(p), [], id);
    const ent = buildTrackedEntity(p, now);
    assert.equal(ent.plannedMilestones.length, p.milestones.length);
    const portfolio = buildPortfolioEntities(p, now);
    assert.equal(portfolio.length, 3);
    assert.ok(portfolio.every((load) => load.demo.portfolio && load.portfolio.lastTrustedSignal.at instanceof Date));
    const withException = p.portfolio.find((load) => load.exception);
    const work = buildPortfolioException(p, withException, now);
    assert.equal(work.exceptionKey, `${p.id}:${withException.trackedEntityId}:${withException.exception.category}`);
    const evt = buildEvent(p, p.samples.find((s) => !s.duplicateOf), { now });
    assert.equal(evt.idempotencyKey, `${evt.source}:${evt.sourceEventId}`);
    assert.ok(evt.eventTime instanceof Date);
  }
});

test('repair plan fixes correlatable legacy events and refuses unknown ones', () => {
  const p = getProfile('retail');
  for (const spec of p.invalidSamples) {
    const doc = { _id: 'abc', ...buildInvalidEvent(p, spec, now) };
    const plan = planRepair(doc, p, now);
    assert.ok(plan.set, spec.id);
    if (spec.issue === 'missing_tracked_entity_id') assert.equal(plan.set.trackedEntityId, p.entity.trackedEntityId);
    if (spec.issue === 'event_time_is_string') assert.ok(plan.set.eventTime instanceof Date);
    assert.equal(plan.set.idempotencyKey, `${doc.source}:LEGACY-abc`);
  }
  const orphan = { _id: 'x', source: 's', eventTime: now, details: { legacyRef: 'UNKNOWN' } };
  assert.match(planRepair(orphan, p).error, /No reference/);
});

test('config requires an Atlas host and never exposes credentials', () => {
  const uri = 'mongodb+srv://user:s3cret@demo.abcde.mongodb.net/?retryWrites=true';
  assert.equal(targetLabel(uri), 'mongodb+srv://demo.abcde.mongodb.net');
  assert.ok(!redact(`failed for ${uri}`).includes('s3cret'));
  assert.equal(isAtlasHost('mongodb://localhost:27017'), false);
  assert.throws(() => loadConfig({ MONGODB_URI: 'mongodb://localhost:27017' }), /Atlas/);
  assert.throws(() => loadConfig({}), /MONGODB_URI is not set/);
  assert.throws(() => assertDemoDbName('production'), /demo/);
  assert.equal(loadConfig({ MONGODB_URI: uri }).dbName, 'atlas_control_tower_demo');
});

test('API routes match with path parameters', () => {
  const m = matchRoute('POST', '/api/exceptions/EXC-1A2B/action');
  assert.equal(m.params.id, 'EXC-1A2B');
  assert.equal(matchRoute('GET', '/api/exceptions/EXC-1/action'), null);
});
