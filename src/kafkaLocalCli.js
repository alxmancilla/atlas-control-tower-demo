import { spawnSync } from 'node:child_process';
import { LOCAL_KAFKA, kafkaTopicFromEnv, telemetryMessages } from './services/kafkaTelemetry.js';

const [command] = process.argv.slice(2);
const topic = kafkaTopicFromEnv();
const compose = (...args) => ['compose', '-f', LOCAL_KAFKA.composeFile, ...args];

function run(args, options = {}) {
  const result = spawnSync('docker', args, { stdio: ['pipe', 'inherit', 'inherit'], ...options });
  if (result.error) throw new Error(`Could not run Docker: ${result.error.message}`);
  if (result.status !== 0) process.exitCode = result.status || 1;
  return result.status === 0;
}

function topicArgs() {
  return compose('exec', '-T', 'kafka', '/opt/kafka/bin/kafka-topics.sh', '--bootstrap-server', 'localhost:9092', '--create', '--if-not-exists', '--topic', topic, '--partitions', '1', '--replication-factor', '1');
}

try {
  switch (command) {
    case 'up':
      if (run(compose('up', '-d', '--wait', 'kafka'))) run(topicArgs());
      break;
    case 'down':
      run(compose('down'));
      break;
    case 'logs':
      run(compose('logs', '--tail', '100', 'kafka'));
      break;
    case 'publish':
      run(compose('exec', '-T', 'kafka', '/opt/kafka/bin/kafka-console-producer.sh', '--bootstrap-server', 'localhost:9092', '--topic', topic), { input: `${telemetryMessages().map(JSON.stringify).join('\n')}\n` });
      break;
    case 'verify':
      run(compose('exec', '-T', 'kafka', '/opt/kafka/bin/kafka-console-consumer.sh', '--bootstrap-server', 'localhost:9092', '--topic', topic, '--from-beginning', '--max-messages', '6', '--timeout-ms', '10000'));
      break;
    default:
      console.error('Usage: node src/kafkaLocalCli.js <up|down|logs|publish|verify>');
      process.exitCode = 1;
  }
} catch (error) {
  console.error(`Local Kafka error: ${error.message}`);
  process.exitCode = 1;
}