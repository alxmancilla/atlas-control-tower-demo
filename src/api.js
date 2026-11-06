import { INDEXES } from './db.js';
import { getProfile, PROFILE_IDS, profileSummary, PROFILES } from './profiles/index.js';
import { BadRequestError, StatusError } from './errors.js';
import { withMaintenance, maintenanceState } from './services/maintenance.js';
import { invalidDocumentsFilter } from './domain/schema.js';
import { triggerSample, timeline, reliabilitySummary, validProbeEvent } from './services/events.js';
import { applyExceptionAction, decideRecommendedAction, exceptionQueue, exceptionSummary } from './services/exceptions.js';
import { entityOverview } from './services/overview.js';
import { telemetryView, addReading } from './services/telemetry.js';
import { createSearchIndexes, findSimilar, runSearch, searchStatus, SearchUnavailableError, SEARCH_INDEXES } from './services/search.js';
import { insertInvalidSample, insertValidEvent, removeInvalid, repairInvalid, transitionStage, validationState } from './services/validation.js';
import { cachedIsInitialized, resetDemo, setupDemo } from './services/setup.js';
import { reanchorDemo } from './services/reanchor.js';
import { isStale, recomputeImpact } from './services/impact.js';
import { kpiSnapshot } from './services/kpis.js';
import { portfolioOverview } from './services/portfolio.js';

export class HttpError extends StatusError {}

const num = (v) => (v === undefined || v === '' ? undefined : Number(v));

// Route table: "METHOD /path" -> handler(ctx). ctx = { app, db, profile, query, body, params }.
export const routes = {
  'GET /api/status': async ({ app }) => app.status(),
  'POST /api/connect': async ({ app }) => {
    await app.ensureConnected();
    return app.status();
  },
  'GET /api/profiles': async ({ app }) => ({
    defaultProfile: app.config.defaultProfile,
    profiles: PROFILE_IDS.map((id) => profileSummary(PROFILES[id])),
    indexes: INDEXES.map(({ coll, key, options, reason }) => ({ coll, key, name: options.name, unique: !!options.unique, reason })),
    searchIndexes: SEARCH_INDEXES,
  }),
  'POST /api/setup': async ({ db }) => withMaintenance(db, 'setup', () => setupDemo(db)),
  'POST /api/reset': async ({ db, body }) => {
    if (body.confirm !== db.databaseName) throw new BadRequestError(`Reset not confirmed. Type the database name "${db.databaseName}" to confirm.`);
    return withMaintenance(db, 'reset', () => resetDemo(db, body.confirm));
  },
  'POST /api/reanchor': async ({ db }) => ({ profiles: await withMaintenance(db, 'reanchor', () => reanchorDemo(db)) }),

  'GET /api/overview': async ({ db, profile }) => {
    const [overview, reliability, exceptions] = await Promise.all([
      entityOverview(db, profile),
      reliabilitySummary(db, profile),
      exceptionSummary(db, profile),
    ]);
    if (!overview) throw new HttpError(404, 'Demo data not found for this profile. Initialize the demo database first.');
    // The stored impact is a time-based estimate; refresh it when it is older than IMPACT_MAX_AGE_MS.
    if (isStale(overview.businessImpact)) overview.businessImpact = await recomputeImpact(db, profile);
    return { ...overview, reliability, exceptionSummary: exceptions };
  },
  'GET /api/portfolio': async ({ db, profile }) => portfolioOverview(db, profile),
  'GET /api/kpis': async ({ db, profile, query }) => kpiSnapshot(db, profile, query.fresh ? { maxAgeMs: 0 } : {}),
  'GET /api/timeline': async ({ db, profile }) => ({ events: await timeline(db, profile, invalidDocumentsFilter()) }),
  'POST /api/events/sample': async ({ db, profile, body }) => triggerSample(db, profile, body.sampleId),

  'GET /api/exceptions': async ({ db, profile }) => ({ queue: await exceptionQueue(db, profile), rules: profile.priority }),
  'POST /api/exceptions/:id/action': async ({ db, params, body }) => applyExceptionAction(db, params.id, body),
  'POST /api/exceptions/:id/recommendation': async ({ db, params, body }) => decideRecommendedAction(db, params.id, body),
  'GET /api/exceptions/:id/similar': async ({ db, params }) => findSimilar(db, params.id),

  'GET /api/telemetry': async ({ db, profile, query }) =>
    telemetryView(db, profile, { hours: num(query.hours) ?? 8, min: num(query.min), max: num(query.max) }),
  'POST /api/telemetry/reading': async ({ db, profile, body }) => {
    if (!['normal', 'excursion'].includes(body.kind)) throw new BadRequestError('kind must be "normal" or "excursion"');
    return addReading(db, profile, body.kind);
  },

  'GET /api/search/status': async ({ db }) => searchStatus(db),
  'POST /api/search/indexes': async ({ db }) => ({ results: await createSearchIndexes(db), status: await searchStatus(db) }),
  'GET /api/search': async ({ db, profile, query }) =>
    runSearch(db, profile, { q: query.q, kind: query.kind, source: query.source || undefined, status: query.status || undefined }),

  'GET /api/validation': async ({ app, db }) => validationState(db, app.info.gate),
  'POST /api/validation/stage': async ({ app, db, body }) => transitionStage(db, body.stage, app.info.gate),
  'POST /api/validation/invalid': async ({ db, profile, body }) => insertInvalidSample(db, profile, body.sampleId),
  'POST /api/validation/valid': async ({ db, profile }) => insertValidEvent(db, profile, validProbeEvent(profile)),
  'POST /api/validation/repair': async ({ db, body }) => repairInvalid(db, body.id),
  'POST /api/validation/remove': async ({ db, body }) => removeInvalid(db, body.id),
};

