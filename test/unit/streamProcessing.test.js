import { test } from 'node:test';
import assert from 'node:assert/strict';
import { STREAM_PROCESSING, telemetryRollupProcessor } from '../../src/services/streamProcessing.js';

test('telemetry rollup processor uses an event-time window and deterministic Atlas merge', () => {
  const processor = telemetryRollupProcessor({ database: 'atlas_control_tower_demo', kafkaConnection: 'controlTowerKafka', topic: 'telemetry-readings', atlasConnection: 'controlTowerAtlas' });
  assert.equal(processor.name, STREAM_PROCESSING.processorName);
  assert.equal(processor.pipeline[0].$source.connectionName, 'controlTowerKafka');
  assert.equal(processor.pipeline[0].$source.timeField.$dateFromString.dateString, '$eventTime');
  const window = processor.pipeline[3].$tumblingWindow;
  assert.deepEqual(window.interval, { size: 1, unit: 'minute' });
  assert.deepEqual(window.allowedLateness, { size: 5, unit: 'minute' });
  const merge = processor.pipeline.at(-1).$merge;
  assert.deepEqual(merge.on, ['profile', 'sensorId', 'metric', 'windowStart']);
  assert.equal(merge.into.coll, STREAM_PROCESSING.rollupsCollection);
  assert.equal(merge.whenMatched, 'replace');
});

test('telemetry rollup processor rejects unsafe or missing non-secret labels', () => {
  assert.throws(() => telemetryRollupProcessor({ database: 'demo name', kafkaConnection: 'kafka', topic: 'topic', atlasConnection: 'atlas' }), /database/);
  assert.throws(() => telemetryRollupProcessor({ database: 'atlas_control_tower_demo', kafkaConnection: '', topic: 'topic', atlasConnection: 'atlas' }), /kafkaConnection/);
});