import { COLL } from '../db.js';
import { assessMilestones } from '../domain/milestoneRisk.js';

/**
 * Aggregation that joins the planned milestones with the earliest valid event per milestone.
 * Using the earliest eventTime per milestoneId means a late or re-sent report never moves
 * the timeline backward; status is derived from milestone sequence, not arrival order.
 */
export function entityPipeline(profile) {
  return [
    { $match: { trackedEntityId: profile.entity.trackedEntityId, profile: profile.id } },
    {
      $lookup: {
        from: COLL.events,
        localField: 'trackedEntityId',
        foreignField: 'trackedEntityId',
        pipeline: [
          { $match: { milestoneId: { $type: 'string' }, eventTime: { $type: 'date' } } },
          { $sort: { eventTime: 1 } },
          {
            $group: {
              _id: '$milestoneId',
              actualTime: { $first: '$eventTime' },
              actualSource: { $first: '$source' },
              actualEventId: { $first: '$_id' },
              reports: { $sum: 1 },
            },
          },
        ],
        as: 'actuals',
      },
    },
    {
      $lookup: {
        from: COLL.exceptions,
        localField: 'trackedEntityId',
        foreignField: 'trackedEntityId',
        pipeline: [{ $match: { status: { $ne: 'resolved' } } }, { $group: { _id: '$severity', n: { $sum: 1 } } }],
        as: 'openExceptions',
      },
    },
    {
      $set: {
        milestones: {
          $map: {
            input: { $sortArray: { input: '$plannedMilestones', sortBy: { sequence: 1 } } },
            as: 'p',
            in: {
              $let: {
                vars: { a: { $first: { $filter: { input: '$actuals', cond: { $eq: ['$$this._id', '$$p.milestoneId'] } } } } },
                in: {
                  id: '$$p.milestoneId',
                  label: '$$p.label',
                  sequence: '$$p.sequence',
                  source: '$$p.source',
                  plannedTime: '$$p.plannedTime',
                  maxDwellMin: '$$p.maxDwellMin',
                  actualTime: { $ifNull: ['$$a.actualTime', null] },
                  actualSource: { $ifNull: ['$$a.actualSource', null] },
                  actualEventId: { $ifNull: [{ $toString: '$$a.actualEventId' }, null] },
                  reports: { $ifNull: ['$$a.reports', 0] },
                },
              },
            },
          },
        },
        openExceptionCount: { $sum: '$openExceptions.n' },
      },
    },
    {
      $set: {
        latestReliableMilestone: { $last: { $filter: { input: '$milestones', cond: { $ne: ['$$this.actualTime', null] } } } },
      },
    },
    { $project: { actuals: 0, plannedMilestones: 0 } },
  ];
}

export async function entityOverview(db, profile, now = new Date()) {
  const [doc] = await db.collection(COLL.entities).aggregate(entityPipeline(profile)).toArray();
  if (!doc) return null;
  const assessment = assessMilestones(doc.milestones, now, profile.risk);
  return {
    entity: {
      trackedEntityId: doc.trackedEntityId,
      entityKind: doc.entityKind,
      product: doc.product,
      origin: doc.origin,
      destination: doc.destination,
      metadata: doc.metadata,
      anchorAt: doc.anchorAt,
    },
    latestReliableMilestone: doc.latestReliableMilestone ?? null,
    openExceptionCount: doc.openExceptionCount,
    openExceptionsBySeverity: Object.fromEntries(doc.openExceptions.map((x) => [x._id, x.n])),
    assessment,
    evaluatedAt: now,
  };
}
