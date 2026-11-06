// Explainable recommendations: a projected stockout calls for an operator decision, not automation.
// IDs are stable per stop so a decision remains attached when fresh impact is recomputed.
const round1 = (n) => Math.round(n * 10) / 10;

export function recommendActions(impact) {
  return (impact.lines ?? [])
    .filter((line) => ['high', 'critical'].includes(line.level) && line.shortfallHours > 0 && line.runOutAt)
    .map((line) => {
      const minutesToRecover = Math.ceil(line.shortfallHours * 60 + 15);
      return {
        actionId: `stop_${line.stop}`,
        type: 'expedite_delivery',
        level: line.level,
        title: `Expedite delivery to ${line.destination.name}`,
        target: { orderRef: line.orderRef, stop: line.stop, destination: line.destination, itemRef: line.itemRef },
        rationale: `${line.destination.name} is projected to be short ${line.unitsShort} ${line.unit} for ${round1(line.shortfallHours)} h before arrival.`,
        decisionDueAt: line.runOutAt,
        expectedOutcome: `Avoid the projected ${line.unitsShort} ${line.unit} shortfall if arrival improves by at least ${minutesToRecover} min.`,
        generatedAt: impact.computedAt,
      };
    });
}