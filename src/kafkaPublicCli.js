import { existsSync } from 'node:fs';
import { resolve } from 'node:path';
import { spawnSync } from 'node:child_process';
import { PUBLIC_KAFKA, kafkaTopicFromEnv, publicKafkaIpFromEnv, telemetryMessages } from './services/kafkaTelemetry.js';

const [command] = process.argv.slice(2);
const topic = kafkaTopicFromEnv();
const publicIp = publicKafkaIpFromEnv();
const secretsDirectory = resolve(process.env.KAFKA_PUBLIC_SECRETS_DIR || PUBLIC_KAFKA.secretsDirectory);
const compose = (...args) => ['compose', '-f', PUBLIC_KAFKA.composeFile, ...args];
const dockerEnvironment = { ...process.env, KAFKA_PUBLIC_IP: publicIp, KAFKA_PUBLIC_SECRETS_DIR: secretsDirectory };

function run(args, options = {}) {
  const result = spawnSync('docker', args, { env: dockerEnvironment, stdio: ['pipe', 'inherit', 'inherit'], ...options });
  if (result.error) throw new Error(`Could not run Docker: ${result.error.message}`);
  if (result.status !== 0) process.exitCode = result.status || 1;
  return result.status === 0;
}

function requirePreparedMaterial() {
  const required = ['broker.crt', 'broker_jaas.conf', 'admin.properties', 'producer.properties'];
  const missing = required.filter((name) => !existsSync(resolve(secretsDirectory, name)));
  if (missing.length) throw new Error(`Run npm run kafka:public:prepare first; missing ${missing.join(', ')} in ${secretsDirectory}`);
}

function admin(commandName, ...args) {
  return compose('exec', '-T', 'kafka', `/opt/kafka/bin/${commandName}`, '--bootstrap-server', 'kafka:9092', '--command-config', '/etc/kafka/secrets/admin.properties', ...args);
}

function createTopic() {
  return admin('kafka-topics.sh', '--create', '--if-not-exists', '--topic', topic, '--partitions', '1', '--replication-factor', '1');
}

function addAcls() {
  const producer = admin('kafka-acls.sh', '--add', '--allow-principal', 'User:producer', '--operation', 'Write', '--operation', 'Describe', '--topic', topic);
  const aspTopic = admin('kafka-acls.sh', '--add', '--allow-principal', 'User:asp', '--operation', 'Read', '--operation', 'Describe', '--topic', topic);
  const aspGroup = admin('kafka-acls.sh', '--add', '--allow-principal', 'User:asp', '--operation', 'Read', '--group', PUBLIC_KAFKA.aspConsumerGroup);
  return run(producer) && run(aspTopic) && run(aspGroup);
}

try {
  switch (command) {
    case 'up':
      requirePreparedMaterial();
      if (run(compose('up', '-d', '--wait', 'kafka')) && run(createTopic())) addAcls();
      break;
    case 'down':
      run(compose('down'));
      break;
    case 'logs':
      run(compose('logs', '--tail', '100', 'kafka'));
      break;
    case 'publish':
      requirePreparedMaterial();
      run(compose('exec', '-T', 'kafka', '/opt/kafka/bin/kafka-console-producer.sh', '--bootstrap-server', 'kafka:9092', '--producer.config', '/etc/kafka/secrets/producer.properties', '--topic', topic), { input: `${telemetryMessages().map(JSON.stringify).join('\n')}\n` });
      break;
    case 'verify':
      requirePreparedMaterial();
      run(compose('exec', '-T', 'kafka', '/opt/kafka/bin/kafka-console-consumer.sh', '--bootstrap-server', 'kafka:9092', '--consumer.config', '/etc/kafka/secrets/admin.properties', '--topic', topic, '--from-beginning', '--max-messages', '6', '--timeout-ms', '10000'));
      break;
    case 'certificate':
      requirePreparedMaterial();
      console.log(resolve(secretsDirectory, 'broker.crt'));
      break;
    default:
      console.error('Usage: node src/kafkaPublicCli.js <up|down|logs|publish|verify|certificate>');
      process.exitCode = 1;
  }
} catch (error) {
  console.error(`Public Kafka error: ${error.message}`);
  process.exitCode = 1;
}