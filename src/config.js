import { PROFILE_IDS } from './profiles/index.js';

export class ConfigError extends Error {}

// Removes credentials from any string that might embed a connection string.
export function redact(text) {
  return String(text ?? '').replace(/(mongodb(?:\+srv)?:\/\/)[^@/\s]+@/gi, '$1<redacted>@');
}

// Returns only host names, never user, password, or query options.
export function targetLabel(uri) {
  const match = /^mongodb(\+srv)?:\/\/(?:[^@/]+@)?([^/?]+)/i.exec(uri || '');
  if (!match) return 'unparseable connection string';
  const hosts = match[2].split(',').map((h) => h.replace(/:\d+$/, ''));
  return `${match[1] ? 'mongodb+srv' : 'mongodb'}://${hosts.join(',')}`;
}

export function isAtlasHost(uri) {
  const match = /^mongodb(\+srv)?:\/\/(?:[^@/]+@)?([^/?]+)/i.exec(uri || '');
  if (!match) return false;
  return match[2].split(',').every((h) => /\.mongodb\.net(:\d+)?$/i.test(h));
}

export function assertDemoDbName(name) {
  if (!/^[A-Za-z0-9_-]+$/.test(name) || !/demo/i.test(name)) {
    throw new ConfigError(`Database name "${name}" is not allowed. Use a dedicated name that contains "demo".`);
  }
  return name;
}

export function loadConfig(env = process.env) {
  const uri = env.MONGODB_URI;
  if (!uri) {
    throw new ConfigError('MONGODB_URI is not set. Copy .env.example to .env and add your Atlas connection string.');
  }
  if (!isAtlasHost(uri)) {
    throw new ConfigError(
      `MONGODB_URI must point to a MongoDB Atlas cluster (*.mongodb.net). Got target ${targetLabel(uri)}. ` +
        'This demo does not substitute a local MongoDB deployment.'
    );
  }
  const defaultProfile = env.DEMO_PROFILE || 'retail';
  if (!PROFILE_IDS.includes(defaultProfile)) {
    throw new ConfigError(`DEMO_PROFILE must be one of ${PROFILE_IDS.join(', ')}. Got "${defaultProfile}".`);
  }
  return {
    uri,
    dbName: assertDemoDbName(env.DEMO_DB_NAME || 'atlas_control_tower_demo'),
    defaultProfile,
    port: Number(env.PORT || 3000),
    host: env.HOST || '127.0.0.1',
    adminToken: env.ADMIN_TOKEN || null,
    target: targetLabel(uri),
  };
}
