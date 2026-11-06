// Business impact: does the shipment arrive before the destination runs out?
// A transparent rule, like milestone risk: cover = on hand / consumption rate, compared with the
// projected arrival of the final milestone.
const HOUR = 3600000;
const round1 = (n) => Math.round(n * 10) / 10;

export const IMPACT_LEVELS = ['none', 'watch', 'high', 'critical'];

const RANK = { none: 0, watch: 1, high: 2, critical: 3 };

/**
 * @param assessment       result of assessMilestones (needs milestones with plannedTime/projectedTime/actualTime)
 * @param position         { onHand, consumptionPerHour, unit } at the destination, or null
 * @param cfg              profile.impact
 * @param arrivalOffsetMin extra time after the final milestone for this stop on a multi-stop route
 * @param destinationName  fills {destination} in cfg.consequence
 */
export function assessImpact({ assessment, position, now, cfg, arrivalOffsetMin = 0, destinationName }) {
  const at = new Date(now);
  if (!position) return { level: 'none', reasons: ['No inventory position recorded for the destination'], computedAt: at };
  const final = assessment.milestones.at(-1);
  const consequence = cfg.consequence.replaceAll('{destination}', destinationName ?? 'the destination');
  const coverHours = position.consumptionPerHour > 0 ? position.onHand / position.consumptionPerHour : Infinity;
  const runOutAt = Number.isFinite(coverHours) ? new Date(at.getTime() + coverHours * HOUR) : null;
  const base = { onHand: position.onHand, unit: position.unit, coverHours: round1(coverHours), runOutAt, computedAt: at };

  if (final.actualTime) {
    return { ...base, level: 'none', arrivalAt: new Date(final.actualTime), etaHours: 0, shortfallHours: 0, unitsShort: 0, reasons: [`${final.label} completed; supply restored`] };
  }
  const arrivalAt = new Date(new Date(final.projectedTime ?? final.plannedTime).getTime() + arrivalOffsetMin * 60000);
  const etaHours = Math.max(0, (arrivalAt - at) / HOUR);
  const shortfallHours = Math.max(0, etaHours - coverHours);
  const marginHours = coverHours - etaHours;
  let level = 'none';
  if (shortfallHours >= cfg.criticalShortfallHours) level = 'critical';
  else if (shortfallHours > 0) level = 'high';
  else if (marginHours < cfg.watchMarginHours) level = 'watch';

  const arrival = destinationName ? `Arrival at ${destinationName} projected in ${round1(etaHours)} h` : `${final.label} projected in ${round1(etaHours)} h`;
  const reasons = [`${round1(coverHours)} h of cover (${position.onHand} ${position.unit} at ${position.consumptionPerHour}/h)`, arrival];
  if (shortfallHours > 0) reasons.push(`${round1(shortfallHours)} h short: ${consequence}`);
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

/**
 * One load, many stops: each order line is assessed against its own destination stock and its own
 * arrival (final milestone + stopOffsetMin). The load's level is the worst stop.
 * @param lines      embedded order lines ({ orderRef, stop, stopOffsetMin, destination, itemRef, quantity, unit })
 * @param positions  inventory_positions joined for those lines ({ locationId, itemRef, onHand, consumptionPerHour, unit })
 */
export function assessLoadImpact({ assessment, lines, positions, now, cfg }) {
  const at = new Date(now);
  const perLine = lines.map((l) => {
    const position = positions.find((p) => p.locationId === l.destination.id && p.itemRef === l.itemRef) ?? null;
    const impact = assessImpact({ assessment, position, now: at, cfg, arrivalOffsetMin: l.stopOffsetMin ?? 0, destinationName: l.destination.name });
    return { orderRef: l.orderRef, stop: l.stop, destination: l.destination, itemRef: l.itemRef, quantity: l.quantity, ...impact, unit: impact.unit ?? l.unit };
  });
  if (!perLine.length) return { level: 'none', reasons: ['No order lines on this load'], lines: [], lineCount: 0, linesAtRisk: 0, computedAt: at };
  const worst = perLine.reduce((a, b) => (RANK[b.level] > RANK[a.level] ? b : a));
  const atRisk = perLine.filter((l) => l.level === 'high' || l.level === 'critical');
  const watch = perLine.filter((l) => l.level === 'watch');
  const units = [...new Set(perLine.map((l) => l.unit))];
  const unit = units.length === 1 ? units[0] : 'units';
  const unitsShort = atRisk.reduce((n, l) => n + (l.unitsShort ?? 0), 0);
  const n = perLine.length;
  const final = assessment.milestones.at(-1);

  let reasons;
  if (final.actualTime) reasons = [`${final.label} completed; supply restored at all ${n} stops`];
  else if (atRisk.length) {
    reasons = [
      `${atRisk.length} of ${n} stops short: ${atRisk.map((l) => l.destination.name).join(', ')}`,
      `${unitsShort} ${unit} short before the load arrives`,
      ...worst.reasons.slice(1).map((r) => `Worst stop, ${worst.destination.name}: ${r}`),
    ];
  } else if (watch.length) reasons = [`${watch.length} of ${n} stops with thin margin: ${watch.map((l) => l.destination.name).join(', ')}`];
  else reasons = [`All ${n} stops have cover until the load arrives`];

  return {
    level: worst.level,
    shortfallHours: Math.max(...perLine.map((l) => l.shortfallHours ?? 0)),
    unitsShort,
    unit,
    lineCount: n,
    linesAtRisk: atRisk.length,
    lines: perLine,
    reasons,
    computedAt: at,
  };
}

// Fields copied onto open exceptions so the queue can rank by impact without a join.
export const impactSummary = (impact) => ({
  level: impact.level,
  shortfallHours: impact.shortfallHours ?? 0,
  unitsShort: impact.unitsShort ?? 0,
  unit: impact.unit ?? null,
  linesAtRisk: impact.linesAtRisk ?? 0,
  lineCount: impact.lineCount ?? 0,
  computedAt: impact.computedAt,
});
