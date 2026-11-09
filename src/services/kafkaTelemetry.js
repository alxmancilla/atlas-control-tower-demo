import { PROFILE_IDS, getProfile } from '../profiles/index.js';

export const LOCAL_KAFKA = {
  composeFile: 'docker-compose.kafka.yml',
  topic: 'control-tower-telemetry',
};

export const PUBLIC_KAFKA = {
  composeFile: 'docker-compose.kafka.public.yml',
  port: 9094,
  secretsDirectory: '.local/kafka-public',
  aspConsumerGroup: 'control-tower-telemetry-rollups-v1',
};

export function kafkaTopicFromEnv(env = process.env) {
  const topic = env.KAFKA_TELEMETRY_TOPIC || LOCAL_KAFKA.topic;
  if (typeof topic !== 'string' || !/^[A-Za-z0-9._-]+$/.test(topic)) throw new TypeError('KAFKA_TELEMETRY_TOPIC must be a Kafka topic name');
  return topic;
}

export function publicKafkaIpFromEnv(env = process.env) {
  const value = env.KAFKA_PUBLIC_IP;
  if (typeof value !== 'string') throw new TypeError('KAFKA_PUBLIC_IP must be a public IPv4 address');
  const octets = value.split('.').map(Number);
  const isIpv4 = octets.length === 4 && octets.every((octet) => Number.isInteger(octet) && octet >= 0 && octet <= 255);
  const [first, second] = octets;
  const isPrivateOrSpecial = first === 0 || first === 10 || first === 127 || first >= 224 || (first === 169 && second === 254) || (first === 172 && second >= 16 && second <= 31) || (first === 192 && second === 168);
  if (!isIpv4 || isPrivateOrSpecial) throw new TypeError('KAFKA_PUBLIC_IP must be a public IPv4 address');
  return value;
}

export function telemetryMessages({ now = new Date(), profileIds = PROFILE_IDS } = {}) {
  if (!(now instanceof Date) || Number.isNaN(now.valueOf())) throw new TypeError('now must be a valid Date');
  return profileIds.flatMap((profileId, index) => {
    const profile = getProfile(profileId);
    const telemetry = profile.telemetry;
    if (!telemetry?.enabled) return [];
    const eventTime = new Date(now.valueOf() + index * 1000).toISOString();
    const base = {
      eventTime,
      profile: profile.id,
      trackedEntityId: profile.entity.trackedEntityId,
      sensorId: telemetry.sensorId,
      metric: telemetry.metric,
      unit: telemetry.unit,
      range: { min: telemetry.min, max: telemetry.max },
    };
    return [
      { ...base, eventId: `demo-${profile.id}-normal-${eventTime}`, value: telemetry.baseline },
      { ...base, eventId: `demo-${profile.id}-excursion-${eventTime}`, value: telemetry.excursionValue },
    ];
  });
}