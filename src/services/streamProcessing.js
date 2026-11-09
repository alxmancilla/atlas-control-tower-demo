export const STREAM_PROCESSING = {
  processorName: 'control-tower-telemetry-rollups-v1',
  rollupsCollection: 'telemetry_rollups',
  allowedProfiles: ['retail', 'healthcare', 'manufacturing'],
};

const requireLabel = (name, value) => {
  if (typeof value !== 'string' || !/^[A-Za-z0-9._-]+$/.test(value)) throw new TypeError(`${name} must be a non-secret connection or topic label`);
  return value;
};

/** A reviewed ASP pipeline; deployment occurs separately in the Atlas Stream Processing UI/API. */
export function telemetryRollupProcessor({ database, kafkaConnection, topic, atlasConnection }) {
  const db = requireLabel('database', database);
  const source = requireLabel('kafkaConnection', kafkaConnection);
  const sourceTopic = requireLabel('topic', topic);
  const sink = requireLabel('atlasConnection', atlasConnection);
  return {
    name: STREAM_PROCESSING.processorName,
    pipeline: [
      { $source: {
        connectionName: source,
        topic: sourceTopic,
        timeField: { $dateFromString: { dateString: '$eventTime' } },
        partitionIdleTimeout: { size: 2, unit: 'minute' },
        config: { auto_offset_reset: 'latest', group_id: STREAM_PROCESSING.processorName },
      } },
      { $match: { profile: { $in: STREAM_PROCESSING.allowedProfiles }, value: { $type: 'number' }, eventTime: { $type: 'string' }, 'range.min': { $type: 'number' }, 'range.max': { $type: 'number' } } },
      { $set: { eventTime: { $dateFromString: { dateString: '$eventTime' } } } },
      { $tumblingWindow: {
        boundary: 'eventTime', interval: { size: 1, unit: 'minute' }, allowedLateness: { size: 5, unit: 'minute' }, idleTimeout: { size: 2, unit: 'minute' },
        pipeline: [
          { $group: { _id: { profile: '$profile', trackedEntityId: '$trackedEntityId', sensorId: '$sensorId', metric: '$metric', windowStart: { $meta: 'stream.window.start' } }, unit: { $first: '$unit' }, count: { $sum: 1 }, average: { $avg: '$value' }, minimum: { $min: '$value' }, maximum: { $max: '$value' }, outOfRangeCount: { $sum: { $cond: [{ $or: [{ $lt: ['$value', '$range.min'] }, { $gt: ['$value', '$range.max'] }] }, 1, 0] } } } },
          { $project: { _id: 0, profile: '$_id.profile', trackedEntityId: '$_id.trackedEntityId', sensorId: '$_id.sensorId', metric: '$_id.metric', windowStart: '$_id.windowStart', unit: 1, count: 1, average: { $round: ['$average', 2] }, minimum: 1, maximum: 1, outOfRangeCount: 1 } },
          { $set: { windowEnd: { $dateAdd: { startDate: '$windowStart', unit: 'minute', amount: 1 } }, status: { $cond: [{ $gt: ['$outOfRangeCount', 0] }, 'excursion', 'normal'] }, processedAt: '$$NOW' } },
        ],
      } },
      { $merge: { into: { connectionName: sink, db, coll: STREAM_PROCESSING.rollupsCollection }, on: ['profile', 'sensorId', 'metric', 'windowStart'], whenMatched: 'replace', whenNotMatched: 'insert' } },
    ],
  };
}

export function streamProcessorConfigFromEnv(env = process.env) {
  return telemetryRollupProcessor({
    database: env.DEMO_DB_NAME || 'atlas_control_tower_demo',
    kafkaConnection: env.ASP_KAFKA_CONNECTION,
    topic: env.ASP_TELEMETRY_TOPIC,
    atlasConnection: env.ASP_ATLAS_CONNECTION,
  });
}