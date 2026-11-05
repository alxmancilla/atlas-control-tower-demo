import { loadConfig, redact } from './config.js';
import { connect, serverInfo } from './db.js';
import { setupDemo, resetDemo, isInitialized } from './services/setup.js';
import { createSearchIndexes, searchStatus } from './services/search.js';
import { validationState } from './services/validation.js';
import { reanchorDemo } from './services/reanchor.js';

const [command, ...args] = process.argv.slice(2);
const print = (obj) => console.log(JSON.stringify(obj, null, 2));

async function main() {
  const config = loadConfig();
  console.log(`Target: ${config.target}  Database: ${config.dbName}`);
  const { client, db } = await connect(config);
  try {
    const info = await serverInfo(db);
    console.log(`MongoDB ${info.version}${info.fcv ? ` (FCV ${info.fcv})` : ''}. ${info.gate.reason}`);
    switch (command) {
      case 'setup':
        print(await setupDemo(db));
        break;
      case 'search-create':
        print(await createSearchIndexes(db));
        print(await searchStatus(db));
        break;
      case 'search-status':
        print(await searchStatus(db));
        break;
      case 'check': {
        const init = await isInitialized(db);
        const validation = init.missing.length ? null : await validationState(db, info.gate);
        print({ init, search: await searchStatus(db), validationStage: validation?.stage, invalidEvents: validation?.invalid.count });
        break;
      }
      case 'reanchor':
        print(await reanchorDemo(db));
        break;
      case 'reset': {
        const i = args.indexOf('--confirm');
        const confirmation = i >= 0 ? args[i + 1] : undefined;
        print(await resetDemo(db, confirmation));
        break;
      }
      default:
        console.log('Usage: node src/cli.js <setup|search-create|search-status|check|reanchor|reset --confirm <dbName>>');
        process.exitCode = 1;
    }
  } finally {
    await client.close();
  }
}

main().catch((err) => {
  console.error(`Error: ${redact(err.message)}`);
  process.exitCode = 1;
});
