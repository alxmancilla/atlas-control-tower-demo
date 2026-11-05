import { test } from 'node:test';
import assert from 'node:assert/strict';
import { memo, invalidate } from '../../src/services/cache.js';

test('memo shares one in-flight call and reuses the result until the TTL expires', async () => {
  let calls = 0;
  const fn = async () => ++calls;
  const [a, b] = await Promise.all([memo('t1:x', 50, fn), memo('t1:x', 50, fn)]);
  assert.deepEqual([a, b, calls], [1, 1, 1]);
  assert.equal(await memo('t1:x', 50, fn), 1);
  await new Promise((r) => setTimeout(r, 60));
  assert.equal(await memo('t1:x', 50, fn), 2);
});

test('memo never caches a failure', async () => {
  let calls = 0;
  await assert.rejects(memo('t2:x', 10000, async () => { calls++; throw new Error('boom'); }), /boom/);
  assert.equal(await memo('t2:x', 10000, async () => ++calls), 2);
});

test('invalidate clears only keys with the given prefix', async () => {
  let n = 0;
  const fn = async () => ++n;
  await memo('dbA:status', 10000, fn);
  await memo('dbB:status', 10000, fn);
  invalidate('dbA:');
  assert.equal(await memo('dbA:status', 10000, fn), 3);
  assert.equal(await memo('dbB:status', 10000, fn), 2);
});
