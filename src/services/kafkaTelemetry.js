import { PROFILE_IDS, getProfile } from '../profiles/index.js';

export const LOCAL_KAFKA = {
  composeFile: 'docker-compose.kafka.yml',
  topic: 'control-tower-telemetry',
};

export function kafkaTopicFromEnv(env = process.env) {
  const topic = env.KAFKA_TELEMETRY_TOPIC || LOCAL_KAFKA.topic;
  if (typeof topic !== 'string' || !/^[A-Za-z0-9._-]+$/.test(topic)) throw new TypeError('KAFKA_TELEMETRY_TOPIC must be a Kafka topic name');
  return topic;
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