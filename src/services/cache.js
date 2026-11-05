// Short-lived memo for status checks that every request would otherwise repeat
// (listCollections, counts, listSearchIndexes). Keys include the database name.
const entries = new Map();

export async function memo(key, ttlMs, fn) {
  const hit = entries.get(key);
  if (hit && hit.expires > Date.now()) return hit.promise;
  const promise = fn();
  entries.set(key, { promise, expires: Date.now() + ttlMs });
  try {
    return await promise;
  } catch (err) {
    entries.delete(key); // Never cache failures.
    throw err;
  }
}

export function invalidate(prefix = '') {
  for (const key of entries.keys()) if (key.startsWith(prefix)) entries.delete(key);
}
