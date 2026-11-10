const text = (value) => (typeof value === 'string' && value.trim() ? value.trim() : null);

// A bounded evidence record for normalized partner signals. This validates only the
// canonical fields this demo owns; it does not transform or write back raw partner data.
export function assessSourceContract(profile, event) {
  const spec = profile.sourceContracts?.[event.source];
  if (!spec) return null;
  const required = spec.requiredCorrelationKeys ?? [];
  const correlation = Object.fromEntries(required.map((key) => [key, text(event.details?.[key])]).filter(([, value]) => value));
  const missingCorrelationKeys = required.filter((key) => !correlation[key]);
  const lagMinutes = event.reliability?.lagMinutes ?? null;
  const freshnessStatus = lagMinutes == null ? 'unknown' : lagMinutes > spec.freshnessTargetMin ? 'stale' : lagMinutes < -5 ? 'future_dated' : 'fresh';
  return {
    contractId: `${profile.id}:${event.source}:v${spec.version}`,
    version: spec.version,
    mappingStatus: missingCorrelationKeys.length ? 'incomplete' : 'mapped',
    requiredCorrelationKeys: required,
    correlation,
    missingCorrelationKeys,
    freshness: { targetMinutes: spec.freshnessTargetMin, observedLagMinutes: lagMinutes, status: freshnessStatus },
  };
}