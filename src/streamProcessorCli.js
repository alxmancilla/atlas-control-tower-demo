import { streamProcessorConfigFromEnv } from './services/streamProcessing.js';

try {
  console.log(JSON.stringify(streamProcessorConfigFromEnv(), null, 2));
} catch (error) {
  console.error(`Stream processor configuration error: ${error.message}`);
  process.exitCode = 1;
}