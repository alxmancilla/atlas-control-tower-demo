import { ObjectId } from 'mongodb';
import { COLL, serverError } from '../db.js';
import { getProfile } from '../profiles/index.js';
import { buildInvalidEvent, planRepair } from '../domain/documents.js';
import { detectStage, invalidDocumentsFilter, operationalEventSchema, planTransition } from '../domain/schema.js';
import { ingestEvent } from './events.js';

export async function eventCollectionOptions(db) {
  const [info] = await db.listCollections({ name: COLL.events }).toArray();
  return info?.options ?? {};
}

// Shown in the UI instead of the full schema to keep command output readable.
const describeCommand = (cmd) => (cmd.validator && Object.keys(cmd.validator).length ? { ...cmd, validator: { $jsonSchema: '<operational_events schema>' } } : cmd);

export async function findInvalidEvents(db, limit = 50) {
  const coll = db.collection(COLL.events);
  const [docs, count] = await Promise.all([
    coll.find(invalidDocumentsFilter()).limit(limit).toArray(),
    coll.countDocuments(invalidDocumentsFilter()),
  ]);
  return { count, docs: docs.map((d) => ({ ...d, _id: String(d._id) })) };
}

export async function validationState(db, gate) {
  const options = await eventCollectionOptions(db);
  const persisted = await db.collection(COLL.state).findOne({ _id: 'validation' });
  const invalid = await findInvalidEvents(db);
  return {
    stage: detectStage(options),
    persistedStage: persisted?.stage ?? null,
    history: persisted?.history ?? [],
    collectionOptions: {
      validationLevel: options.validationLevel ?? null,
      validationAction: options.validationAction ?? null,
      hasValidator: !!options.validator && Object.keys(options.validator).length > 0,
    },
    constraintGate: gate,
    schema: operationalEventSchema,
    invalidQuery: { $nor: [{ $jsonSchema: '<operational_events schema>' }] },
    invalid,
  };
}

export async function transitionStage(db, target, gate) {
  if (target === 'constraint' && !gate.supported) {
    return { ok: false, target, steps: [], error: { message: gate.reason }, stage: detectStage(await eventCollectionOptions(db)) };
  }
  const steps = planTransition(await eventCollectionOptions(db), target, COLL.events);
  const results = [];
  let failed = null;
  for (const step of steps) {
    try {
      const res = await db.command(step.command);
      results.push({ label: step.label, command: describeCommand(step.command), ok: true, response: { ok: res.ok } });
    } catch (err) {
      const r = { label: step.label, command: describeCommand(step.command), ok: false, optional: !!step.optional, error: serverError(err) };
      results.push(r);
      if (!step.optional) {
        failed = r;
        break;
      }
    }
  }
  const stage = detectStage(await eventCollectionOptions(db));
  const ok = !failed && stage === target;
  const now = new Date();
  await db.collection(COLL.state).updateOne(
    { _id: 'validation' },
    { $set: { stage, updatedAt: now }, $push: { history: { $each: [{ at: now, target, ok, stage }], $slice: -20 } } },
    { upsert: true }
  );
  const out = { ok, target, stage, steps: results, error: failed?.error ?? null };
  if (failed && target === 'constraint') out.violatingDocuments = (await findInvalidEvents(db)).docs;
  return out;
}

export async function insertInvalidSample(db, profile, sampleId, now = new Date()) {
  const spec = profile.invalidSamples.find((s) => s.id === sampleId);
  if (!spec) throw new Error(`Unknown invalid sample "${sampleId}"`);
  const doc = buildInvalidEvent(profile, spec, now);
  const stage = detectStage(await eventCollectionOptions(db));
  try {
    const res = await db.collection(COLL.events).insertOne(doc);
    return {
      ok: true,
      stage,
      writeResult: { acknowledged: res.acknowledged, insertedId: String(res.insertedId) },
      note:
        stage === 'observe'
          ? 'MongoDB accepted the write. With validationAction "warn" the server records a warning in its log; the driver does not return it. The invalid-documents query below surfaces the record.'
          : 'MongoDB accepted the write because no validator is enforced at this stage.',
    };
  } catch (err) {
    return { ok: false, stage, error: serverError(err) };
  }
}

export async function insertValidEvent(db, profile, evt) {
  try {
    const res = await ingestEvent(db, profile, evt);
    return { ok: true, outcome: res.outcome, writeResult: { acknowledged: true, insertedId: String(res.event?._id) } };
  } catch (err) {
    return { ok: false, error: serverError(err) };
  }
}

const toObjectId = (id) => {
  if (!ObjectId.isValid(id)) throw new Error('Invalid document id');
  return new ObjectId(id);
};

export async function repairInvalid(db, id) {
  const coll = db.collection(COLL.events);
  const doc = await coll.findOne({ _id: toObjectId(id), 'demo.invalidSample': true });
  if (!doc) throw new Error('Only seeded or sample invalid demo records can be repaired here');
  const plan = planRepair(doc, getProfile(doc.profile));
  if (plan.error) return { ok: false, error: { message: plan.error } };
  try {
    const res = await coll.updateOne({ _id: doc._id }, { $set: plan.set });
    return { ok: true, modified: res.modifiedCount, applied: Object.keys(plan.set) };
  } catch (err) {
    return { ok: false, error: serverError(err) };
  }
}

export async function removeInvalid(db, id) {
  const res = await db.collection(COLL.events).deleteOne({ _id: toObjectId(id), 'demo.invalidSample': true });
  if (!res.deletedCount) throw new Error('Only seeded or sample invalid demo records can be removed here');
  return { ok: true, deleted: res.deletedCount };
}
