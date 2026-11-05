import { timingSafeEqual } from 'node:crypto';
import { StatusError } from './errors.js';

export class GuardError extends StatusError {}

const LOOPBACK = ['localhost', '127.0.0.1', '[::1]'];
const WILDCARD = ['0.0.0.0', '::'];

export const isLoopbackBind = (host) => ['127.0.0.1', '::1', 'localhost'].includes(host);

// Host names the server answers to; null means any (wildcard bind, which requires ADMIN_TOKEN).
// Checking Host blocks DNS-rebinding pages from reading the API.
export function allowedHosts({ host, port }) {
  if (WILDCARD.includes(host)) return null;
  const names = new Set(LOOPBACK);
  if (host && !isLoopbackBind(host)) names.add(host.includes(':') ? `[${host}]` : host);
  return new Set([...names].map((n) => `${n}:${port}`));
}

// Exposing the write API beyond this machine without a token is refused at startup.
export function assertSafeBinding({ host, adminToken }) {
  if (!isLoopbackBind(host) && !adminToken) {
    throw new Error(`HOST=${host} exposes the demo beyond this machine. Set ADMIN_TOKEN to protect changes, or remove HOST to bind to 127.0.0.1.`);
  }
}

const tokenMatches = (given, expected) => {
  const a = Buffer.from(String(given ?? ''));
  const b = Buffer.from(expected);
  return a.length === b.length && timingSafeEqual(a, b);
};

/**
 * Rejects requests that did not come from this app's own page:
 * - Host must be one this server serves (DNS rebinding).
 * - POST must be application/json, which forces a CORS preflight that this server never approves,
 *   and any Origin header must match the Host (cross-site requests).
 * - When ADMIN_TOKEN is set, POST must carry it in X-Admin-Token.
 */
export function guardRequest(req, config) {
  const hostHeader = String(req.headers.host ?? '').toLowerCase();
  const hosts = allowedHosts(config);
  if (hosts && !hosts.has(hostHeader)) throw new GuardError(421, `Host "${hostHeader}" is not served by this demo`);
  if (req.method === 'GET' || req.method === 'HEAD') return;
  if (req.method !== 'POST') throw new GuardError(405, `Method ${req.method} is not allowed`);
  const origin = req.headers.origin;
  if (origin && origin.toLowerCase() !== `http://${hostHeader}`) throw new GuardError(403, 'Cross-origin requests are not allowed');
  const type = String(req.headers['content-type'] ?? '').split(';')[0].trim().toLowerCase();
  if (type !== 'application/json') throw new GuardError(415, 'POST requests must use Content-Type: application/json');
  if (config.adminToken && !tokenMatches(req.headers['x-admin-token'], config.adminToken)) {
    throw new GuardError(401, 'Admin token required for changes. Enter the ADMIN_TOKEN value when prompted.');
  }
}

export const SECURITY_HEADERS = {
  'Content-Security-Policy': "default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self' data:; connect-src 'self'; object-src 'none'; base-uri 'none'; frame-ancestors 'none'",
  'X-Content-Type-Options': 'nosniff',
  'Referrer-Policy': 'no-referrer',
};
