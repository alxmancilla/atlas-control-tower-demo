import { COLL } from '../db.js';
import { ConflictError } from '../errors.js';
import { invalidate } from './cache.js';

const LOCK_ID = 'maintenance';
// A crashed process cannot hold the lock forever; demo maintenance finishes well inside this window.
const LOCK_TTL_MS = 10 * 60 * 1000;

/**
 * Runs setup, reset, or re-anchor while holding a lock document in demo_state.
 * Acquisition is atomic: the filter only matches a free or expired lock, so a held lock makes the
 * upsert collide on _id (duplicate key) instead of taking over. The UI pauses while the lock is held.
 */
export async function withMaintenance(db, op, fn) {
  const state = db.collection(COLL.state);
  const now = new Date();
  const token = `${op}:${now.getTime()}:${Math.random().toString(36).slice(2, 8)}`;
  try {
    await state.updateOne(
      { _id: LOCK_ID, $or: [{ active: false }, { until: { $lt: now } }] },
      { $set: { active: true, op, token, startedAt: now, until: new Date(now.getTime() + LOCK_TTL_MS) } },
      { upsert: true }
    );
  } catch (err) {
    if (err?.code !== 11000) throw err;
    const held = await state.findOne({ _id: LOCK_ID });
    throw new ConflictError(`Another maintenance operation (${held?.op ?? 'unknown'}) is running. Try again when it finishes.`);
  }
  invalidate(`${db.databaseName}:`);
  try {
    return await fn();
  } finally {
    await state.updateOne({ _id: LOCK_ID, token }, { $set: { active: false, finishedAt: new Date() } }).catch(() => {});
    invalidate(`${db.databaseName}:`);
  }
}

export async function maintenanceState(db, now = new Date()) {
  const doc = await db.collection(COLL.state).findOne({ _id: LOCK_ID }, { projection: { active: 1, op: 1, startedAt: 1, until: 1 } });
  const active = !!doc?.active && doc.until > now;
  return active ? { active, op: doc.op, startedAt: doc.startedAt } : { active: false };
}