const NEEDS_NO_DB = new Set(['GET /api/status', 'POST /api/connect', 'GET /api/profiles']);
const NEEDS_NO_INIT = new Set([...NEEDS_NO_DB, 'POST /api/setup', 'POST /api/reset', 'GET /api/search/status']);
// Maintenance routes take the lock themselves; everything else waits so views never show half-shifted data.
const RUNS_DURING_MAINTENANCE = new Set([...NEEDS_NO_DB, 'POST /api/setup', 'POST /api/reset', 'POST /api/reanchor', 'GET /api/search/status']);

export function matchRoute(method, pathname) {
  for (const [key, handler] of Object.entries(routes)) {
    const [m, pattern] = key.split(' ');
    if (m !== method) continue;
    const names = [];
    const re = new RegExp(`^${pattern.replace(/:(\w+)/g, (_, n) => (names.push(n), '([^/]+)'))}$`);
    const hit = re.exec(pathname);
    if (hit) return { key, handler, params: Object.fromEntries(names.map((n, i) => [n, decodeURIComponent(hit[i + 1])])) };
  }
  return null;
}

export async function handleApi(app, method, url, body) {
  const route = matchRoute(method, url.pathname);
  if (!route) throw new HttpError(404, `No route ${method} ${url.pathname}`);
  if (body === null || typeof body !== 'object' || Array.isArray(body)) throw new BadRequestError('Request body must be a JSON object');
  const query = Object.fromEntries(url.searchParams);
  let profile = null;
  try {
    profile = getProfile(query.profile || body.profile || app.config.defaultProfile);
  } catch (err) {
    throw new HttpError(400, err.message);
  }
  let db = null;
  if (!NEEDS_NO_DB.has(route.key)) {
    db = await app.ensureConnected();
    if (!RUNS_DURING_MAINTENANCE.has(route.key)) {
      const m = await maintenanceState(db);
      if (m.active) throw new HttpError(503, `Maintenance in progress (${m.op}). Views resume automatically when it finishes.`);
    }
    if (!NEEDS_NO_INIT.has(route.key)) {
      const init = await cachedIsInitialized(db);
      if (!init.initialized) throw new HttpError(409, 'The demo database is not initialized. Click "Initialize demo data" or run npm run setup.');
    }
  }
  return route.handler({ app, db, profile, query, body, params: route.params });
}

export function statusFor(err) {
  if (err instanceof StatusError) return err.status;
  if (err instanceof SearchUnavailableError) return 503;
  if (err?.code === 13 || err?.codeName === 'Unauthorized') return 403;
  return 500;
}
