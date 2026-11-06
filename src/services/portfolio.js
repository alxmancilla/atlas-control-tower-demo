import { COLL } from '../db.js';

const IMPACT_RANK = {
  $switch: {
    branches: [
      { case: { $eq: ['$businessImpact.level', 'critical'] }, then: 4 },
      { case: { $eq: ['$businessImpact.level', 'high'] }, then: 3 },
      { case: { $eq: ['$businessImpact.level', 'watch'] }, then: 2 },
    ],
    default: 1,
  },
};

// Portfolio list: a single aggregation starts from all active entities for a profile, then joins
// each entity's nearest open exception. The primary entity remains the interactive drill-down.
export async function portfolioOverview(db, profile) {
  const loads = await db
    .collection(COLL.entities)
    .aggregate([
      { $match: { profile: profile.id } },
      {
        $lookup: {
          from: COLL.exceptions,
          let: { entityId: '$trackedEntityId' },
          pipeline: [
            { $match: { $expr: { $and: [{ $eq: ['$trackedEntityId', '$$entityId'] }, { $ne: ['$status', 'resolved'] }] } } },
            { $sort: { slaDueAt: 1 } },
            { $project: { _id: 0, exceptionId: 1, categoryLabel: 1, severity: 1, owner: 1, slaDueAt: 1, summary: 1 } },
          ],
          as: 'openExceptions',
        },
      },
      {
        $set: {
          impactRank: IMPACT_RANK,
          openExceptionCount: { $size: '$openExceptions' },
          nearestException: { $first: '$openExceptions' },
        },
      },
      { $sort: { impactRank: -1, openExceptionCount: -1, 'nearestException.slaDueAt': 1, trackedEntityId: 1 } },
      {
        $project: {
          _id: 0,
          trackedEntityId: 1,
          product: 1,
          origin: 1,
          destination: 1,
          businessImpact: 1,
          openExceptionCount: 1,
          nearestException: 1,
          state: { $ifNull: ['$portfolio.state', 'interactive_primary'] },
          lastTrustedSignal: '$portfolio.lastTrustedSignal',
          interactive: { $eq: ['$trackedEntityId', profile.entity.trackedEntityId] },
        },
      },
    ])
    .toArray();
  const byImpact = (level) => loads.filter((load) => load.businessImpact?.level === level).length;
  return {
    loads,
    summary: {
      total: loads.length,
      critical: byImpact('critical'),
      high: byImpact('high'),
      watch: byImpact('watch'),
      openExceptions: loads.reduce((n, load) => n + load.openExceptionCount, 0),
    },
  };
}