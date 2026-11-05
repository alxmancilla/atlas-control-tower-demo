import { COLL } from '../db.js';

// Telemetry lives in a time-series collection. Time-series collections do not support
// change streams or MongoDB Search, so the UI queries this view on demand.

const reading = (profile, ts, value) => ({
  ts,
  meta: {
    trackedEntityId: profile.entity.trackedEntityId,
    sensorId: profile.telemetry.sensorId,
    profile: profile.id,
    metric: profile.telemetry.metric,
  },
  value: Math.round(value * 10) / 10,
  unit: profile.telemetry.unit,
});

// Deterministic synthetic series: baseline with a gentle wave and two out-of-range readings.
export function syntheticSeries(profile, now = new Date(), points = 36, stepMin = 10) {
  const t = profile.telemetry;
  const span = (t.max - t.min) / 6;
  return Array.from({ length: points }, (_, i) => {
    const ts = new Date(now.getTime() - (points - i) * stepMin * 60000);
    const excursion = i === 20 || i === 21;
    return reading(profile, ts, excursion ? t.excursionValue : t.baseline + Math.sin(i / 3) * span);
  });
}

export async function seedTelemetry(db, profile, now = new Date()) {
  if (!profile.telemetry?.enabled) return { inserted: 0, skipped: 'telemetry not used by this profile' };
  const coll = db.collection(COLL.telemetry);
  const existing = await coll.countDocuments({ 'meta.trackedEntityId': profile.entity.trackedEntityId }, { limit: 1 });
  if (existing) return { inserted: 0, skipped: 'readings already present' };
  const res = await coll.insertMany(syntheticSeries(profile, now));
  return { inserted: res.insertedCount };
}

export async function addReading(db, profile, kind = 'normal', now = new Date()) {
  const t = profile.telemetry;
  if (!t?.enabled) throw new Error(`Profile ${profile.id} does not use telemetry`);
  const value = kind === 'excursion' ? t.excursionValue : t.baseline + (Math.random() - 0.5) * ((t.max - t.min) / 4);
  const doc = reading(profile, now, value);
  await db.collection(COLL.telemetry).insertOne(doc);
  return doc;
}

export function telemetryPipeline(profile, { since, min, max }) {
  const outOfRange = { $or: [{ $lt: ['$value', min] }, { $gt: ['$value', max] }] };
  return [
    { $match: { 'meta.trackedEntityId': profile.entity.trackedEntityId, 'meta.metric': profile.telemetry.metric, ts: { $gte: since } } },
    { $sort: { ts: 1 } },
    {
      $setWindowFields: {
        partitionBy: '$meta.sensorId',
        sortBy: { ts: 1 },
        output: { movingAvg: { $avg: '$value', window: { documents: [-5, 0] } } },
      },
    },
    { $set: { outOfRange } },
    {
      $facet: {
        recent: [
          { $sort: { ts: -1 } },
          { $limit: 48 },
          { $project: { _id: 0, ts: 1, value: 1, unit: 1, sensorId: '$meta.sensorId', outOfRange: 1, movingAvg: { $round: ['$movingAvg', 2] } } },
        ],
        stats: [
          {
            $group: {
              _id: null,
              count: { $sum: 1 },
              min: { $min: '$value' },
              max: { $max: '$value' },
              avg: { $avg: '$value' },
              last: { $last: '$value' },
              outOfRange: { $sum: { $cond: ['$outOfRange', 1, 0] } },
            },
          },
          { $project: { _id: 0, count: 1, min: 1, max: 1, last: 1, outOfRange: 1, avg: { $round: ['$avg', 2] } } },
        ],
        excursions: [
          { $match: { outOfRange: true } },
          { $sort: { ts: -1 } },
          { $limit: 20 },
          {
            $project: {
              _id: 0,
              ts: 1,
              value: 1,
              deviation: { $round: [{ $cond: [{ $gt: ['$value', max] }, { $subtract: ['$value', max] }, { $subtract: ['$value', min] }] }, 2] },
            },
          },
        ],
      },
    },
  ];
}

export async function telemetryView(db, profile, { hours = 8, min, max } = {}, now = new Date()) {
  const t = profile.telemetry;
  if (!t?.enabled) return { enabled: false, note: 'This profile does not use telemetry; the capability is optional here.' };
  const range = { min: Number.isFinite(min) ? min : t.min, max: Number.isFinite(max) ? max : t.max };
  if (!(range.min < range.max)) throw new Error('Telemetry range min must be below max');
  const since = new Date(now.getTime() - hours * 3600000);
  const [res] = await db.collection(COLL.telemetry).aggregate(telemetryPipeline(profile, { since, ...range })).toArray();
  return {
    enabled: true,
    metric: t.metric,
    label: t.label,
    unit: t.unit,
    sensorId: t.sensorId,
    range,
    configuredRange: { min: t.min, max: t.max },
    since,
    stats: res.stats[0] ?? { count: 0 },
    recent: res.recent,
    excursions: res.excursions,
    queriedAt: now,
  };
}
