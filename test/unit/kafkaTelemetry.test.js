import { test } from 'node:test';
import assert from 'node:assert/strict';
import { kafkaTopicFromEnv, LOCAL_KAFKA, PUBLIC_KAFKA, publicKafkaIpFromEnv, telemetryMessages } from '../../src/services/kafkaTelemetry.js';

test('Kafka telemetry fixtures satisfy the ASP source contract for every profile', () => {
  const messages = telemetryMessages({ now: new Date('2026-01-01T12:00:00Z') });
  assert.equal(messages.length, 6);
  assert.deepEqual(messages.map((message) => message.profile), ['retail', 'retail', 'healthcare', 'healthcare', 'manufacturing', 'manufacturing']);
  for (const message of messages) {
    assert.match(message.eventId, /^demo-/);
    assert.match(message.eventTime, /^2026-01-01T12:00:/);
    assert.equal(typeof message.value, 'number');
    assert.equal(typeof message.range.min, 'number');
    assert.equal(typeof message.range.max, 'number');
  }
  assert.equal(messages[1].value > messages[1].range.max, true);
});

test('Kafka topic has a safe default and rejects unsafe values', () => {
  assert.equal(kafkaTopicFromEnv({}), LOCAL_KAFKA.topic);
  assert.equal(kafkaTopicFromEnv({ KAFKA_TELEMETRY_TOPIC: 'control.tower-v2' }), 'control.tower-v2');
  assert.throws(() => kafkaTopicFromEnv({ KAFKA_TELEMETRY_TOPIC: 'invalid topic' }), /topic name/);
});

test('public Kafka profile accepts only a routable IPv4 advertised listener', () => {
  assert.equal(PUBLIC_KAFKA.port, 9094);
  assert.equal(publicKafkaIpFromEnv({ KAFKA_PUBLIC_IP: '8.8.8.8' }), '8.8.8.8');
  for (const value of [undefined, 'localhost', '127.0.0.1', '10.0.0.8', '192.168.1.8', '172.16.0.8', '224.0.0.1']) {
    assert.throws(() => publicKafkaIpFromEnv({ KAFKA_PUBLIC_IP: value }), /public IPv4/);
  }
});