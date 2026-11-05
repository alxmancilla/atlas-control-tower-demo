// Business impact: does the shipment arrive before the destination runs out?
// A transparent rule, like milestone risk: cover = on hand / consumption rate, compared with the
// projected arrival of the final milestone.
const HOUR = 3600000;
const round1 = (n) => Math.round(n * 10) / 10;

export const IMPACT_LEVELS = ['none', 'watch', 'high', 'critical'];

/**
 * @param assessment  result of assessMilestones (needs milestones with plannedTime/projectedTime/actualTime)
 * @param position    { onHand, consumptionPerHour, unit } at the destination, or null
 * @param cfg         profile.impact
 */
export function assessImpact({ assessment, position, now, cfg }) {
  const at = new Date(now);
  if (!position) return { level: 'none', reasons: ['No inventory position recorded for the destination'], computedAt: at };
  const final = assessment.milestones.at(-1);
  const coverHours = position.consumptionPerHour > 0 ? position.onHand / position.consumptionPerHour : Infinity;
  const runOutAt = Number.isFinite(coverHours) ? new Date(at.getTime() + coverHours * HOUR) : null;
  const base = { onHand: position.onHand, unit: position.unit, coverHours: round1(coverHours), runOutAt, computedAt: at };

  if (final.actualTime) {
    return { ...base, level: 'none', arrivalAt: new Date(final.actualTime), etaHours: 0, shortfallHours: 0, unitsShort: 0, reasons: [`${final.label} completed; supply restored`] };
  }
  const arrivalAt = new Date(final.projectedTime ?? final.plannedTime);
  const etaHours = Math.max(0, (arrivalAt - at) / HOUR);
  const shortfallHours = Math.max(0, etaHours - coverHours);
  const marginHours = coverHours - etaHours;
  let level = 'none';
  if (shortfallHours >= cfg.criticalShortfallHours) level = 'critical';
  else if (shortfallHours > 0) level = 'high';
  else if (marginHours < cfg.watchMarginHours) level = 'watch';

  const reasons = [`${round1(coverHours)} h of cover (${position.onHand} ${position.unit} at ${position.consumptionPerHour}/h)`, `${final.label} projected in ${round1(etaHours)} h`];
  if (shortfallHours > 0) reasons.push(`${round1(shortfallHours)} h short: ${cfg.consequence}`);
  else if (level === 'watch') reasons.push(`Only ${round1(marginHours)} h of margin (watch below ${cfg.watchMarginHours} h)`);
  else reasons.push(`${round1(marginHours)} h of margin`);
  return {
    ...base,
    level,
    arrivalAt,
    etaHours: round1(etaHours),
    shortfallHours: round1(shortfallHours),
    unitsShort: Math.ceil(shortfallHours * position.consumptionPerHour),
    reasons,
  };
}

// Fields copied onto open exceptions so the queue can rank by impact without a join.
export const impactSummary = (impact) => ({
  level: impact.level,
  shortfallHours: impact.shortfallHours ?? 0,
  unitsShort: impact.unitsShort ?? 0,
  unit: impact.unit ?? null,
  computedAt: impact.computedAt,
});
