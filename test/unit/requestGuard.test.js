import { test } from 'node:test';
import assert from 'node:assert/strict';
import { assertSafeBinding, guardRequest, GuardError } from '../../src/requestGuard.js';
import { statusFor } from '../../src/api.js';
import { checkExceptionAction } from '../../src/domain/rules.js';
import { planTransition } from '../../src/domain/schema.js';
import { loadConfig } from '../../src/config.js';

const local = { host: '127.0.0.1', port: 3000, adminToken: null };
const req = (method, headers = {}) => ({ method, headers: { host: 'localhost:3000', ...headers } });
const status = (fn) => {
  try {
    fn();
    return 'ok';
  } catch (err) {
    assert.ok(err instanceof GuardError, err.message);
    return err.status;
  }
};

test('same-origin JSON requests from this machine pass', () => {
  assert.equal(status(() => guardRequest(req('GET'), local)), 'ok');
  assert.equal(status(() => guardRequest(req('POST', { 'content-type': 'application/json; charset=utf-8', origin: 'http://localhost:3000' }), local)), 'ok');
  assert.equal(status(() => guardRequest(req('POST', { 'content-type': 'application/json', host: '127.0.0.1:3000' }), local)), 'ok');
});

test('cross-site and rebinding requests are rejected', () => {
  // A "simple" cross-site form/fetch post: no preflight, so the server itself must refuse it.
  assert.equal(status(() => guardRequest(req('POST', { 'content-type': 'text/plain', origin: 'http://localhost:3000' }), local)), 415);
  assert.equal(status(() => guardRequest(req('POST', { 'content-type': 'application/json', origin: 'https://evil.example' }), local)), 403);
  assert.equal(status(() => guardRequest(req('GET', { host: 'evil.example:3000' }), local)), 421);
  assert.equal(status(() => guardRequest(req('DELETE'), local)), 405);
});

test('admin token is required for writes when configured', () => {
  const cfg = { ...local, adminToken: 'demo-secret' };
  const json = { 'content-type': 'application/json' };
  assert.equal(status(() => guardRequest(req('POST', json), cfg)), 401);
  assert.equal(status(() => guardRequest(req('POST', { ...json, 'x-admin-token': 'wrong' }), cfg)), 401);
  assert.equal(status(() => guardRequest(req('POST', { ...json, 'x-admin-token': 'demo-secret' }), cfg)), 'ok');
  assert.equal(status(() => guardRequest(req('GET'), cfg)), 'ok');
});

test('binding beyond loopback requires a token; wildcard binds accept any Host', () => {
  assert.throws(() => assertSafeBinding({ host: '0.0.0.0', adminToken: null }), /ADMIN_TOKEN/);
  assert.doesNotThrow(() => assertSafeBinding({ host: '0.0.0.0', adminToken: 't' }));
  assert.doesNotThrow(() => assertSafeBinding({ host: '127.0.0.1', adminToken: null }));
  const wide = { host: '0.0.0.0', port: 3000, adminToken: 't' };
  assert.equal(status(() => guardRequest(req('GET', { host: '192.168.1.20:3000' }), wide)), 'ok');
  const uri = 'mongodb+srv://u:p@demo.abcde.mongodb.net/';
  assert.equal(loadConfig({ MONGODB_URI: uri }).host, '127.0.0.1');
});

test('caller mistakes map to 4xx, not 500', () => {
  const statusOf = (fn) => {
    try {
      fn();
    } catch (err) {
      return statusFor(err);
    }
    return null;
  };
  assert.equal(statusOf(() => checkExceptionAction('open', 'delete')), 400);
  assert.equal(statusOf(() => checkExceptionAction('open', 'resolve', { note: '' })), 400);
  assert.equal(statusOf(() => checkExceptionAction('resolved', 'acknowledge')), 409);
  assert.equal(statusOf(() => planTransition({}, 'bogus', 'c')), 400);
  assert.equal(statusFor(new Error('driver failure')), 500);
});
