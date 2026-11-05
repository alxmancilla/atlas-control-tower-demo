import http from 'node:http';
import { readFile } from 'node:fs/promises';
import { extname, join, normalize } from 'node:path';
import { fileURLToPath } from 'node:url';
import { loadConfig, redact, ConfigError } from './config.js';
import { connect, serverInfo } from './db.js';
import { ChangeFeed } from './services/changeFeed.js';
import { cachedIsInitialized } from './services/setup.js';
import { cachedSearchStatus } from './services/search.js';
import { demoClockAge } from './services/reanchor.js';
import { maintenanceState } from './services/maintenance.js';
import { handleApi, HttpError, statusFor } from './api.js';
import { assertSafeBinding, guardRequest, isLoopbackBind, SECURITY_HEADERS } from './requestGuard.js';

const PUBLIC_DIR = fileURLToPath(new URL('../public/', import.meta.url));
const TYPES = { '.html': 'text/html; charset=utf-8', '.js': 'text/javascript; charset=utf-8', '.css': 'text/css; charset=utf-8' };

export function createApp(config) {
  const app = {
    config,
    client: null,
    db: null,
    info: null,
    feed: null,
    connectError: null,
    connecting: null,

    async ensureConnected() {
      if (app.db) return app.db;
      if (!app.connecting) {
        app.connecting = (async () => {
          try {
            const { client, db } = await connect(config);
            app.client = client;
            app.db = db;
            app.info = await serverInfo(db);
            app.feed = new ChangeFeed(db).start();
            app.connectError = null;
          } catch (err) {
            app.connectError = redact(err.message);
          } finally {
            app.connecting = null;
          }
        })();
      }
      await app.connecting;
      if (!app.db) throw new HttpError(503, app.connectError);
      return app.db;
    },

    async status() {
      const base = { database: config.dbName, target: config.target, defaultProfile: config.defaultProfile, adminTokenRequired: !!config.adminToken };
      if (!app.db) return { ...base, connected: false, error: app.connectError };
      try {
        const maintenance = await maintenanceState(app.db);
        if (maintenance.active) return { ...base, connected: true, server: app.info, maintenance, changeStream: app.feed?.state };
        const [init, search, clockAgeMin] = await Promise.all([cachedIsInitialized(app.db), cachedSearchStatus(app.db), demoClockAge(app.db)]);
        return { ...base, connected: true, server: app.info, maintenance, init, search, clockAgeMin, changeStream: app.feed?.state };
      } catch (err) {
        return { ...base, connected: true, server: app.info, error: `Status check failed: ${redact(err.message)}`, changeStream: app.feed?.state };
      }
    },

    async close() {
      await app.feed?.stop();
      await app.client?.close();
    },
  };
  return app;
}

async function readBody(req, limit = 64 * 1024) {
  let size = 0;
  const chunks = [];
  for await (const chunk of req) {
    size += chunk.length;
    if (size > limit) throw new HttpError(413, 'Request body too large');
    chunks.push(chunk);
  }
  if (!chunks.length) return {};
  try {
    return JSON.parse(Buffer.concat(chunks).toString('utf8'));
  } catch {
    throw new HttpError(400, 'Request body must be JSON');
  }
}

function serveStream(app, req, res) {
  res.writeHead(200, { 'Content-Type': 'text/event-stream', 'Cache-Control': 'no-cache', Connection: 'keep-alive' });
  const send = (event, data) => res.write(`event: ${event}\ndata: ${JSON.stringify(data)}\n\n`);
  res.write('retry: 3000\n\n');
  const feed = app.feed;
  if (!feed) {
    send('stream-status', { state: 'unavailable', error: app.connectError || 'Not connected to Atlas' });
    return res.end();
  }
  send('stream-status', feed.state);
  const onChange = (c) => send('change', c);
  const onStatus = (s) => send('stream-status', s);
  const onResync = (r) => send('resync', r);
  feed.on('change', onChange).on('status', onStatus).on('resync', onResync);
  const heartbeat = setInterval(() => res.write(': heartbeat\n\n'), 20000);
  req.on('close', () => {
    clearInterval(heartbeat);
    feed.off('change', onChange).off('status', onStatus).off('resync', onResync);
  });
}

async function serveStatic(pathname, res) {
  const rel = normalize(pathname === '/' ? 'index.html' : pathname.slice(1));
  if (rel.startsWith('..')) throw new HttpError(404, 'Not found');
  const body = await readFile(join(PUBLIC_DIR, rel)).catch(() => null);
  if (!body) throw new HttpError(404, 'Not found');
  res.writeHead(200, { 'Content-Type': TYPES[extname(rel)] ?? 'application/octet-stream', 'Cache-Control': 'no-cache', ...SECURITY_HEADERS });
  res.end(body);
}

export function createServer(app) {
  return http.createServer(async (req, res) => {
    const url = new URL(req.url, 'http://localhost');
    try {
      guardRequest(req, app.config);
      if (url.pathname === '/api/stream') {
        await app.ensureConnected().catch(() => {});
        return serveStream(app, req, res);
      }
      if (!url.pathname.startsWith('/api/')) return await serveStatic(url.pathname, res);
      const body = req.method === 'POST' ? await readBody(req) : {};
      const result = await handleApi(app, req.method, url, body);
      res.writeHead(200, { 'Content-Type': 'application/json', 'Cache-Control': 'no-store', 'X-Content-Type-Options': 'nosniff' });
      res.end(JSON.stringify(result));
    } catch (err) {
      const status = statusFor(err);
      if (status >= 500 && !(err instanceof HttpError)) console.error(`[${req.method} ${url.pathname}]`, redact(err.stack || err.message));
      if (!res.headersSent) res.writeHead(status, { 'Content-Type': 'application/json' });
      res.end(JSON.stringify({ error: redact(err.message), code: err.code, codeName: err.codeName }));
    }
  });
}

if (process.argv[1] === fileURLToPath(import.meta.url)) {
  let config;
  try {
    config = loadConfig();
  } catch (err) {
    console.error(err instanceof ConfigError ? `Configuration error: ${err.message}` : redact(err.message));
    process.exit(1);
  }
  try {
    assertSafeBinding(config);
  } catch (err) {
    console.error(`Configuration error: ${err.message}`);
    process.exit(1);
  }
  const app = createApp(config);
  const server = createServer(app);
  server.listen(config.port, config.host, () => {
    const where = isLoopbackBind(config.host) ? 'this machine only' : `HOST ${config.host}, admin token required for changes`;
    console.log(`Control Tower: http://localhost:${config.port}  (${where}; Atlas target ${config.target}, database ${config.dbName})`);
  });
  app.ensureConnected().catch((err) => console.error(`Atlas connection: ${err.message}`));
  const shutdown = async () => {
    server.close();
    await app.close();
    process.exit(0);
  };
  process.on('SIGINT', shutdown).on('SIGTERM', shutdown);
}
