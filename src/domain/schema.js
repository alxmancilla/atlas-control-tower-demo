import { PROFILE_IDS } from '../profiles/index.js';
import { BadRequestError } from '../errors.js';

// Proposed operational_events schema. Uses only $jsonSchema keywords MongoDB documents:
// bsonType, required, properties, enum, minLength, description.
export const operationalEventSchema = {
  bsonType: 'object',
  title: 'operational_events envelope',
  required: ['trackedEntityId', 'profile', 'eventType', 'eventTime', 'receivedAt', 'source', 'sourceEventId', 'idempotencyKey'],
  properties: {
    trackedEntityId: { bsonType: 'string', minLength: 1, description: 'required: correlates the event to a tracked entity' },
    profile: { enum: PROFILE_IDS, description: 'required: demo domain profile' },
    eventType: { bsonType: 'string', minLength: 1 },
    eventTime: { bsonType: 'date', description: 'required: when the event happened, as a BSON date' },
    receivedAt: { bsonType: 'date', description: 'required: when the platform received the event, as a BSON date' },
    source: { bsonType: 'string', minLength: 1 },
    sourceEventId: { bsonType: 'string', minLength: 1 },
    idempotencyKey: { bsonType: 'string', minLength: 1, description: 'required: source + sourceEventId, unique' },
    milestoneId: { bsonType: 'string' },
    details: { bsonType: 'object', description: 'source-specific fields stay flexible' },
  },
};

export const invalidDocumentsFilter = () => ({ $nor: [{ $jsonSchema: operationalEventSchema }] });

export const STAGES = ['flexible', 'observe', 'enforce', 'constraint'];

export function stageCommand(stage, coll) {
  switch (stage) {
    case 'flexible':
      return { collMod: coll, validator: {}, validationLevel: 'off', validationAction: 'error' };
    case 'observe':
      return { collMod: coll, validator: { $jsonSchema: operationalEventSchema }, validationLevel: 'strict', validationAction: 'warn' };
    case 'enforce':
      return { collMod: coll, validator: { $jsonSchema: operationalEventSchema }, validationLevel: 'strict', validationAction: 'error' };
    default:
      throw new Error(`No single command for stage "${stage}"`);
  }
}

// Derives the stage from the collection options MongoDB reports (listCollections).
export function detectStage(options = {}) {
  const hasValidator = !!options.validator && Object.keys(options.validator).length > 0;
  const level = options.validationLevel ?? 'strict';
  const action = options.validationAction ?? 'error';
  if (!hasValidator || level === 'off') return 'flexible';
  if (level === 'constraint') return 'constraint';
  if (level === 'strict' && action === 'warn') return 'observe';
  if (level === 'strict' && (action === 'error' || action === 'errorAndLog')) return 'enforce';
  return 'custom';
}

/**
 * Ordered collMod steps from the current options to the target stage.
 * Leaving constraint requires downgrading to strict before the validator can change.
 */
export function planTransition(currentOptions, target, coll) {
  if (!STAGES.includes(target)) throw new BadRequestError(`Unknown stage "${target}"`);
  const current = detectStage(currentOptions);
  if (current === target) return [];
  const steps = [];
  if (current === 'constraint') {
    steps.push({ label: 'Leave constraint: downgrade to strict', command: { collMod: coll, validationLevel: 'strict', validationAction: 'error' } });
  }
  if (target === 'constraint') {
    if (current !== 'enforce') steps.push({ label: 'Apply strict validator with action error', command: stageCommand('enforce', coll) });
    steps.push({ label: 'Prepare collection for constraint', command: { collMod: coll, prepareConstraintValidationLevel: true } });
    steps.push({
      label: 'Set validationLevel constraint (MongoDB scans existing documents)',
      command: { collMod: coll, validationLevel: 'constraint', validationAction: 'error' },
    });
    return steps;
  }
  if (current === 'constraint' || current === 'enforce') {
    steps.push({
      label: 'Clear constraint preparation flag if set',
      command: { collMod: coll, prepareConstraintValidationLevel: false },
      optional: true,
    });
  }
  steps.push({ label: `Apply ${target} stage`, command: stageCommand(target, coll) });
  return steps;
}
