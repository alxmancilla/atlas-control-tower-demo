// Rules-based milestone risk. This is a transparent estimate from explicit profile thresholds,
// not a predictive model.
export const minutesBetween = (later, earlier) => Math.round((new Date(later) - new Date(earlier)) / 60000);

/**
 * @param plan  milestones in plan order: { id, label, plannedTime, maxDwellMin, actualTime|null, ... }
 * @param now   evaluation time
 * @param risk  profile.risk thresholds
 */
export function assessMilestones(plan, now, risk) {
  const tol = risk.onTimeToleranceMin;
  const lastCompletedIdx = plan.reduce((acc, m, i) => (m.actualTime ? i : acc), -1);
  let carryLateMin = 0;
  let prevActual = null;
  let prevLabel = null;
  let dwellChecked = false;

  const milestones = plan.map((m, i) => {
    const planned = new Date(m.plannedTime);
    const r = { ...m, varianceMin: null, projectedTime: null, status: 'pending', reasons: [] };

    if (m.actualTime) {
      const v = minutesBetween(m.actualTime, planned);
      r.varianceMin = v;
      if (v > tol) {
        r.status = 'completed_late';
        r.reasons.push(`Completed ${v} min after plan (tolerance ${tol} min)`);
      } else {
        r.status = 'completed';
        r.reasons.push(v < 0 ? `Completed ${-v} min ahead of plan` : `Completed within tolerance (+${v} min)`);
      }
      carryLateMin = Math.max(0, v);
      prevActual = new Date(m.actualTime);
      prevLabel = m.label;
      return r;
    }

    const overdueMin = minutesBetween(now, planned);
    r.projectedTime = new Date(planned.getTime() + carryLateMin * 60000);

    if (i < lastCompletedIdx) {
      r.status = 'missed';
      r.reasons.push('A later milestone is complete but no event was received for this one');
    } else if (overdueMin > risk.missedGraceMin) {
      r.status = 'missed';
      r.reasons.push(`No event ${overdueMin} min after planned time (grace ${risk.missedGraceMin} min)`);
    } else {
      if (overdueMin >= 0) {
        r.status = 'at_risk';
        r.reasons.push(`Planned time passed ${overdueMin} min ago; inside ${risk.missedGraceMin} min grace`);
      } else if (-overdueMin <= risk.atRiskWindowMin) {
        r.status = 'at_risk';
        r.reasons.push(`Due in ${-overdueMin} min and no event received yet`);
      }
      if (carryLateMin > tol) {
        r.status = 'at_risk';
        r.reasons.push(`Projected ${carryLateMin} min late: upstream lateness carried forward`);
      }
    }

    if (!dwellChecked && prevActual && m.maxDwellMin) {
      dwellChecked = true;
      const dwell = minutesBetween(now, prevActual);
      if (dwell > m.maxDwellMin) {
        if (r.status === 'pending') r.status = 'at_risk';
        r.reasons.push(`Dwell since "${prevLabel}" is ${dwell} min (limit ${m.maxDwellMin} min)`);
      }
    }

    if (overdueMin > 0) carryLateMin = Math.max(carryLateMin, overdueMin);
    if (r.status === 'pending') r.reasons.push('On plan');
    return r;
  });

  const current = lastCompletedIdx >= 0 ? milestones[lastCompletedIdx] : null;
  const next = milestones.find((m, i) => !m.actualTime && i > lastCompletedIdx) || null;
  const final = milestones[milestones.length - 1];
  const projectedFinalLateMin = final.actualTime
    ? Math.max(0, final.varianceMin)
    : Math.max(0, minutesBetween(final.projectedTime, final.plannedTime));
  const missed = milestones.filter((m) => m.status === 'missed').length;
  const atRisk = milestones.filter((m) => m.status === 'at_risk').length;
  const riskLevel = missed > 0 ? 'high' : atRisk > 0 || projectedFinalLateMin > tol ? 'medium' : 'low';

  return {
    milestones,
    currentStatus: current ? { milestoneId: current.id, label: current.label, actualTime: current.actualTime } : null,
    nextMilestone: next ? { milestoneId: next.id, label: next.label, plannedTime: next.plannedTime, status: next.status } : null,
    completedCount: milestones.filter((m) => m.actualTime).length,
    missedCount: missed,
    atRiskCount: atRisk,
    projectedFinalLateMin,
    riskLevel,
  };
}
