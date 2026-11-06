import { COLL } from '../db.js';
import { invalidDocumentsFilter } from '../domain/schema.js';

// Snapshots older than this are recomputed when read; `fresh` reads recompute immediately.
export const KPI_MAX_AGE_MS = 15000;

const rate = (part, whole) => ({ $cond: [{ $gt: [whole, 0] }, { $round: [{ $multiply: [{ $divide: [part, whole] }, 100] }, 1] }, null] });
const countIf = (cond) => ({ $sum: { $cond: [cond, 1, 0] } });
const isLate = { $in: ['late', { $ifNull: ['$reliability.flags', []] }] };
const isOutOfOrder = { $eq: ['$reliability.status', 'out_of_order'] };
const dupes = { $sum: { $ifNull: ['$reliability.duplicateCount', 0] } };
const open = { $ne: ['$status', 'resolved'] };

/**
 * On-demand materialized view: one aggregation per profile computes the control tower KPIs from
 * the operational collections and writes them to kpi_snapshots with $merge (replace by _id).
 * Readers then fetch one small document instead of rerunning the aggregation.
 */
export function kpiPipeline(profile) {
  const tolMs = profile.risk.onTimeToleranceMin * 60000;
  return [
    { $match: { trackedEntityId: profile.entity.trackedEntityId, profile: profile.id } },
    {
      $lookup: {
        from: COLL.events,
        localField: 'trackedEntityId',
        foreignField: 'trackedEntityId',
        pipeline: [
          { $match: { milestoneId: { $type: 'string' }, eventTime: { $type: 'date' } } },
          { $group: { _id: '$milestoneId', actualTime: { $min: '$eventTime' } } },
        ],
        as: 'actuals',
      },
    },
    {
      $lookup: {
        from: COLL.events,
        localField: 'profile',
        foreignField: 'profile',
        pipeline: [
          {
            $facet: {
              totals: [{ $group: { _id: null, events: { $sum: 1 }, late: countIf(isLate), outOfOrder: countIf(isOutOfOrder), duplicates: dupes } }],
              bySource: [
                {
                  $group: {
                    _id: '$source',
                    events: { $sum: 1 },
                    late: countIf(isLate),
                    outOfOrder: countIf(isOutOfOrder),
                    clean: countIf({ $not: [{ $or: [isLate, isOutOfOrder] }] }),
                    duplicates: dupes,
                  },
                },
                { $sort: { _id: 1 } },
              ],
              invalid: [{ $match: invalidDocumentsFilter() }, { $count: 'n' }],
            },
          },
        ],
        as: 'ev',
      },
    },
    {
      $lookup: {
        from: COLL.exceptions,
        localField: 'profile',
        foreignField: 'profile',
        pipeline: [
          {
            $facet: {
              current: [
                { $match: { trackedEntityId: profile.entity.trackedEntityId } },
                {
                  $group: {
                    _id: null,
                    open: countIf(open),
                    slaBreached: countIf({ $and: [open, { $lt: ['$slaDueAt', '$$NOW'] }] }),
                    oldestOpenAt: { $min: { $cond: [open, '$createdAt', null] } },
                  },
                },
              ],
              // Includes the resolved history from earlier shipments.
              resolved: [
                { $match: { status: 'resolved', 'resolution.at': { $type: 'date' } } },
                {
                  $group: {
                    _id: null,
                    n: { $sum: 1 },
                    avgMs: { $avg: { $subtract: ['$resolution.at', '$createdAt'] } },
                    withinSla: countIf({ $lte: ['$resolution.at', '$slaDueAt'] }),
                  },
                },
              ],
              actions: [
                { $match: { trackedEntityId: profile.entity.trackedEntityId } },
                {
                  $group: {
                    _id: null,
                    recommended: { $sum: { $size: { $ifNull: ['$recommendedActions', []] } } },
                    decisionArrays: { $push: { $objectToArray: { $ifNull: ['$actionDecisions', {}] } } },
                  },
                },
                {
                  $project: {
                    recommended: 1,
                    decisions: {
                      $reduce: { input: '$decisionArrays', initialValue: [], in: { $concatArrays: ['$$value', '$$this'] } },
                    },
                  },
                },
                {
                  $project: {
                    recommended: 1,
                    decided: { $size: '$decisions' },
                    accepted: { $size: { $filter: { input: '$decisions', cond: { $eq: ['$$this.v.decision', 'accepted'] } } } },
                    declined: { $size: { $filter: { input: '$decisions', cond: { $eq: ['$$this.v.decision', 'declined'] } } } },
                  },
                },
              ],
            },
          },
        ],
        as: 'ex',
      },
    },
    {
      $set: {
        mm: {
          $map: {
            input: '$plannedMilestones',
            as: 'p',
            in: {
              $let: {
                vars: { a: { $first: { $filter: { input: '$actuals', cond: { $eq: ['$$this._id', '$$p.milestoneId'] } } } } },
                in: {
                  done: { $eq: [{ $type: '$$a.actualTime' }, 'date'] },
                  onTime: { $and: [{ $eq: [{ $type: '$$a.actualTime' }, 'date'] }, { $lte: [{ $subtract: ['$$a.actualTime', '$$p.plannedTime'] }, tolMs] }] },
                },
              },
            },
          },
        },
        t: { $ifNull: [{ $first: { $first: '$ev.totals' } }, { events: 0, late: 0, outOfOrder: 0, duplicates: 0 }] },
        cur: { $ifNull: [{ $first: { $first: '$ex.current' } }, { open: 0, slaBreached: 0, oldestOpenAt: null }] },
        res: { $ifNull: [{ $first: { $first: '$ex.resolved' } }, { n: 0, avgMs: null, withinSla: 0 }] },
        act: { $ifNull: [{ $first: { $first: '$ex.actions' } }, { recommended: 0, decided: 0, accepted: 0, declined: 0 }] },
      },
    },
    {
      $project: {
        _id: '$profile',
        profile: 1,
        trackedEntityId: 1,
        milestoneAdherence: {
          completed: { $size: { $filter: { input: '$mm', cond: '$$this.done' } } },
          onTime: { $size: { $filter: { input: '$mm', cond: '$$this.onTime' } } },
          onTimePct: rate({ $size: { $filter: { input: '$mm', cond: '$$this.onTime' } } }, { $size: { $filter: { input: '$mm', cond: '$$this.done' } } }),
        },
        eventQuality: {
          events: '$t.events',
          lateRatePct: rate('$t.late', '$t.events'),
          outOfOrderRatePct: rate('$t.outOfOrder', '$t.events'),
          // Share of all received messages that were duplicates (suppressed copies + stored events).
          duplicateRatePct: rate('$t.duplicates', { $add: ['$t.events', '$t.duplicates'] }),
          invalidRecords: { $ifNull: [{ $first: { $first: '$ev.invalid.n' } }, 0] },
        },
        sourceReliability: {
          $map: {
            input: { $ifNull: [{ $first: '$ev.bySource' }, []] },
            as: 's',
            in: {
              source: '$$s._id',
              events: '$$s.events',
              late: '$$s.late',
              outOfOrder: '$$s.outOfOrder',
              duplicates: '$$s.duplicates',
              // Clean, in-order, first-time deliveries as a share of all messages from the source.
              reliablePct: rate('$$s.clean', { $add: ['$$s.events', '$$s.duplicates'] }),
            },
          },
        },
        exceptions: {
          open: '$cur.open',
          slaBreached: '$cur.slaBreached',
          oldestOpenAgeMin: { $cond: ['$cur.oldestOpenAt', { $round: [{ $divide: [{ $subtract: ['$$NOW', '$cur.oldestOpenAt'] }, 60000] }, 0] }, null] },
          resolvedCount: '$res.n',
          mttrHours: { $cond: ['$res.avgMs', { $round: [{ $divide: ['$res.avgMs', 3600000] }, 1] }, null] },
          resolvedWithinSlaPct: rate('$res.withinSla', '$res.n'),
        },
        inventoryAtRisk: {
          level: { $ifNull: ['$businessImpact.level', 'none'] },
          linesAtRisk: { $ifNull: ['$businessImpact.linesAtRisk', 0] },
          lineCount: { $ifNull: ['$businessImpact.lineCount', 0] },
          unitsShort: { $ifNull: ['$businessImpact.unitsShort', 0] },
          unit: '$businessImpact.unit',
        },
        actionAdoption: {
          recommended: '$act.recommended',
          decided: '$act.decided',
          accepted: '$act.accepted',
          declined: '$act.declined',
          decisionRatePct: rate('$act.decided', '$act.recommended'),
          acceptanceRatePct: rate('$act.accepted', '$act.decided'),
        },
        computedAt: '$$NOW',
      },
    },
    { $merge: { into: COLL.kpis, on: '_id', whenMatched: 'replace', whenNotMatched: 'insert' } },
  ];
}

export async function refreshKpis(db, profile) {
  await db.collection(COLL.entities).aggregate(kpiPipeline(profile)).toArray();
  return db.collection(COLL.kpis).findOne({ _id: profile.id });
}

export async function kpiSnapshot(db, profile, { maxAgeMs = KPI_MAX_AGE_MS, now = new Date() } = {}) {
  const doc = await db.collection(COLL.kpis).findOne({ _id: profile.id });
  if (doc && now - doc.computedAt <= maxAgeMs) return { ...doc, cached: true };
  return { ...(await refreshKpis(db, profile)), cached: false };
}
